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
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from evopaw.asr.models import AsrFailure
from evopaw.models import InboundMessage, SenderProtocol
from evopaw.session.manager import SessionManager
from evopaw.session.models import MessageEntry
from evopaw.observability.metrics import (
    runner_workers_active,
    runner_queue_size,
    routing_key_type,
    record_audio_dedup_hit,
    record_audio_message,
    record_error,
)

if TYPE_CHECKING:
    from evopaw.asr.service import SpeechRecognitionService
    from evopaw.feishu.downloader import FeishuDownloader

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


def _build_voice_message(
    transcript: str,
    sandbox_path: str,
    *,
    transcription_title: str = "语音转写",
    include_audio_path: bool = True,
) -> str:
    """构造语音转写成功后传给 Agent 的增强消息（设计文档 §6.4）.

    ``include_audio_path=False`` 时不暴露沙盒路径，Agent 只看到 transcript。
    """
    parts = [
        "用户发送了一条语音消息。\n\n",
        f"{transcription_title}：\n{transcript}",
    ]
    if include_audio_path:
        parts.append(
            f"\n\n原始音频文件已保存到：\n`{sandbox_path}`\n\n"
            "请优先根据语音转写理解用户意图；"
            "如有歧义，可结合原始音频文件路径做进一步处理。"
        )
    else:
        parts.append("\n\n请根据语音转写理解用户意图。")
    return "".join(parts)


def _format_voice_reply(
    transcript: str,
    agent_reply: str,
    *,
    transcription_title: str = "语音转写",
    answer_title: str = "回答",
    display_transcript: bool = True,
) -> str:
    """格式化语音消息的最终回复（设计文档 §6.5 / §7.3）.

    ``display_transcript=False`` 时退化为纯 Agent 回复（不含转写段，也不带"回答："标题）。
    """
    reply_body = agent_reply.strip() if agent_reply else ""
    if not reply_body:
        reply_body = (
            "我已经完成语音转写，但本次未生成有效回答。"
            "你可以继续追问，或基于上面的转写文本补充说明。"
        )
    if not display_transcript:
        return reply_body
    return (
        f"{transcription_title}：\n"
        f"{transcript}\n\n"
        f"{answer_title}：\n"
        f"{reply_body}"
    )


_VOICE_AGENT_ERROR_REPLY = "处理出错，请稍后重试。"

# AsrFailure.reason → 用户可见降级文案（设计文档 §12.1-§12.5）
_VOICE_FAILURE_TEXTS: dict[str, str] = {
    "download": "语音文件下载失败，请重试，或改发文字消息。",          # §12.1
    "ws_connect": "语音已收到，但转写服务连接失败，请稍后重试。",       # §12.2
    "submit": "语音已收到，但转写服务连接失败，请稍后重试。",           # §12.2
    "disconnect": "语音转写中断，请稍后重试，或改发文字消息。",         # §12.3
    "timeout": "语音转写超时，请稍后重试，或改发文字消息。",            # §12.4
    "task_failed": "语音转写失败，请重试，或改发文字消息。",            # §12.5
    "empty": "语音转写失败，请重试，或改发文字消息。",                  # §12.5
}
_VOICE_FAILURE_DEFAULT_TEXT = "语音转写失败，请重试，或改发文字消息。"
_VOICE_ACK_DEFAULT_TEXT = "语音已收到，正在转写和分析，请稍候。"


