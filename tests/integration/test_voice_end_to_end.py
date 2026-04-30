"""语音端到端集成测试。

启动一个本地 aiohttp WebSocket mock server 模拟百炼 Fun-ASR 实时 API，
跑完整的 Runner → SpeechRecognitionService → FunASRRealtimeClient → WS 链路，
断言最终回复格式为 "语音转写 + 回答"、session 历史写入正确。

本测试不依赖外网、不依赖 Anthropic API Key，可离线运行。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from evopaw.asr.funasr_realtime_client import FunASRRealtimeClient
from evopaw.asr.service import SpeechRecognitionService
from evopaw.models import Attachment, InboundMessage
from evopaw.runner import Runner
from evopaw.session.manager import SessionManager


pytestmark = pytest.mark.integration


# ── Fun-ASR WebSocket mock server ──────────────────────────────


def _make_mock_handler(
    sentences: list[str],
    *,
    fail_after_started: bool = False,
    pre_finish_delay_s: float = 0.0,
    never_send_finished: bool = False,
    drop_after_started: bool = False,
):
    """返回一个 aiohttp WebSocket handler，按 Fun-ASR 协议下发事件.

    参数:
        sentences: result-generated 中要下发的 sentence_end=true 句子文本（按顺序）
        fail_after_started: True 时 task-started 后立刻 task-failed
        pre_finish_delay_s: 收到 finish-task 后的额外延迟（用于模拟慢转写以测 ack 流）
        never_send_finished: True 时不下发 task-finished（测整体超时）
        drop_after_started: True 时 task-started 后直接 close（测 disconnect 分类）
    """

    async def handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        task_id: str | None = None

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                payload = json.loads(msg.data)
                header = payload.get("header") or {}
                action = header.get("action")
                if action == "run-task":
                    task_id = header.get("task_id")
                    await ws.send_json({
                        "header": {"event": "task-started", "task_id": task_id},
                    })
                    if fail_after_started:
                        await ws.send_json({
                            "header": {
                                "event": "task-failed",
                                "task_id": task_id,
                                "error_code": "E-MOCK",
                                "error_message": "mock failure",
                            },
                        })
                        await ws.close()
                        return ws
                    if drop_after_started:
                        # 立即关闭连接，触发 client 的 disconnect 路径
                        await ws.close()
                        return ws
                elif action == "finish-task":
                    if pre_finish_delay_s > 0:
                        await asyncio.sleep(pre_finish_delay_s)
                    if never_send_finished:
                        # 故意不下发任何事件，等 client 的 max_wait_s 超时
                        await asyncio.sleep(60)
                        return ws
                    for idx, text in enumerate(sentences):
                        await ws.send_json({
                            "header": {"event": "result-generated", "task_id": task_id},
                            "payload": {
                                "output": {
                                    "sentence": {
                                        "text": text,
                                        "sentence_end": True,
                                        "begin_time": idx * 1000,
                                        "end_time": idx * 1000 + 900,
                                    }
                                }
                            },
                        })
                    await ws.send_json({
                        "header": {"event": "task-finished", "task_id": task_id},
                    })
                    await ws.close()
                    return ws
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break

        return ws

    return handler


async def _spawn_ws_server(
    sentences: list[str],
    *,
    fail_after_started: bool = False,
    pre_finish_delay_s: float = 0.0,
    never_send_finished: bool = False,
    drop_after_started: bool = False,
) -> tuple[TestServer, str]:
    """返回 (server, ws_url)."""
    app = web.Application()
    app.router.add_get(
        "/api-ws/v1/inference/",
        _make_mock_handler(
            sentences,
            fail_after_started=fail_after_started,
            pre_finish_delay_s=pre_finish_delay_s,
            never_send_finished=never_send_finished,
            drop_after_started=drop_after_started,
        ),
    )
    server = TestServer(app)
    await server.start_server()
    host, port = server.host, server.port
    return server, f"ws://{host}:{port}/api-ws/v1/inference/"


# ── Runner MockSender（复用单测风格）────────────────────────────


class _Sender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send(self, routing_key: str, content: str, root_id: str) -> None:
        msg = (routing_key, content, root_id)
        self.messages.append(msg)
        await self._queue.put(msg)

    async def send_text(self, routing_key: str, content: str, root_id: str) -> None:
        await self.send(routing_key, content, root_id)

    async def send_thinking(self, routing_key: str, root_id: str) -> str | None:
        return None

    async def update_card(self, card_msg_id: str, content: str) -> None:
        msg = ("card", content, card_msg_id)
        self.messages.append(msg)
        await self._queue.put(msg)

    async def wait(self, timeout: float = 3.0) -> tuple[str, str, str]:
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)


class _Downloader:
    """直接返回预置本地路径的下载器 stub."""

    def __init__(self, audio_path: Path) -> None:
        self._audio_path = audio_path

    async def download(self, msg_id: str, attachment: Attachment, session_id: str) -> Path:
        return self._audio_path


async def _echo_agent(
    user_message: str,
    history,
    session_id: str,
    routing_key: str = "",
    root_id: str = "",
    verbose: bool = False,
) -> str:
    # 只要收到"语音转写:"开头的模板，就假装理解后回答
    if "语音转写：" in user_message:
        return "我知道了，这是我的回答。"
    return f"echo: {user_message}"


def _audio_inbound(msg_id: str = "om_e2e", duration_ms: int | None = 3000) -> InboundMessage:
    att = Attachment(
        msg_type="audio",
        file_key="fk_e2e",
        file_name="fk_e2e.audio",
        duration_ms=duration_ms,
    )
    return InboundMessage(
        routing_key="p2p:ou_e2e",
        content="",
        msg_id=msg_id,
        root_id=msg_id,
        sender_id="ou_e2e",
        ts=1,
        attachment=att,
    )


# ── Tests ──────────────────────────────────────────────────────


class TestVoiceEndToEnd:
    async def test_happy_path_ws_mock_delivers_transcript_and_reply(self, tmp_path):
        server, ws_url = await _spawn_ws_server(
            sentences=["你好", "请问现在几点"]
        )
        try:
            audio_path = tmp_path / "fk_e2e.audio"
            audio_path.write_bytes(b"\x00" * 4096)  # mock 字节流（服务器不校验内容）

            client = FunASRRealtimeClient(
                api_key="sk-mock",
                ws_url=ws_url,
                chunk_bytes=256,
                chunk_interval_ms=0,
                submit_timeout_s=5.0,
                max_wait_s=10.0,
                max_reconnect_retries=0,
            )
            svc = SpeechRecognitionService(client)

            session_mgr = SessionManager(data_dir=tmp_path)
            sender = _Sender()
            runner = Runner(
                session_mgr=session_mgr,
                sender=sender,
                agent_fn=_echo_agent,
                idle_timeout=5.0,
                downloader=_Downloader(audio_path),
                speech_service=svc,
                long_audio_threshold_ms=60_000,
                short_wait_s=5.0,
            )
            try:
                await runner.dispatch(_audio_inbound())
                _, reply, _ = await sender.wait(timeout=10.0)

                # 正式回复必须有两段：语音转写 + 回答
                assert reply.startswith("语音转写：")
                assert "你好" in reply
                assert "请问现在几点" in reply
                assert "回答：" in reply
                assert "我的回答" in reply

                # session 历史写入格式化后的回复
                session = await session_mgr.get_or_create("p2p:ou_e2e")
                history = await session_mgr.load_history(session.id)
                assistant = next(h for h in history if h.role == "assistant")
                assert assistant.content.startswith("语音转写：")
            finally:
                await runner.shutdown()
        finally:
            await server.close()

    async def test_ws_task_failed_yields_classified_reply(self, tmp_path):
        server, ws_url = await _spawn_ws_server(
            sentences=[], fail_after_started=True,
        )
        try:
            audio_path = tmp_path / "fk.audio"
            audio_path.write_bytes(b"\x00" * 256)

            client = FunASRRealtimeClient(
                api_key="sk-mock",
                ws_url=ws_url,
                chunk_bytes=64,
                chunk_interval_ms=0,
                submit_timeout_s=3.0,
                max_wait_s=5.0,
                max_reconnect_retries=0,
            )
            svc = SpeechRecognitionService(client)

            session_mgr = SessionManager(data_dir=tmp_path)
            sender = _Sender()
            runner = Runner(
                session_mgr=session_mgr,
                sender=sender,
                agent_fn=_echo_agent,
                idle_timeout=5.0,
                downloader=_Downloader(audio_path),
                speech_service=svc,
                long_audio_threshold_ms=60_000,
                short_wait_s=5.0,
            )
            try:
                await runner.dispatch(_audio_inbound(msg_id="om_fail"))
                _, reply, _ = await sender.wait(timeout=10.0)
                # task_failed 类别映射到通用转写失败文案。
                assert "转写失败" in reply
            finally:
                await runner.shutdown()
        finally:
            await server.close()


def _build_runner(
    *,
    tmp_path: Path,
    ws_url: str,
    audio_bytes_size: int = 1024,
    short_wait_s: float = 5.0,
    long_audio_threshold_ms: int = 60_000,
    max_wait_s: float = 8.0,
    max_reconnect_retries: int = 0,
) -> tuple[Runner, "_Sender", SessionManager]:
    """构造一套完整 Runner 配置（同 4 个用例共用）."""
    audio_path = tmp_path / "fk_e2e.audio"
    audio_path.write_bytes(b"\x00" * audio_bytes_size)

    client = FunASRRealtimeClient(
        api_key="sk-mock",
        ws_url=ws_url,
        chunk_bytes=256,
        chunk_interval_ms=0,
        submit_timeout_s=3.0,
        max_wait_s=max_wait_s,
        max_reconnect_retries=max_reconnect_retries,
    )
    svc = SpeechRecognitionService(client)
    session_mgr = SessionManager(data_dir=tmp_path)
    sender = _Sender()
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=_echo_agent,
        idle_timeout=5.0,
        downloader=_Downloader(audio_path),
        speech_service=svc,
        long_audio_threshold_ms=long_audio_threshold_ms,
        short_wait_s=short_wait_s,
    )
    return runner, sender, session_mgr


# ── 四类样例（mock 层面可验证的部分）──────────────────────────


class TestVoiceFourSampleCategories:
    """四类真实样例中 mock server 层面能验证的子集。

    真实录音、采样率、转写质量等部分仍需 runbook 步骤 D 用真凭证联调。
    本 class 验证的是"链路在四类输入下能给出预期回复格式与失败分类"。
    """

    async def test_short_audio_completes_within_one_card(self, tmp_path):
        """样例 1：3 秒短语音 — duration<阈值且 ASR 立刻完成 → 无 ack，仅一条最终回复."""
        server, ws_url = await _spawn_ws_server(sentences=["你好"])
        try:
            runner, sender, _ = _build_runner(
                tmp_path=tmp_path,
                ws_url=ws_url,
                long_audio_threshold_ms=60_000,
                short_wait_s=5.0,
            )
            try:
                await runner.dispatch(_audio_inbound(msg_id="om_short", duration_ms=3000))
                _, reply, _ = await sender.wait(timeout=10.0)

                assert reply.startswith("语音转写：")
                assert "你好" in reply
                # 不应有第二条消息（短音频不发 ack）
                with pytest.raises(asyncio.TimeoutError):
                    await sender.wait(timeout=0.5)
            finally:
                await runner.shutdown()
        finally:
            await server.close()

    async def test_long_audio_sends_ack_then_final_reply(self, tmp_path):
        """样例 2：20 秒长语音 — duration > long_audio_threshold_ms → 先 ack，后正式回复."""
        server, ws_url = await _spawn_ws_server(
            sentences=["请帮我", "总结昨天的会议纪要"],
        )
        try:
            runner, sender, session_mgr = _build_runner(
                tmp_path=tmp_path,
                ws_url=ws_url,
                long_audio_threshold_ms=10_000,  # 让 20s 触发回执
                short_wait_s=10.0,
            )
            try:
                await runner.dispatch(_audio_inbound(msg_id="om_long", duration_ms=20_000))

                first = await sender.wait(timeout=5.0)
                # 第一条应为 ack
                assert "稍候" in first[1] or "正在转写" in first[1], first[1]

                second = await sender.wait(timeout=10.0)
                # 第二条为正式回复
                assert second[1].startswith("语音转写：")
                assert "请帮我" in second[1]
                assert "总结昨天的会议纪要" in second[1]

                # session 历史只写最终回复（不写 ack）
                session = await session_mgr.get_or_create("p2p:ou_e2e")
                history = await session_mgr.load_history(session.id)
                assistants = [h for h in history if h.role == "assistant"]
                assert len(assistants) == 1
                assert assistants[0].content.startswith("语音转写：")
            finally:
                await runner.shutdown()
        finally:
            await server.close()

    async def test_disconnect_mid_stream_yields_disconnect_reply(self, tmp_path):
        """样例 4 子项：连接中途异常 → disconnect 文案。"""
        server, ws_url = await _spawn_ws_server(
            sentences=[], drop_after_started=True,
        )
        try:
            runner, sender, _ = _build_runner(
                tmp_path=tmp_path,
                ws_url=ws_url,
                max_reconnect_retries=0,  # 关闭重试以观察单次失败
            )
            try:
                await runner.dispatch(_audio_inbound(msg_id="om_disconnect"))
                _, reply, _ = await sender.wait(timeout=10.0)
                assert "转写中断" in reply
            finally:
                await runner.shutdown()
        finally:
            await server.close()

    async def test_overall_timeout_yields_timeout_reply(self, tmp_path):
        """样例 4 子项：服务端 task-started 后再无任何事件 → timeout 文案。"""
        server, ws_url = await _spawn_ws_server(
            sentences=[], never_send_finished=True,
        )
        try:
            runner, sender, _ = _build_runner(
                tmp_path=tmp_path,
                ws_url=ws_url,
                short_wait_s=10.0,
                max_wait_s=0.4,  # 立刻让客户端整体超时
                max_reconnect_retries=0,
            )
            try:
                await runner.dispatch(_audio_inbound(msg_id="om_timeout"))
                _, reply, _ = await sender.wait(timeout=10.0)
                assert "转写超时" in reply
            finally:
                await runner.shutdown()
        finally:
            await server.close()
