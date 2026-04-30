"""SpeechRecognitionService 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from evopaw.asr.models import AsrFailure, AsrResult
from evopaw.asr.service import SpeechRecognitionService
from evopaw.observability.metrics import asr_latency_seconds, asr_requests_total


def _make_client(
    result: AsrResult | None = None,
    raise_failure: AsrFailure | None = None,
    provider: str = "aliyun_funasr_realtime",
) -> MagicMock:
    client = MagicMock()
    client._provider = provider  # SpeechRecognitionService 会读这个属性
    if raise_failure is not None:
        client.transcribe = AsyncMock(side_effect=raise_failure)
    else:
        client.transcribe = AsyncMock(
            return_value=result
            or AsrResult(
                transcript="你好世界",
                provider=provider,
                model="fun-asr-realtime",
                task_id="tid",
            )
        )
    return client


class TestTranscribeFile:
    async def test_reads_bytes_and_delegates_to_client(self, tmp_path):
        audio = tmp_path / "a.opus"
        audio.write_bytes(b"opus-bytes")
        client = _make_client()
        svc = SpeechRecognitionService(client)

        result = await svc.transcribe_file(audio, duration_ms=2000)

        assert result.transcript == "你好世界"
        client.transcribe.assert_awaited_once()
        call = client.transcribe.await_args
        assert call.args == (b"opus-bytes",)
        assert call.kwargs == {"duration_ms": 2000}

    async def test_duration_ms_defaults_to_none(self, tmp_path):
        audio = tmp_path / "a.opus"
        audio.write_bytes(b"data")
        client = _make_client()
        svc = SpeechRecognitionService(client)

        await svc.transcribe_file(audio)

        assert client.transcribe.await_args.kwargs == {"duration_ms": None}

    async def test_missing_file_raises_download_failure(self, tmp_path):
        client = _make_client()
        svc = SpeechRecognitionService(client)

        with pytest.raises(AsrFailure) as excinfo:
            await svc.transcribe_file(tmp_path / "nonexistent.opus")
        assert excinfo.value.reason == "download"
        client.transcribe.assert_not_awaited()

    async def test_directory_path_raises_download_failure(self, tmp_path):
        client = _make_client()
        svc = SpeechRecognitionService(client)

        with pytest.raises(AsrFailure) as excinfo:
            await svc.transcribe_file(tmp_path)
        assert excinfo.value.reason == "download"
        client.transcribe.assert_not_awaited()

    async def test_empty_file_raises_download_failure(self, tmp_path):
        audio = tmp_path / "empty.opus"
        audio.write_bytes(b"")
        client = _make_client()
        svc = SpeechRecognitionService(client)

        with pytest.raises(AsrFailure) as excinfo:
            await svc.transcribe_file(audio)
        assert excinfo.value.reason == "download"
        client.transcribe.assert_not_awaited()

    async def test_client_failure_propagates(self, tmp_path):
        audio = tmp_path / "a.opus"
        audio.write_bytes(b"data")
        client = _make_client(
            raise_failure=AsrFailure(reason="task_failed", detail="E001", task_id="t1"),
        )
        svc = SpeechRecognitionService(client)

        with pytest.raises(AsrFailure) as excinfo:
            await svc.transcribe_file(audio)
        assert excinfo.value.reason == "task_failed"
        assert excinfo.value.task_id == "t1"

    async def test_returns_same_result_object(self, tmp_path):
        audio = tmp_path / "a.opus"
        audio.write_bytes(b"data")
        expected = AsrResult(
            transcript="ok",
            provider="aliyun_funasr_realtime",
            model="fun-asr-realtime",
            task_id="my-task",
            duration_ms=1000,
        )
        client = _make_client(result=expected)
        svc = SpeechRecognitionService(client)

        result = await svc.transcribe_file(audio, duration_ms=1000)

        assert result is expected


def _counter_value(counter, **labels) -> float:
    """Prometheus Counter 读当前值（用于增量断言）."""
    return counter.labels(**labels)._value.get()


def _histogram_sum(hist, **labels) -> float:
    return hist.labels(**labels)._sum.get()


class TestMetrics:
    """服务层埋点：asr_requests_total / asr_latency_seconds。"""

    async def test_success_increments_success_counter_and_latency(self, tmp_path):
        audio = tmp_path / "a.opus"
        audio.write_bytes(b"data")
        client = _make_client()
        svc = SpeechRecognitionService(client)

        before_success = _counter_value(
            asr_requests_total, provider="aliyun_funasr_realtime", status="success"
        )
        before_sum = _histogram_sum(
            asr_latency_seconds, provider="aliyun_funasr_realtime"
        )

        await svc.transcribe_file(audio)

        after_success = _counter_value(
            asr_requests_total, provider="aliyun_funasr_realtime", status="success"
        )
        after_sum = _histogram_sum(
            asr_latency_seconds, provider="aliyun_funasr_realtime"
        )

        assert after_success - before_success == 1.0
        assert after_sum >= before_sum  # 至少观察到一次

    async def test_failure_increments_reason_labelled_counter(self, tmp_path):
        audio = tmp_path / "a.opus"
        audio.write_bytes(b"data")
        client = _make_client(
            raise_failure=AsrFailure(reason="timeout", detail="x", task_id="t")
        )
        svc = SpeechRecognitionService(client)

        before = _counter_value(
            asr_requests_total, provider="aliyun_funasr_realtime", status="timeout"
        )
        with pytest.raises(AsrFailure):
            await svc.transcribe_file(audio)
        after = _counter_value(
            asr_requests_total, provider="aliyun_funasr_realtime", status="timeout"
        )
        assert after - before == 1.0

    async def test_download_failure_increments_download_counter(self, tmp_path):
        """文件缺失属 'download' reason 一类，指标正确归类."""
        client = _make_client()
        svc = SpeechRecognitionService(client)

        before = _counter_value(
            asr_requests_total, provider="aliyun_funasr_realtime", status="download"
        )
        with pytest.raises(AsrFailure):
            await svc.transcribe_file(tmp_path / "does_not_exist.opus")
        after = _counter_value(
            asr_requests_total, provider="aliyun_funasr_realtime", status="download"
        )
        assert after - before == 1.0