class Runner:
    """执行引擎：per-routing_key 串行队列 + Slash Command + Agent 调度"""

    def __init__(
        self,
        session_mgr: SessionManager,
        sender: SenderProtocol,
        agent_fn: AgentFn | None = None,
        idle_timeout: float = 300.0,
        downloader: FeishuDownloader | None = None,
        speech_service: SpeechRecognitionService | None = None,
        dedup_window_size: int = 256,
        long_audio_threshold_ms: int = 15_000,
        short_wait_s: float = 10.0,
        ack_text: str = _VOICE_ACK_DEFAULT_TEXT,
        transcription_title: str = "语音转写",
        answer_title: str = "回答",
        display_transcript: bool = True,
        include_audio_path: bool = True,
    ) -> None:
        self._session_mgr = session_mgr
        self._sender = sender
        self._agent_fn = agent_fn or self._default_agent_fn
        self._idle_timeout = idle_timeout
        self._downloader = downloader
        self._speech_service = speech_service
        self._queues: dict[str, asyncio.Queue[InboundMessage]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._dispatch_lock = asyncio.Lock()
        # 最近已处理 msg_id 的 LRU 窗口，用于飞书重投递去重（设计文档 §8.1 runner）。
        self._dedup_window_size = max(0, int(dedup_window_size))
        self._recent_msg_ids: OrderedDict[str, None] = OrderedDict()
        # 语音回执参数（设计文档 §7.2 / §9）
        self._long_audio_threshold_ms = max(0, int(long_audio_threshold_ms))
        self._short_wait_s = max(0.0, float(short_wait_s))
        self._ack_text = ack_text or _VOICE_ACK_DEFAULT_TEXT
        # 语音显示参数（设计文档 §9 / Phase 4 第 5 项）
        self._transcription_title = transcription_title or "语音转写"
        self._answer_title = answer_title or "回答"
        self._display_transcript = bool(display_transcript)
        self._include_audio_path = bool(include_audio_path)

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
        """处理单条消息：dedup → slash → session → attachment/ASR → agent → send."""
        key = inbound.routing_key
        is_audio = inbound.attachment is not None and inbound.attachment.msg_type == "audio"

        # 0. 重投递去重（cron 调度不参与）
        if not inbound.is_cron and self._is_duplicate_msg(inbound.msg_id):
            logger.info(
                "[%s] duplicate msg_id skipped: %s", key, inbound.msg_id
            )
            if is_audio:
                record_audio_dedup_hit()
            return

        # 1. Slash Command 拦截（不进入 Agent，不写历史）
        slash_reply = await self._handle_slash(inbound)
        if slash_reply is not None:
            await self._sender.send(key, slash_reply, inbound.root_id)
            return

        # 2. 动态解析当前 active session
        session = await self._session_mgr.get_or_create(key)

        # 3. 附件下载 + 语音转写
        user_content = inbound.content
        transcript: str | None = None  # 语音分支才会被赋值，用于最终回复格式化
        if inbound.attachment and self._downloader:
            att = inbound.attachment
            sandbox_path = (
                f"/workspace/sessions/{session.id}/uploads/"
                f"{att.file_name}"
            )
            local_path = await self._downloader.download(
                inbound.msg_id, att, session.id
            )
            if local_path is None:
                user_content = f"[附件下载失败] {inbound.content}".strip()
                if is_audio:
                    record_audio_message("download_failed")
            elif att.msg_type == "audio" and self._speech_service is not None:
                # 语音：下载成功后送 Fun-ASR 做 one-shot 转写
                try:
                    asr = await self._transcribe_with_ack(
                        local_path=local_path,
                        attachment=att,
                        routing_key=key,
                        root_id=inbound.root_id,
                    )
                except AsrFailure as failure:
                    logger.warning(
                        "[%s] ASR 失败 msg_id=%s reason=%s detail=%s",
                        key,
                        inbound.msg_id,
                        failure.reason,
                        failure.detail,
                    )
                    record_error("runner", f"asr_{failure.reason}")
                    record_audio_message("asr_failed")
                    await self._sender.send(
                        key,
                        _VOICE_FAILURE_TEXTS.get(
                            failure.reason, _VOICE_FAILURE_DEFAULT_TEXT
                        ),
                        inbound.root_id,
                    )
                    return
                transcript = asr.transcript
                user_content = _build_voice_message(
                    transcript=transcript,
                    sandbox_path=sandbox_path,
                    transcription_title=self._transcription_title,
                    include_audio_path=self._include_audio_path,
                )
                record_audio_message("success")
            else:
                user_content = _build_attachment_message(
                    sandbox_path=sandbox_path,
                    original_text=inbound.content,
                )
                if is_audio:
                    # 有 audio 附件但 speech_service 未注入 —— 走通用附件模板
                    record_audio_message("no_service")

        # 4. 加载对话历史
        history = await self._session_mgr.load_history(session.id, max_turns=0)

        # 5. 发送 Loading 卡片（send_thinking），获取 card_msg_id
        card_msg_id = await self._sender.send_thinking(key, inbound.root_id)

        # 6. 执行 Agent；转写已成功时，Agent 异常也要保住 transcript（§12.6）
        try:
            reply = await self._agent_fn(
                user_content, history, session.id,
                inbound.routing_key, inbound.root_id, session.verbose,
            )
        except Exception:
            if transcript is None:
                raise
            logger.exception(
                "[%s] agent failed after successful ASR msg_id=%s",
                key,
                inbound.msg_id,
            )
            record_error("runner", "agent_after_asr_failed")
            reply = _VOICE_AGENT_ERROR_REPLY

        # 7. 语音消息：最终回复加 "语音转写 + 回答" 包装（§6.5 / §7.3）
        final_reply = (
            _format_voice_reply(
                transcript,
                reply,
                transcription_title=self._transcription_title,
                answer_title=self._answer_title,
                display_transcript=self._display_transcript,
            )
            if transcript is not None
            else reply
        )

        # 8. 写入 session 历史
        await self._session_mgr.append(
            session.id,
            user=user_content,
            feishu_msg_id=inbound.msg_id,
            assistant=final_reply,
        )

        # 9. 发送回复：优先更新卡片，失败时降级为 send()
        if card_msg_id:
            await self._sender.update_card(card_msg_id, final_reply)
        else:
            await self._sender.send(key, final_reply, inbound.root_id)

    # ── Voice ack ─────────────────────────────────────────────

    async def _transcribe_with_ack(
        self,
        *,
        local_path,
        attachment,
        routing_key: str,
        root_id: str,
    ):
        """包 speech_service.transcribe_file，按 §7.2 规则发回执.

        规则：
        - 飞书消息声明的 ``duration_ms > long_audio_threshold_ms`` → 立即发回执
        - 否则 ``short_wait_s`` 内拿到结果则同步返回（不发回执）；超时则发回执后继续等
        """
        assert self._speech_service is not None  # 上层已保证
        duration_ms = attachment.duration_ms
        task = asyncio.create_task(
            self._speech_service.transcribe_file(
                local_path, duration_ms=duration_ms
            )
        )
        try:
            if (
                duration_ms is not None
                and duration_ms > self._long_audio_threshold_ms
            ):
                # 长语音：立即 ack，然后等整次转写完成（客户端内部有 max_wait_s）
                await self._sender.send(routing_key, self._ack_text, root_id)
                return await task

            # 未知时长或短语音：先等 short_wait_s；超时则 ack 后继续等
            try:
                return await asyncio.wait_for(
                    asyncio.shield(task), timeout=self._short_wait_s
                )
            except asyncio.TimeoutError:
                await self._sender.send(routing_key, self._ack_text, root_id)
                return await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:  # noqa: BLE001, S110
                    pass

    # ── Dedup ─────────────────────────────────────────────────

    def _is_duplicate_msg(self, msg_id: str) -> bool:
        """检查 msg_id 是否在最近已处理集合中；未命中时插入并按 LRU 截断."""
        if not msg_id or self._dedup_window_size <= 0:
            return False
        if msg_id in self._recent_msg_ids:
            # 命中：刷新 LRU 位置，报告重复
            self._recent_msg_ids.move_to_end(msg_id)
            return True
        self._recent_msg_ids[msg_id] = None
        while len(self._recent_msg_ids) > self._dedup_window_size:
            self._recent_msg_ids.popitem(last=False)
        return False

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
        """默认 agent（未注入时使用）"""
        raise NotImplementedError("agent_fn not configured")
