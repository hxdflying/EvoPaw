"""FeishuListener — 维护飞书 WebSocket 长连接，将事件转换为 InboundMessage.

当前版本处理文本消息和 post 富文本消息，其它类型保留空 content，交由上游统一回复"收到"。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from lark_oapi.client import LogLevel
from lark_oapi.ws import Client as WSClient
from lark_oapi.ws.client import EventDispatcherHandler

from xiaopaw.feishu.session_key import resolve_routing_key
from xiaopaw.models import Attachment, InboundMessage
from xiaopaw.observability.metrics import record_feishu_event, record_inbound_message

logger = logging.getLogger(__name__)


OnMessageFn = Callable[[InboundMessage], Awaitable[None]]
OnBotAddedFn = Callable[[str, str], Awaitable[None]]


class _XiaoPawEventHandler(EventDispatcherHandler):
    """自定义事件处理器：拦截 im.message.receive_v1 和 im.chat.member.bot.added_v1，并转发给 Runner."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_message: OnMessageFn,
        on_bot_added: OnBotAddedFn | None = None,
        allowed_chats: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._loop = loop
        self._on_message = on_message
        self._on_bot_added = on_bot_added
        self._allowed_chats = allowed_chats  # None 或 [] 表示允许所有

    def _is_chat_allowed(self, chat_id: str, chat_type: str) -> bool:
        """检查 chat_id 是否在白名单中。p2p 消息始终允许。"""
        if chat_type == "p2p":
            return True
        if not self._allowed_chats:  # None 或空列表
            return True
        return chat_id in self._allowed_chats

    def do_without_validation(self, payload: bytes) -> None:  # type: ignore[override]
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            logger.exception("Failed to decode websocket payload")
            return

        header = data.get("header") or {}
        event = data.get("event") or {}
        event_type = header.get("event_type") or event.get("type")

        try:
            # 记录所有 Feishu 事件类型
            event_obj = data.get("event") or {}
            message = event_obj.get("message") or {}
            chat_type = message.get("chat_type") or ""
            record_feishu_event(event_type or "unknown", chat_type)

            # ── 处理 Bot 入群事件 ──────────────────────────────────────────
            if event_type == "im.chat.member.bot.added_v1":
                chat_id = event_obj.get("chat_id") or ""
                group_name = event_obj.get("name") or ""
                if not self._is_chat_allowed(chat_id, "group"):
                    return
                if self._on_bot_added is not None:
                    asyncio.run_coroutine_threadsafe(
                        self._on_bot_added(chat_id, group_name), self._loop
                    )
                return

            if event_type != "im.message.receive_v1":
                # 其它事件暂不处理
                return

            sender = event_obj.get("sender") or {}
            sender_ids = sender.get("sender_id") or {}
            sender_open_id = sender_ids.get("open_id") or ""

            chat_type = message.get("chat_type") or ""
            chat_id = message.get("chat_id") or ""
            thread_id = message.get("thread_id")

            # ── allowed_chats 白名单检查 ────────────────────────────────────
            if not self._is_chat_allowed(chat_id, chat_type):
                return

            routing_key = resolve_routing_key(
                chat_type=chat_type,
                sender_id=sender_open_id,
                chat_id=chat_id,
                thread_id=thread_id,
            )

            content = FeishuListener._extract_content(
                message.get("message_type") or "",
                message.get("content") or "",
            )

            attachment = FeishuListener._extract_attachment(
                message.get("message_type") or "",
                message.get("content") or "",
            )

            msg_id = message.get("message_id") or ""
            root_id = message.get("root_id") or msg_id
            ts_str = message.get("create_time") or "0"
            try:
                ts = int(ts_str)
            except ValueError:
                ts = 0

            inbound = InboundMessage(
                routing_key=routing_key,
                content=content,
                msg_id=msg_id,
                root_id=root_id,
                sender_id=sender_open_id,
                ts=ts,
                attachment=attachment,
            )

            # 记录 InboundMessage metrics
            record_inbound_message(routing_key, has_attachment=attachment is not None)

            # 在主事件循环中调度 Runner.dispatch
            asyncio.run_coroutine_threadsafe(self._on_message(inbound), self._loop)
        except Exception:
            logger.exception("Failed to handle im.message.receive_v1 websocket event")


