"""语音识别服务层.

1. 从本地路径读取音频字节。
2. 调用 :class:`FunASRRealtimeClient` 完成 one-shot 转写。
3. 负责日志字段统一（``task_id`` / ``elapsed_ms`` / ``file_path`` 等）。
4. 按 :mod:`evopaw.observability.metrics` 记录请求计数与时延。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from evopaw.asr.funasr_realtime_client import FunASRRealtimeClient
from evopaw.asr.models import AsrFailure, AsrResult
from evopaw.observability.metrics import record_asr_latency, record_asr_request

logger = logging.getLogger(__name__)


class SpeechRecognitionService:
    """对单条音频做同步 one-shot 转写.

    Runner 层通过 :meth:`transcribe_file` 获取 transcript；失败以
    :class:`AsrFailure` 形式抛出，Runner 负责映射到用户可见的降级文案。
    """

    def __init__(self, client: FunASRRealtimeClient) -> None:
        self._client = client
        self._provider = getattr(client, "_provider", "aliyun_funasr_realtime")

    async def transcribe_file(
        self,
        audio_path: Path,
        *,
        duration_ms: int | None = None,
    ) -> AsrResult:
        """转写指定本地音频文件.

        Raises:
            AsrFailure: 包含 ``reason`` 中的七种分类之一。
        """
        if not audio_path.exists():
            record_asr_request(self._provider, "download")
            raise AsrFailure(
                reason="download",
                detail=f"audio file not found: {audio_path}",
            )
        if not audio_path.is_file():
            record_asr_request(self._provider, "download")
            raise AsrFailure(
                reason="download",
                detail=f"audio path is not a regular file: {audio_path}",
            )

        try:
            audio_bytes = audio_path.read_bytes()
        except OSError as exc:
            record_asr_request(self._provider, "download")
            raise AsrFailure(
                reason="download",
                detail=f"read audio failed: {exc}",
            ) from exc

        if not audio_bytes:
            record_asr_request(self._provider, "download")
            raise AsrFailure(
                reason="download",
                detail=f"audio file is empty: {audio_path}",
            )

        started = time.monotonic()
        try:
            result = await self._client.transcribe(
                audio_bytes,
                duration_ms=duration_ms,
            )
        except AsrFailure as failure:
            elapsed = time.monotonic() - started
            record_asr_request(self._provider, failure.reason)
            record_asr_latency(self._provider, elapsed)
            logger.warning(
                "ASR 失败 reason=%s task_id=%s elapsed_ms=%d file=%s detail=%s",
                failure.reason,
                failure.task_id,
                int(elapsed * 1000),
                audio_path,
                failure.detail,
            )
            raise

        elapsed = time.monotonic() - started
        record_asr_request(self._provider, "success")
        record_asr_latency(self._provider, elapsed)
        logger.info(
            "ASR 成功 task_id=%s elapsed_ms=%d file=%s bytes=%d chars=%d",
            result.task_id,
            int(elapsed * 1000),
            audio_path,
            len(audio_bytes),
            len(result.transcript),
        )
        return result
