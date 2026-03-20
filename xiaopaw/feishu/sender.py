"""FeishuSender — 根据 routing_key 发送文本消息到飞书."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from lark_oapi.client import Client
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from xiaopaw.models import SenderProtocol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FeishuSender(SenderProtocol):
    """基于 lark-oapi 的飞书发送实现."""

    client: Client
    max_retries: int = 3
    retry_backoff: tuple[int, ...] = (1, 2, 4)

    async def send(self, routing_key: str, content: str, root_id: str) -> None:
        """发送 interactive 卡片消息（lark_md Markdown 格式）."""
        msg_type = "interactive"
        msg_content = self._build_card(content)

        for attempt in range(self.max_retries):
            try:
                if routing_key.startswith("p2p:"):
                    receive_id = routing_key.split(":", 1)[1]
                    await self._send_p2p(receive_id, msg_type, msg_content, root_id)
                elif routing_key.startswith("group:"):
                    chat_id = routing_key.split(":", 1)[1]
                    await self._send_group(chat_id, msg_type, msg_content, root_id)
                elif routing_key.startswith("thread:"):
                    await self._send_thread(root_id, msg_type, msg_content, root_id)
                else:
                    logger.warning("Unknown routing_key: %s", routing_key)
                return
            except Exception as exc:  # pragma: no cover - 网络错误在集成环境验证
                logger.warning(
                    "Failed to send message to %s (attempt %d/%d): %s",
                    routing_key,
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
                if attempt + 1 >= self.max_retries:
                    break
                delay = self.retry_backoff[min(
                    attempt, len(self.retry_backoff) - 1
                )]
                await asyncio.sleep(delay)

    def _build_card(self, text: str) -> str:
        """构建飞书交互式卡片 JSON（lark_md 格式）."""
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "content": text,
                        "tag": "lark_md",
                    },
                }
            ],
        }
        return json.dumps(card, ensure_ascii=False)

    async def send_thinking(
        self, routing_key: str, root_id: str
    ) -> str | None:
        """发送 '⏳ 思考中...' Loading 卡片，返回 card_msg_id。

        失败时返回 None，不阻断主流程。
        """
        msg_type = "interactive"
        msg_content = self._build_card("⏳ 思考中，请稍候...")
        try:
            if routing_key.startswith("p2p:"):
                receive_id = routing_key.split(":", 1)[1]
                resp = await self._create_p2p_raw(receive_id, msg_type, msg_content, root_id)
            elif routing_key.startswith("group:"):
                chat_id = routing_key.split(":", 1)[1]
                resp = await self._create_group_raw(chat_id, msg_type, msg_content, root_id)
            elif routing_key.startswith("thread:"):
                resp = await self._reply_thread_raw(root_id, msg_type, msg_content, root_id)
            else:
                logger.warning("send_thinking: unknown routing_key %s", routing_key)
                return None

            if not resp.success():
                logger.warning(
                    "send_thinking API failed: %s %s", resp.code, resp.msg
                )
                return None
            return resp.data.message_id
        except Exception as exc:
            logger.warning("send_thinking failed: %s", exc)
            return None

    async def update_card(self, card_msg_id: str, content: str) -> None:
        """PATCH 更新已发送的卡片内容（用 Agent 结果替换 Loading 文字）."""
        card_content = self._build_card(content)
        body = (
            PatchMessageRequestBody.builder()
            .content(card_content)
            .build()
        )
        req = (
            PatchMessageRequest.builder()
            .message_id(card_msg_id)
            .request_body(body)
            .build()
        )
        resp = await self.client.im.v1.message.apatch(req)
        if not resp.success():
            raise RuntimeError(
                f"Feishu patch message failed: {resp.code}, {resp.msg}"
            )

    async def send_text(
        self, routing_key: str, content: str, root_id: str
    ) -> None:
        """发送纯文本消息（供 slash 命令使用）."""
        msg_type = "text"
        msg_content = json.dumps({"text": content}, ensure_ascii=False)

        for attempt in range(self.max_retries):
            try:
                if routing_key.startswith("p2p:"):
                    receive_id = routing_key.split(":", 1)[1]
                    await self._send_p2p(receive_id, msg_type, msg_content, root_id)
                elif routing_key.startswith("group:"):
                    chat_id = routing_key.split(":", 1)[1]
                    await self._send_group(chat_id, msg_type, msg_content, root_id)
                elif routing_key.startswith("thread:"):
                    await self._send_thread(root_id, msg_type, msg_content, root_id)
                else:
                    logger.warning("send_text: unknown routing_key %s", routing_key)
                return
            except Exception as exc:  # pragma: no cover - 网络错误在集成环境验证
                logger.warning(
                    "Failed to send_text to %s (attempt %d/%d): %s",
                    routing_key,
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
                if attempt + 1 >= self.max_retries:
                    break
                delay = self.retry_backoff[min(
                    attempt, len(self.retry_backoff) - 1
                )]
                await asyncio.sleep(delay)

    async def _send_p2p(
        self, open_id: str, msg_type: str, content: str, uuid: str
    ) -> None:
        resp = await self._create_p2p_raw(open_id, msg_type, content, uuid)
        if not resp.success():
            raise RuntimeError(f"Feishu create message failed: {resp.code}, {resp.msg}")

    async def _create_p2p_raw(
        self, open_id: str, msg_type: str, content: str, uuid: str
    ):
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(open_id)
            .msg_type(msg_type)
            .content(content)
            .uuid(uuid)
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(body)
            .build()
        )
        return await self.client.im.v1.message.acreate(req)

    async def _send_group(
        self, chat_id: str, msg_type: str, content: str, uuid: str
    ) -> None:
        resp = await self._create_group_raw(chat_id, msg_type, content, uuid)
        if not resp.success():
            raise RuntimeError(f"Feishu create message failed: {resp.code}, {resp.msg}")

    async def _create_group_raw(
        self, chat_id: str, msg_type: str, content: str, uuid: str
    ):
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type(msg_type)
            .content(content)
            .uuid(uuid)
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        return await self.client.im.v1.message.acreate(req)

    async def _send_thread(
        self, root_id: str, msg_type: str, content: str, uuid: str
    ) -> None:
        resp = await self._reply_thread_raw(root_id, msg_type, content, uuid)
        if not resp.success():
            raise RuntimeError(f"Feishu reply message failed: {resp.code}, {resp.msg}")

    async def _reply_thread_raw(
        self, root_id: str, msg_type: str, content: str, uuid: str
    ):
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type(msg_type)
            .content(content)
            .reply_in_thread(True)
            .uuid(uuid)
            .build()
        )
        req = (
            ReplyMessageRequest.builder()
            .message_id(root_id)
            .request_body(body)
            .build()
        )
        return await self.client.im.v1.message.areply(req)