class FeishuListener:
    """飞书 WebSocket 监听器.

    负责:
    - 建立 WebSocket 长连接
    - 订阅 IM_MESSAGE_RECEIVE_V1 事件
    - 将事件映射为 InboundMessage 并交给上游处理
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        on_message: OnMessageFn,
        loop: asyncio.AbstractEventLoop,
        log_level: LogLevel = LogLevel.INFO,
        on_bot_added: OnBotAddedFn | None = None,
        allowed_chats: list[str] | None = None,
    ) -> None:
        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=on_message,
            on_bot_added=on_bot_added,
            allowed_chats=allowed_chats,
        )
        self._ws_client = WSClient(
            app_id=app_id,
            app_secret=app_secret,
            log_level=log_level,
            event_handler=handler,
        )

    async def start(self) -> None:
        """启动监听（在独立线程中运行 lark-oapi 的事件循环）。"""
        logger.info("FeishuListener starting WebSocket client...")
        loop = asyncio.get_running_loop()
        # lark-oapi ws.Client.start() 为阻塞同步方法，内部自管事件循环，
        # 这里通过线程池隔离，避免嵌套事件循环错误。
        await loop.run_in_executor(None, self._ws_client.start)

    @staticmethod
    def _extract_attachment(msg_type: str, content_json: str) -> Attachment | None:
        """从 content JSON 中提取附件元信息（仅 image / file 类型）."""
        if msg_type not in ("image", "file"):
            return None
        if not content_json:
            return None
        try:
            data = json.loads(content_json)
        except json.JSONDecodeError:
            return None

        if msg_type == "image":
            image_key = data.get("image_key") or ""
            if not image_key:
                return None
            return Attachment(
                msg_type="image",
                file_key=image_key,
                file_name=f"{image_key}.jpg",
            )

        if msg_type == "file":
            file_key = data.get("file_key") or ""
            if not file_key:
                return None
            file_name = data.get("file_name") or file_key
            return Attachment(
                msg_type="file",
                file_key=file_key,
                file_name=file_name,
            )

        return None  # pragma: no cover

    @staticmethod
    def _extract_post_text(data: dict) -> str:
        """从 post 消息的 content dict 中提取纯文本。

        飞书 post 消息结构::

            {
              "zh_cn": {
                "title": "标题（可选）",
                "content": [
                  [{"tag": "text", "text": "第一段"}, {"tag": "a", ...}],
                  [{"tag": "text", "text": "第二段"}]
                ]
              }
            }

        提取逻辑：
        - 优先取 zh_cn，不存在时取根对象
        - 提取所有 tag == "text" 的 text 字段
        - title 非空时拼接在最前面，与 content 间用换行分隔
        - 返回 .strip() 后的结果
        """
        try:
            node = data.get("zh_cn") or data
            title = node.get("title") or "" if isinstance(node, dict) else ""
            raw_content = node.get("content") if isinstance(node, dict) else None

            if not isinstance(raw_content, list):
                return ""

            paragraph_texts: list[str] = []
            for paragraph in raw_content:
                if not isinstance(paragraph, list):
                    continue
                words = [
                    elem.get("text", "")
                    for elem in paragraph
                    if isinstance(elem, dict) and elem.get("tag") == "text"
                ]
                paragraph_texts.append(" ".join(words))

            body = " ".join(paragraph_texts)

            if title:
                return f"{title}\n{body}".strip()
            return body.strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _extract_content(msg_type: str, content_json: str) -> str:
        """根据消息类型从 content JSON 中提取纯文本内容."""
        if not content_json:
            return ""

        try:
            data = json.loads(content_json)
        except json.JSONDecodeError:
            return ""

        if msg_type == "text":
            return data.get("text", "")

        if msg_type == "post":
            return FeishuListener._extract_post_text(data)

        # 其它类型先不做细分，统一交给上游决定如何处理
        return ""


async def run_forever(listener: FeishuListener) -> None:
    """简单的包装，方便在 main 中启动监听."""
    while True:  # 断线自动重连
        try:
            await listener.start()
        except Exception as exc:  # pragma: no cover - 运行时行为
            logger.exception("FeishuListener stopped with error, retrying: %s", exc)
            await asyncio.sleep(5.0)

