"""FunASRRealtimeClient 单元测试（覆盖设计文档 §16.1.4）."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import pytest

from evopaw.asr.funasr_realtime_client import FunASRRealtimeClient
from evopaw.asr.models import AsrFailure


# ── Fakes ─────────────────────────────────────────────────────────


class _FakeMsg:
    def __init__(self, type_: aiohttp.WSMsgType, data: Any = None) -> None:
        self.type = type_
        self.data = data


class _FakeWS:
    """最小化 aiohttp ClientWebSocketResponse 替身."""

    def __init__(self) -> None:
        self.sent_json: list[dict[str, Any]] = []
        self.sent_bytes: list[bytes] = []
        self._incoming: asyncio.Queue[_FakeMsg] = asyncio.Queue()
        self.closed = False
        self._exception: BaseException | None = None

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent_json.append(msg)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def receive(self) -> _FakeMsg:
        return await self._incoming.get()

    async def close(self) -> None:
        self.closed = True

    def exception(self) -> BaseException | None:
        return self._exception

    # Test helpers
    def push_event(self, event: dict[str, Any]) -> None:
        self._incoming.put_nowait(_FakeMsg(aiohttp.WSMsgType.TEXT, json.dumps(event)))

    def push_closed(self) -> None:
        self._incoming.put_nowait(_FakeMsg(aiohttp.WSMsgType.CLOSED))

    def push_error(self, exc: BaseException | None = None) -> None:
        self._exception = exc
        self._incoming.put_nowait(_FakeMsg(aiohttp.WSMsgType.ERROR))


class _FakeSession:
    def __init__(
        self,
        ws: _FakeWS | None = None,
        connect_error: BaseException | None = None,
    ) -> None:
        self.ws = ws or _FakeWS()
        self._connect_error = connect_error
        self.closed = False
        self.connect_url: str | None = None
        self.connect_headers: dict[str, str] | None = None

    async def ws_connect(self, url: str, headers: dict[str, str] | None = None) -> _FakeWS:
        self.connect_url = url
        self.connect_headers = dict(headers or {})
        if self._connect_error is not None:
            raise self._connect_error
        return self.ws

    async def close(self) -> None:
        self.closed = True


def _started(task_id: str = "tid") -> dict[str, Any]:
    return {"header": {"event": "task-started", "task_id": task_id}}


def _finished(task_id: str = "tid") -> dict[str, Any]:
    return {"header": {"event": "task-finished", "task_id": task_id}}


def _failed(
    task_id: str = "tid",
    code: str = "E001",
    message: str = "bad",
) -> dict[str, Any]:
    return {
        "header": {
            "event": "task-failed",
            "task_id": task_id,
            "error_code": code,
            "error_message": message,
        }
    }


def _sentence(
    text: str,
    *,
    sentence_end: bool = True,
    begin_time: int = 0,
    end_time: int | None = None,
) -> dict[str, Any]:
    return {
        "header": {"event": "result-generated"},
        "payload": {
            "output": {
                "sentence": {
                    "text": text,
                    "sentence_end": sentence_end,
                    "begin_time": begin_time,
                    "end_time": end_time if end_time is not None else begin_time + 1000,
                }
            }
        },
    }


def _client(
    session: _FakeSession,
    *,
    task_id: str = "tid",
    submit_timeout_s: float = 1.0,
    max_wait_s: float = 2.0,
    chunk_bytes: int = 4,
    chunk_interval_ms: int = 0,
    max_reconnect_retries: int = 0,
) -> FunASRRealtimeClient:
    return FunASRRealtimeClient(
        api_key="sk-test-key",
        session_factory=lambda: session,
        task_id_factory=lambda: task_id,
        chunk_bytes=chunk_bytes,
        chunk_interval_ms=chunk_interval_ms,
        submit_timeout_s=submit_timeout_s,
        max_wait_s=max_wait_s,
        max_reconnect_retries=max_reconnect_retries,
    )


# ── Tests ─────────────────────────────────────────────────────────


class TestHandshakeAndRunTask:
    async def test_authorization_header_uses_bearer(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        session.ws.push_event(_sentence("hi", begin_time=0))
        session.ws.push_event(_finished())
        client = _client(session)

        await client.transcribe(b"abcdefgh")

        assert session.connect_headers is not None
        assert session.connect_headers.get("Authorization") == "bearer sk-test-key"

    async def test_run_task_payload_structure(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        session.ws.push_event(_sentence("hello"))
        session.ws.push_event(_finished())
        client = _client(session)

        await client.transcribe(b"abcdefgh")

        run_task = session.ws.sent_json[0]
        assert run_task["header"]["action"] == "run-task"
        assert run_task["header"]["task_id"] == "tid"
        assert run_task["header"]["streaming"] == "duplex"
        payload = run_task["payload"]
        assert payload["task_group"] == "audio"
        assert payload["task"] == "asr"
        assert payload["function"] == "recognition"
        assert payload["model"] == "fun-asr-realtime"
        assert payload["parameters"] == {"format": "opus", "sample_rate": 16000}
        assert payload["input"] == {}

    async def test_audio_streamed_only_after_task_started(self):
        """task-started 出现前不应有任何 binary 帧被发出."""
        session = _FakeSession()

        async def feed_later() -> None:
            await asyncio.sleep(0.05)
            # 此时应仍未推流
            assert session.ws.sent_bytes == []
            session.ws.push_event(_started())
            session.ws.push_event(_sentence("ok"))
            session.ws.push_event(_finished())

        client = _client(session)
        await asyncio.gather(feed_later(), client.transcribe(b"abcdefgh"))

        # 推流完成后，字节必须全部送出
        assert b"".join(session.ws.sent_bytes) == b"abcdefgh"


class TestTranscriptAggregation:
    async def test_only_sentence_end_true_is_kept(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        session.ws.push_event(_sentence("你好", sentence_end=False, begin_time=0))
        session.ws.push_event(_sentence("你好世界", sentence_end=True, begin_time=0))
        session.ws.push_event(_finished())
        client = _client(session)

        result = await client.transcribe(b"xx")

        assert result.transcript == "你好世界"

    async def test_multiple_sentences_sorted_by_begin_time(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        # 乱序到达
        session.ws.push_event(_sentence("third", begin_time=3000))
        session.ws.push_event(_sentence("first", begin_time=1000))
        session.ws.push_event(_sentence("second", begin_time=2000))
        session.ws.push_event(_finished())
        client = _client(session)

        result = await client.transcribe(b"xxxx")

        assert result.transcript == "first second third"

    async def test_empty_transcript_raises_empty(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        session.ws.push_event(_finished())
        client = _client(session)

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"xx")
        assert excinfo.value.reason == "empty"


class TestFailureAndTimeout:
    async def test_task_failed_raises_task_failed(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        session.ws.push_event(_failed(code="E123", message="oops"))
        client = _client(session)

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"xx")
        assert excinfo.value.reason == "task_failed"
        assert "E123" in (excinfo.value.detail or "")

    async def test_ws_closed_mid_stream_raises_disconnect(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        session.ws.push_closed()
        client = _client(session)

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"xx")
        assert excinfo.value.reason == "disconnect"

    async def test_submit_timeout_when_no_task_started(self):
        session = _FakeSession()
        # 只推一个无关事件 —— 不推 task-started
        # （若什么都不推 receive 会一直挂；推一个 result-generated 让 receive 返回，
        #   客户端检测到 first event 不是 task-started 会直接报 submit）
        session.ws.push_event(_sentence("irrelevant"))
        client = _client(session, submit_timeout_s=0.5)

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"xx")
        assert excinfo.value.reason == "submit"

    async def test_submit_timeout_on_silence(self):
        """队列完全空时，submit_timeout_s 后报 submit."""
        session = _FakeSession()
        client = _client(session, submit_timeout_s=0.1)

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"xx")
        assert excinfo.value.reason == "submit"

    async def test_max_wait_timeout_closes_ws(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        # 永不送 task-finished
        client = _client(session, submit_timeout_s=1.0, max_wait_s=0.2)

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"abcdefgh")
        assert excinfo.value.reason == "timeout"
        # 终态必须关闭 ws（设计文档 §18.4）
        assert session.ws.closed is True

    async def test_ws_connect_failure_raises_ws_connect(self):
        session = _FakeSession(connect_error=OSError("refused"))
        client = _client(session)

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"xx")
        assert excinfo.value.reason == "ws_connect"


class TestLifecycle:
    async def test_ws_and_session_closed_after_success(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        session.ws.push_event(_sentence("ok"))
        session.ws.push_event(_finished())
        client = _client(session)

        await client.transcribe(b"xx")

        assert session.ws.closed is True
        assert session.closed is True

    async def test_ws_closed_even_on_failure(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        session.ws.push_event(_failed())
        client = _client(session)

        with pytest.raises(AsrFailure):
            await client.transcribe(b"xx")
        assert session.ws.closed is True
        assert session.closed is True

    async def test_empty_audio_raises_immediately(self):
        session = _FakeSession()
        client = _client(session)

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"")
        assert excinfo.value.reason == "empty"
        # 不应建立 WebSocket
        assert session.connect_url is None

    async def test_missing_api_key_rejected_at_construction(self):
        with pytest.raises(ValueError):
            FunASRRealtimeClient(api_key="")


class TestRetry:
    """max_reconnect_retries 语义（设计文档 §9 / §12.2 / §12.3）."""

    async def test_retry_on_ws_connect_then_success(self):
        """第一次握手失败、第二次成功 → 返回成功结果."""
        attempts: list[_FakeSession] = []

        good_session = _FakeSession()
        good_session.ws.push_event(_started())
        good_session.ws.push_event(_sentence("ok"))
        good_session.ws.push_event(_finished())

        def factory() -> _FakeSession:
            if not attempts:
                s = _FakeSession(connect_error=OSError("ECONNREFUSED"))
            else:
                s = good_session
            attempts.append(s)
            return s

        client = FunASRRealtimeClient(
            api_key="k",
            session_factory=factory,
            task_id_factory=lambda: "tid",
            chunk_bytes=4,
            chunk_interval_ms=0,
            submit_timeout_s=1.0,
            max_wait_s=2.0,
            max_reconnect_retries=1,
        )

        result = await client.transcribe(b"abcd")
        assert result.transcript == "ok"
        assert len(attempts) == 2

    async def test_retry_exhausted_surfaces_last_reason(self):
        """重试次数耗尽，沿最后一次失败抛 AsrFailure."""
        def factory() -> _FakeSession:
            return _FakeSession(connect_error=OSError("nope"))

        client = FunASRRealtimeClient(
            api_key="k",
            session_factory=factory,
            task_id_factory=lambda: "tid",
            chunk_interval_ms=0,
            submit_timeout_s=0.5,
            max_wait_s=1.0,
            max_reconnect_retries=2,
        )

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"abcd")
        assert excinfo.value.reason == "ws_connect"

    async def test_task_failed_is_not_retried(self):
        """task_failed 不属于可重试类型；第一次失败即抛."""
        count = 0

        def factory() -> _FakeSession:
            nonlocal count
            count += 1
            s = _FakeSession()
            s.ws.push_event(_started())
            s.ws.push_event(_failed(code="E999"))
            return s

        client = FunASRRealtimeClient(
            api_key="k",
            session_factory=factory,
            task_id_factory=lambda: "tid",
            chunk_interval_ms=0,
            submit_timeout_s=0.5,
            max_wait_s=1.0,
            max_reconnect_retries=3,
        )

        with pytest.raises(AsrFailure) as excinfo:
            await client.transcribe(b"abcd")
        assert excinfo.value.reason == "task_failed"
        assert count == 1  # 只尝试一次

    async def test_retry_on_disconnect(self):
        """disconnect 属于可重试类型."""
        attempts: list[_FakeSession] = []

        def factory() -> _FakeSession:
            if not attempts:
                s = _FakeSession()
                s.ws.push_event(_started())
                s.ws.push_closed()
            else:
                s = _FakeSession()
                s.ws.push_event(_started())
                s.ws.push_event(_sentence("retry-ok"))
                s.ws.push_event(_finished())
            attempts.append(s)
            return s

        client = FunASRRealtimeClient(
            api_key="k",
            session_factory=factory,
            task_id_factory=lambda: "tid",
            chunk_bytes=4,
            chunk_interval_ms=0,
            submit_timeout_s=1.0,
            max_wait_s=2.0,
            max_reconnect_retries=1,
        )

        result = await client.transcribe(b"abcd")
        assert result.transcript == "retry-ok"
        assert len(attempts) == 2


class TestResultFields:
    async def test_result_contains_task_id_and_duration(self):
        session = _FakeSession()
        session.ws.push_event(_started())
        session.ws.push_event(_sentence("hi"))
        session.ws.push_event(_finished())
        client = _client(session, task_id="abcd1234")

        result = await client.transcribe(b"xx", duration_ms=3500)

        assert result.task_id == "abcd1234"
        assert result.duration_ms == 3500
        assert result.provider == "aliyun_funasr_realtime"
        assert result.model == "fun-asr-realtime"
        assert result.transcript == "hi"
