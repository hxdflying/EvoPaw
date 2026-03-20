"""Runner — 执行引擎：per-routing_key 串行队列、Slash Command、Agent 调度

并发控制:
- 同一 routing_key 的消息串行处理（per-routing_key asyncio.Queue + worker）
- 不同 routing_key 之间并行
- worker 空闲超时后自动退出，释放内存
- _dispatch_lock 保护 queue/worker 的创建与清理，避免边界竞态
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from xiaopaw.models import InboundMessage, SenderProtocol
from xiaopaw.session.manager import SessionManager
from xiaopaw.session.models import MessageEntry
from xiaopaw.observability.metrics import (
    runner_workers_active,
    runner_queue_size,
    routing_key_type,
    record_error,
)

if TYPE_CHECKING:
    from xiaopaw.feishu.downloader import FeishuDownloader

logger = logging.getLogger(__name__)

AgentFn = Callable[[str, list[MessageEntry], str, str, str, bool], Awaitable[str]]
# 参数依次: user_message, history, session_id, routing_key, root_id, verbose


_HELP_TEXT = """\
可用命令：
/new — 创建新对话（清除历史上下文）
/verbose on|off — 开启/关闭详细模式（显示推理过程）
/verbose — 查询当前详细模式状态
/status — 查看当前对话信息
/help — 显示本帮助"""

_SLASH_COMMANDS = frozenset({"/new", "/verbose", "/help", "/status"})


def _build_attachment_message(sandbox_path: str, original_text: str) -> str:
    """构造附件下载成功后传给 Agent 的模板消息"""
    msg = (
        f"用户发来了文件，已自动保存至沙盒路径：\n`{sandbox_path}`\n"
        "请根据文件内容和用户意图完成相应处理。"
    )
    if original_text.strip():
        msg += f"\n用户备注：{original_text}"
    return msg


class Runner:
    """执行引擎：per-routing_key 串行队列 + Slash Command + Agent 调度"""

    def __init__(
        self,
        session_mgr: SessionManager,
        sender: SenderProtocol,
        agent_fn: AgentFn | None = None,
        idle_timeout: float = 300.0,
        downloader: FeishuDownloader | None = None,
    ) -> None:
        self._session_mgr = session_mgr
        self._sender = sender
        self._agent_fn = agent_fn or self._default_agent_fn
        self._idle_timeout = idle_timeout
        self._downloader = downloader
        self._queues: dict[str, asyncio.Queue[InboundMessage]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._dispatch_lock = asyncio.Lock()

    # ── 公开方法 ───────────────────────────────────────────────

    async def dispatch(self, inbound: InboundMessage) -> None:
        """外部入口：消息入队，确保同一会话串行执行"""
        key = inbound.routing_key
        async with self._dispatch_lock:
            if key not in self._queues:
                self._queues[key] = asyncio.Queue()
                self._workers[key] = asyncio.create_task(self._worker(key))
                rk_type = routing_key_type(key)
                runner_workers_active.labels(routing_key_type=rk_type).inc()
        await self._queues[key].put(inbound)
        rk_type = routing_key_type(key)
        runner_queue_size.labels(routing_key_type=rk_type).set(
            self._queues[key].qsize()
        )

    async def shutdown(self) -> None:
        """取消所有 worker，释放资源"""
        for key, queue in self._queues.items():
            if not queue.empty():
                logger.warning(
                    "[%s] shutting down with %d unprocessed messages",
                    key,
                    queue.qsize(),
                )
        for task in list(self._workers.values()):
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()
        self._queues.clear()

    # ── Worker ────────────────────────────────────────────────

    async def _worker(self, key: str) -> None:
        """per-routing_key worker：逐条消费队列，空闲超时后退出"""
        queue = self._queues[key]
        while True:
            try:
                inbound = await asyncio.wait_for(
                    queue.get(), timeout=self._idle_timeout
                )
            except asyncio.TimeoutError:
                async with self._dispatch_lock:
                    # 仅当自己仍是该 key 的 worker 时才清理
                    if self._workers.get(key) is asyncio.current_task():
                        self._queues.pop(key, None)
                        self._workers.pop(key, None)
                        rk_type = routing_key_type(key)
                        runner_workers_active.labels(
                            routing_key_type=rk_type
                        ).dec()
                return
            try:
                await self._handle(inbound)
            except Exception:
                logger.exception("[%s] handle error", key)
                record_error("runner", "handle_error")
                try:
                    await self._sender.send(
                        key, "处理出错，请稍后重试。", inbound.root_id
                    )
                except Exception:
                    logger.exception("[%s] failed to send error message", key)
                    record_error("runner", "send_error_message_failed")
            finally:
                queue.task_done()

    # ── Handle ────────────────────────────────────────────────

    async def _handle(self, inbound: InboundMessage) -> None:
        """处理单条消息：slash 拦截 → session → agent → append → send"""
        key = inbound.routing_key

        # 1. Slash Command 拦截（不进入 Agent，不写历史）
        slash_reply = await self._handle_slash(inbound)
        if slash_reply is not None:
            await self._sender.send_text(key, slash_reply, inbound.root_id)
            return

        # 2. 动态解析当前 active session
        session = await self._session_mgr.get_or_create(key)

        # 3. 附件下载
        user_content = inbound.content
        if inbound.attachment and self._downloader:
            sandbox_path = (
                f"/workspace/sessions/{session.id}/uploads/"
                f"{inbound.attachment.file_name}"
            )
            local_path = await self._downloader.download(
                inbound.msg_id, inbound.attachment, session.id
            )
            if local_path is not None:
                user_content = _build_attachment_message(
                    sandbox_path=sandbox_path,
                    original_text=inbound.content,
                )
            else:
                user_content = f"[附件下载失败] {inbound.content}".strip()

        # 4. 加载对话历史
        history = await self._session_mgr.load_history(session.id)

        # 5. 发送 Loading 卡片（send_thinking），获取 card_msg_id
        card_msg_id = await self._sender.send_thinking(key, inbound.root_id)

        # 6. 执行 Agent
        reply = await self._agent_fn(
            user_content, history, session.id,
            inbound.routing_key, inbound.root_id, session.verbose,
        )

        # 7. 写入 session 历史
        await self._session_mgr.append(
            session.id,
            user=user_content,
            feishu_msg_id=inbound.msg_id,
            assistant=reply,
        )

        # 8. 发送回复：优先更新卡片，失败时降级为 send()
        if card_msg_id:
            await self._sender.update_card(card_msg_id, reply)
        else:
            await self._sender.send(key, reply, inbound.root_id)

    # ── Slash Command ─────────────────────────────────────────

    async def _handle_slash(self, inbound: InboundMessage) -> str | None:
        """处理 slash command，返回回复文本；非 slash command 返回 None"""
        text = inbound.content.strip()
        if not text.startswith("/"):
            return None

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip().lower() if len(parts) > 1 else ""

        if cmd not in _SLASH_COMMANDS:
            return None

        key = inbound.routing_key

        if cmd == "/new":
            new_session = await self._session_mgr.create_new_session(key)
            return f"已创建新对话 {new_session.id}，之前的历史不会带入。"

        if cmd == "/verbose":
            if arg == "on":
                await self._session_mgr.get_or_create(key)
                await self._session_mgr.update_verbose(key, True)
                return "详细模式已开启，我会把推理过程发给你。"
            if arg == "off":
                await self._session_mgr.get_or_create(key)
                await self._session_mgr.update_verbose(key, False)
                return "详细模式已关闭。"
            # 查询当前状态
            session = await self._session_mgr.get_or_create(key)
            status = "开启" if session.verbose else "关闭"
            return f"当前详细模式：{status}"

        if cmd == "/help":
            return _HELP_TEXT

        if cmd == "/status":
            session = await self._session_mgr.get_or_create(key)
            verbose_str = "开启" if session.verbose else "关闭"
            return (
                f"当前对话：{session.id}\n"
                f"消息数：{session.message_count}\n"
                f"详细模式：{verbose_str}"
            )

        return None  # pragma: no cover

    # ── Default Agent ─────────────────────────────────────────

    @staticmethod
    async def _default_agent_fn(
        user_message: str,
        history: list[MessageEntry],
        session_id: str,
        routing_key: str = "",
        root_id: str = "",
        verbose: bool = False,
    ) -> str:
        """默认 agent（未注入时使用），后续替换为 CrewAI"""
        raise NotImplementedError("agent_fn not configured")
