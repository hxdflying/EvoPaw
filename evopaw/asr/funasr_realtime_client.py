"""Fun-ASR 实时 WebSocket API 客户端（one-shot 模式）.

本客户端只负责单次 ``run-task → 推流 → finish-task → task-finished`` 的短连接交互，
不持有任何长连接状态。

凭证 ``DASHSCOPE_API_KEY`` 只在构造期注入，不向 Skill / Sub-Agent 暴露。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from evopaw.asr.models import AsrFailure, AsrResult
from evopaw.observability.metrics import record_asr_ws_reconnect

logger = logging.getLogger(__name__)


# 允许整次 transcribe 重试的失败类型。
_RETRYABLE_REASONS = frozenset({"ws_connect", "submit", "disconnect"})


SessionFactory = Callable[[], aiohttp.ClientSession]
TaskIdFactory = Callable[[], str]


class FunASRRealtimeClient:
    """百炼 Fun-ASR 实时 WebSocket 客户端.

    每次 :meth:`transcribe` 调用创建并关闭一条独立 WebSocket，失败或超时
    一律在 ``finally`` 中显式关闭，避免连接泄漏。

    服务端事件路由（简化版）::

        task-started     -> 允许开始推流
        result-generated -> 仅 sentence_end==true 的 sentence 进 transcript
        task-finished    -> 正常终态
        task-failed      -> AsrFailure(reason="task_failed")

    其它异常：握手失败 → ``ws_connect``；submit 超时 → ``submit``；
    连接中途异常 → ``disconnect``；整体超时 → ``timeout``；拼接为空 → ``empty``。
    """

    def __init__(
        self,
        api_key: str,
        *,
        ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference/",
        model: str = "fun-asr-realtime",
        audio_format: str = "opus",
        sample_rate: int = 16000,
        chunk_bytes: int = 1024,
        chunk_interval_ms: int = 100,
        submit_timeout_s: float = 10.0,
        max_wait_s: float = 120.0,
        max_reconnect_retries: int = 1,
        provider: str = "aliyun_funasr_realtime",
        session_factory: SessionFactory | None = None,
        task_id_factory: TaskIdFactory | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY is required")
        self._api_key = api_key
        self._ws_url = ws_url
        self._model = model
        self._audio_format = audio_format
        self._sample_rate = sample_rate
        self._chunk_bytes = max(1, int(chunk_bytes))
        self._chunk_interval_s = max(0.0, float(chunk_interval_ms) / 1000.0)
        self._submit_timeout_s = float(submit_timeout_s)
        self._max_wait_s = float(max_wait_s)
        self._max_reconnect_retries = max(0, int(max_reconnect_retries))
        self._provider = provider
        self._session_factory = session_factory or aiohttp.ClientSession
        self._task_id_factory = task_id_factory or (lambda: uuid.uuid4().hex)

    # ── 对外接口 ──────────────────────────────────────────────────

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        duration_ms: int | None = None,
    ) -> AsrResult:
        """对一段完整的音频字节做 one-shot 转写（含重试）.

        对 ``ws_connect`` / ``submit`` / ``disconnect`` 三类失败做整次转写重试，
        最多 ``max_reconnect_retries`` 次。``task_failed`` / ``empty`` / ``timeout``
        不重试。

        Raises:
            AsrFailure: 所有重试耗尽或非可重试失败。
        """
        if not audio_bytes:
            raise AsrFailure(reason="empty", detail="audio_bytes is empty")

        attempt = 0
        last_exc: AsrFailure | None = None
        while True:
            try:
                return await self._transcribe_once(
                    audio_bytes, duration_ms=duration_ms
                )
            except AsrFailure as exc:
                last_exc = exc
                if (
                    exc.reason in _RETRYABLE_REASONS
                    and attempt < self._max_reconnect_retries
                ):
                    attempt += 1
                    record_asr_ws_reconnect(self._provider)
                    logger.warning(
                        "ASR 重试 reason=%s attempt=%d/%d task_id=%s",
                        exc.reason,
                        attempt,
                        self._max_reconnect_retries,
                        exc.task_id,
                    )
                    continue
                raise
        # unreachable — for type checker
        raise last_exc  # type: ignore[misc]  # pragma: no cover

    async def _transcribe_once(
        self,
        audio_bytes: bytes,
        *,
        duration_ms: int | None = None,
    ) -> AsrResult:
        """单次 one-shot 转写（不含重试）；所有失败归入 :class:`AsrFailure`."""
        task_id = self._task_id_factory()
        headers = {"Authorization": f"bearer {self._api_key}"}
        run_task_msg = self._build_run_task(task_id)
        finish_task_msg = self._build_finish_task(task_id)

        session: aiohttp.ClientSession | None = None
        ws: Any = None
        try:
            session = self._session_factory()
            try:
                ws = await session.ws_connect(self._ws_url, headers=headers)
            except Exception as exc:  # noqa: BLE001
                raise AsrFailure(
                    reason="ws_connect",
                    detail=str(exc),
                    task_id=task_id,
                ) from exc

            await ws.send_json(run_task_msg)

            # 1) 等待 task-started
            try:
                first_event = await asyncio.wait_for(
                    self._receive_text_event(ws, task_id),
                    timeout=self._submit_timeout_s,
                )
            except asyncio.TimeoutError as exc:
                raise AsrFailure(
                    reason="submit",
                    detail=f"task-started not received within {self._submit_timeout_s}s",
                    task_id=task_id,
                ) from exc

            first_evt_name = _event_name(first_event)
            if first_evt_name == "task-failed":
                raise AsrFailure(
                    reason="task_failed",
                    detail=_error_detail(first_event),
                    task_id=task_id,
                )
            if first_evt_name != "task-started":
                raise AsrFailure(
                    reason="submit",
                    detail=f"unexpected first event: {first_evt_name!r}",
                    task_id=task_id,
                )

            # 2) 推流 + 读取并发执行，整体受 max_wait_s 限制
            sentences: list[dict[str, Any]] = []

            async def writer() -> None:
                for i in range(0, len(audio_bytes), self._chunk_bytes):
                    await ws.send_bytes(audio_bytes[i : i + self._chunk_bytes])
                    if self._chunk_interval_s > 0:
                        await asyncio.sleep(self._chunk_interval_s)
                await ws.send_json(finish_task_msg)

            async def reader() -> None:
                while True:
                    event = await self._receive_text_event(ws, task_id)
                    name = _event_name(event)
                    if name == "result-generated":
                        sentence = (
                            (event.get("payload") or {}).get("output", {}).get("sentence")
                        )
                        if (
                            isinstance(sentence, dict)
                            and sentence.get("sentence_end") is True
                        ):
                            sentences.append(sentence)
                    elif name == "task-finished":
                        return
                    elif name == "task-failed":
                        raise AsrFailure(
                            reason="task_failed",
                            detail=_error_detail(event),
                            task_id=task_id,
                        )
                    # 其它事件忽略

            try:
                await asyncio.wait_for(
                    _run_writer_reader(writer, reader),
                    timeout=self._max_wait_s,
                )
            except asyncio.TimeoutError as exc:
                raise AsrFailure(
                    reason="timeout",
                    detail=f"overall transcribe exceeded {self._max_wait_s}s",
                    task_id=task_id,
                ) from exc

            transcript = _merge_sentences(sentences)
            if not transcript:
                raise AsrFailure(
                    reason="empty",
                    detail="no sentence_end text received",
                    task_id=task_id,
                )

            logger.info(
                "ASR 转写成功 task_id=%s chars=%d sentences=%d",
                task_id,
                len(transcript),
                len(sentences),
            )
            return AsrResult(
                transcript=transcript,
                provider=self._provider,
                model=self._model,
                task_id=task_id,
                duration_ms=duration_ms,
            )
        finally:
            if ws is not None:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    logger.debug("ws.close() raised; ignored", exc_info=True)
            if session is not None:
                try:
                    await session.close()
                except Exception:  # noqa: BLE001
                    logger.debug("session.close() raised; ignored", exc_info=True)

    # ── 内部辅助 ──────────────────────────────────────────────────

    def _build_run_task(self, task_id: str) -> dict[str, Any]:
        return {
            "header": {
                "action": "run-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self._model,
                "parameters": {
                    "format": self._audio_format,
                    "sample_rate": self._sample_rate,
                },
                "input": {},
            },
        }

    def _build_finish_task(self, task_id: str) -> dict[str, Any]:
        return {
            "header": {
                "action": "finish-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }

    async def _receive_text_event(self, ws: Any, task_id: str) -> dict[str, Any]:
        """拉取下一条文本事件；非文本 / 关闭 / 错误一律归入 disconnect."""
        while True:
            msg = await ws.receive()
            msg_type = getattr(msg, "type", None)
            if msg_type == aiohttp.WSMsgType.TEXT:
                try:
                    return json.loads(msg.data)
                except json.JSONDecodeError as exc:
                    raise AsrFailure(
                        reason="disconnect",
                        detail=f"invalid json frame: {exc}",
                        task_id=task_id,
                    ) from exc
            if msg_type == aiohttp.WSMsgType.BINARY:
                # 服务端不应主动发二进制帧，忽略即可
                continue
            if msg_type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSE,
            ):
                raise AsrFailure(
                    reason="disconnect",
                    detail=f"websocket closed: {getattr(msg, 'data', None)!r}",
                    task_id=task_id,
                )
            if msg_type == aiohttp.WSMsgType.ERROR:
                raise AsrFailure(
                    reason="disconnect",
                    detail=f"websocket error: {ws.exception()!r}",
                    task_id=task_id,
                )
            # 其它类型（PING / PONG / CONTINUATION）继续循环


# ── 模块级辅助 ────────────────────────────────────────────────────


def _event_name(event: dict[str, Any]) -> str:
    header = event.get("header") if isinstance(event, dict) else None
    if isinstance(header, dict):
        return str(header.get("event") or "")
    return ""


def _error_detail(event: dict[str, Any]) -> str:
    header = event.get("header") or {}
    code = header.get("error_code") or header.get("code")
    message = header.get("error_message") or header.get("message")
    return f"{code}: {message}" if code or message else "task_failed"


def _merge_sentences(sentences: list[dict[str, Any]]) -> str:
    sentences_sorted = sorted(
        sentences,
        key=lambda s: s.get("begin_time") if isinstance(s.get("begin_time"), int) else 0,
    )
    texts = [str(s.get("text") or "").strip() for s in sentences_sorted]
    return " ".join(t for t in texts if t)


async def _run_writer_reader(
    writer: Callable[[], Awaitable[None]],
    reader: Callable[[], Awaitable[None]],
) -> None:
    """同时运行 writer 和 reader；任一抛错则取消另一个并透传。"""
    writer_task = asyncio.create_task(writer())
    reader_task = asyncio.create_task(reader())
    try:
        done, pending = await asyncio.wait(
            {writer_task, reader_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        # 若 reader 先结束（task-finished）而 writer 还在跑，等 writer 收尾
        if reader_task in done and reader_task.exception() is None:
            if not writer_task.done():
                await writer_task
            return
        # 否则，聚合第一个异常并取消另一个
        for t in done:
            exc = t.exception()
            if exc is not None:
                for p in pending:
                    p.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                raise exc
        # 两个都无异常完成：正常返回
    finally:
        for t in (writer_task, reader_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(writer_task, reader_task, return_exceptions=True)
