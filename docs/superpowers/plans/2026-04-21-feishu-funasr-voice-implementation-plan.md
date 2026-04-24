# Feishu Fun-ASR Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 EvoPaw 增加飞书语音消息输入能力，使系统能够下载飞书 `audio` 消息、通过 OSS + 百炼 Fun-ASR 完成转写，并按“语音转写 + 回答”的格式回复用户。

**Architecture:** 保持现有 `FeishuListener -> Runner -> agent_fn -> FeishuSender` 主链路不变，在 `Runner` 进入 Agent 前新增语音预处理阶段。语音预处理由两个独立边界完成：`OssUploader` 负责文件中转，`FunASRClient` 负责百炼异步任务，`SpeechRecognitionService` 将两者编排成一个可直接被 `Runner` 调用的高层服务。

**Tech Stack:** Python 3.11+, `aiohttp`, `lark-oapi`, `prometheus_client`, `oss2`, `pytest`, `pytest-asyncio`

---

## File Structure

### Existing files to modify

- `evopaw/models.py`
  - 扩展 `Attachment`，支持 `audio` 和 `duration_ms`
- `evopaw/feishu/listener.py`
  - 解析飞书 `audio` 消息为标准化附件
- `evopaw/runner.py`
  - 新增语音转写编排、回执逻辑、正式回复格式
- `evopaw/main.py`
  - 从配置构建 `SpeechRecognitionService`
- `evopaw/observability/metrics.py`
  - 新增语音链路指标
- `config.yaml.template`
  - 新增 `asr` 与 `oss` 配置段
- `requirements.txt`
  - 新增 `oss2`
- `README.md`
  - 记录新环境变量与运行说明
- `tests/unit/test_feishu_listener.py`
  - 补齐 `audio` 解析测试
- `tests/unit/test_downloader.py`
  - 补齐音频下载测试
- `tests/unit/test_runner.py`
  - 补齐短语音、长语音、超时、失败测试
- `tests/unit/test_metrics.py`
  - 补齐新指标测试

### New files to create

- `evopaw/storage/__init__.py`
  - 存储层包入口
- `evopaw/storage/oss_uploader.py`
  - 上传、签名 URL、删除远端对象
- `evopaw/asr/__init__.py`
  - ASR 包入口
- `evopaw/asr/models.py`
  - `AsrTaskHandle`、`AsrResult`
- `evopaw/asr/funasr_client.py`
  - 提交任务、轮询状态、等待结果
- `evopaw/asr/service.py`
  - 上传 OSS + 调用 Fun-ASR 的高层编排
- `tests/unit/test_oss_uploader.py`
  - OSS 上传器单测
- `tests/unit/test_funasr_client.py`
  - Fun-ASR 客户端单测
- `tests/unit/test_asr_service.py`
  - ASR 服务层单测
- `tests/unit/test_main.py`
  - `main.py` 配置装配测试
- `tests/integration/test_feishu_audio_pipeline.py`
  - 音频消息到最终回复的集成测试

## Task 1: Normalize Audio Attachments at the Feishu Boundary

**Files:**
- Modify: `evopaw/models.py`
- Modify: `evopaw/feishu/listener.py`
- Modify: `tests/unit/test_feishu_listener.py`
- Modify: `tests/unit/test_downloader.py`

- [ ] **Step 1: Write the failing listener and downloader tests**

Add these tests to `tests/unit/test_feishu_listener.py` and `tests/unit/test_downloader.py`:

```python
def test_audio_with_key_returns_attachment(self):
    content = json.dumps({"file_key": "audio_001", "duration": 3200})
    result = FeishuListener._extract_attachment("audio", content)
    assert result is not None
    assert result.msg_type == "audio"
    assert result.file_key == "audio_001"
    assert result.file_name == "audio_001.audio"
    assert result.duration_ms == 3200


async def test_audio_download_success_returns_path(self, tmp_path):
    client = _make_client(data=b"opus-bytes")
    dl = FeishuDownloader(client=client, data_dir=tmp_path)
    att = _make_attachment(
        msg_type="audio",
        file_key="audio_001",
        file_name="audio_001.audio",
    )

    result = await dl.download("msg_audio_001", att, "s-audio")

    assert result is not None
    assert result.name == "audio_001.audio"
    assert result.read_bytes() == b"opus-bytes"
```

- [ ] **Step 2: Run the targeted tests and confirm they fail**

Run:

```bash
pytest tests/unit/test_feishu_listener.py::TestFeishuListenerExtractAttachment::test_audio_with_key_returns_attachment \
       tests/unit/test_downloader.py::TestFeishuDownloaderDownload::test_audio_download_success_returns_path -v
```

Expected:

- `test_audio_with_key_returns_attachment` fails because `_extract_attachment()` does not handle `audio`
- `test_audio_download_success_returns_path` fails because `_make_attachment()` type hints or attachment handling assume only `image/file`

- [ ] **Step 3: Extend the attachment model and listener implementation**

Update `evopaw/models.py`:

```python
@dataclass(frozen=True)
class Attachment:
    msg_type: str  # "image" | "file" | "audio"
    file_key: str
    file_name: str
    duration_ms: int | None = None
```

Update the `audio` branch in `evopaw/feishu/listener.py`:

```python
if msg_type == "audio":
    file_key = data.get("file_key") or ""
    if not file_key:
        return None
    raw_duration = data.get("duration")
    try:
        duration_ms = int(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    return Attachment(
        msg_type="audio",
        file_key=file_key,
        file_name=f"{file_key}.audio",
        duration_ms=duration_ms,
    )
```

- [ ] **Step 4: Expand listener coverage for missing/invalid audio fields**

Add these tests to `tests/unit/test_feishu_listener.py`:

```python
def test_audio_without_key_returns_none(self):
    content = json.dumps({"duration": 1500})
    assert FeishuListener._extract_attachment("audio", content) is None


def test_audio_with_invalid_duration_sets_none(self):
    content = json.dumps({"file_key": "audio_002", "duration": "oops"})
    result = FeishuListener._extract_attachment("audio", content)
    assert result is not None
    assert result.duration_ms is None
```

- [ ] **Step 5: Run the updated unit tests and confirm they pass**

Run:

```bash
pytest tests/unit/test_feishu_listener.py tests/unit/test_downloader.py -v
```

Expected:

- Audio attachment parsing tests pass
- Existing image/file tests still pass

- [ ] **Step 6: Commit the Feishu boundary changes**

```bash
git add evopaw/models.py evopaw/feishu/listener.py tests/unit/test_feishu_listener.py tests/unit/test_downloader.py
git commit -m "feat: add feishu audio attachment parsing"
```

## Task 2: Add OSS Uploading with Signed URLs

**Files:**
- Create: `evopaw/storage/__init__.py`
- Create: `evopaw/storage/oss_uploader.py`
- Create: `tests/unit/test_oss_uploader.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Write the failing OSS uploader tests**

Create `tests/unit/test_oss_uploader.py` with:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from evopaw.storage.oss_uploader import OssUploader


async def test_upload_file_returns_object_key_and_signed_url(tmp_path):
    local_file = tmp_path / "clip.audio"
    local_file.write_bytes(b"voice")

    bucket = MagicMock()
    bucket.sign_url.return_value = "https://oss.example.com/signed"

    uploader = OssUploader(bucket=bucket, key_prefix="evopaw/voice", signed_url_ttl_s=3600)

    result = await uploader.upload_file(local_file, session_id="s-1", msg_id="m-1")

    assert result.object_key.startswith("evopaw/voice/")
    assert result.url == "https://oss.example.com/signed"


async def test_delete_object_calls_bucket_delete_object():
    bucket = MagicMock()
    uploader = OssUploader(bucket=bucket, key_prefix="evopaw/voice", signed_url_ttl_s=3600)

    await uploader.delete_object("evopaw/voice/2026/04/21/s-1/m-1-clip.audio")

    bucket.delete_object.assert_called_once_with("evopaw/voice/2026/04/21/s-1/m-1-clip.audio")
```

- [ ] **Step 2: Run the OSS uploader tests and confirm import failure**

Run:

```bash
pytest tests/unit/test_oss_uploader.py -v
```

Expected:

- FAIL with `ModuleNotFoundError: No module named 'evopaw.storage'`

- [ ] **Step 3: Add the dependency and create the uploader implementation**

Append to `requirements.txt`:

```text
oss2>=2.18.0
```

Create `evopaw/storage/oss_uploader.py`:

```python
from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UploadedObject:
    object_key: str
    url: str


class OssUploader:
    def __init__(self, bucket, key_prefix: str, signed_url_ttl_s: int) -> None:
        self._bucket = bucket
        self._key_prefix = key_prefix.strip("/")
        self._signed_url_ttl_s = signed_url_ttl_s

    async def upload_file(self, local_file: Path, session_id: str, msg_id: str) -> UploadedObject:
        object_key = self._build_object_key(local_file, session_id=session_id, msg_id=msg_id)
        await asyncio.to_thread(self._bucket.put_object_from_file, object_key, str(local_file))
        url = await asyncio.to_thread(
            self._bucket.sign_url,
            "GET",
            object_key,
            self._signed_url_ttl_s,
            slash_safe=True,
        )
        return UploadedObject(object_key=object_key, url=url)

    async def delete_object(self, object_key: str) -> None:
        await asyncio.to_thread(self._bucket.delete_object, object_key)

    def _build_object_key(self, local_file: Path, session_id: str, msg_id: str) -> str:
        now = dt.datetime.utcnow()
        return (
            f"{self._key_prefix}/{now:%Y/%m/%d}/{session_id}/"
            f"{msg_id}-{local_file.name}"
        )
```

- [ ] **Step 4: Add the package entry point**

Create `evopaw/storage/__init__.py`:

```python
"""Storage helpers for EvoPaw."""

from .oss_uploader import OssUploader, UploadedObject

__all__ = ["OssUploader", "UploadedObject"]
```

- [ ] **Step 5: Run the OSS uploader tests and confirm they pass**

Run:

```bash
pytest tests/unit/test_oss_uploader.py -v
```

Expected:

- PASS

- [ ] **Step 6: Commit the OSS uploader**

```bash
git add requirements.txt evopaw/storage/__init__.py evopaw/storage/oss_uploader.py tests/unit/test_oss_uploader.py
git commit -m "feat: add oss uploader for audio transcription"
```

## Task 3: Add the Fun-ASR Client and Result Models

**Files:**
- Create: `evopaw/asr/__init__.py`
- Create: `evopaw/asr/models.py`
- Create: `evopaw/asr/funasr_client.py`
- Create: `tests/unit/test_funasr_client.py`

- [ ] **Step 1: Write the failing Fun-ASR client tests**

Create `tests/unit/test_funasr_client.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from evopaw.asr.funasr_client import FunASRClient


async def test_submit_returns_task_handle():
    session = MagicMock()
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"output": {"task_id": "task-123"}})
    session.post.return_value.__aenter__.return_value = response

    client = FunASRClient(
        api_key="test-key",
        model="fun-asr",
        session=session,
        submit_timeout_s=10,
        query_timeout_s=10,
        poll_interval_s=0.01,
    )

    handle = await client.submit("https://oss.example.com/audio.wav")

    assert handle.task_id == "task-123"
    assert handle.audio_url == "https://oss.example.com/audio.wav"


async def test_wait_for_result_returns_transcript():
    session = MagicMock()
    task_response = MagicMock()
    task_response.status = 200
    task_response.json = AsyncMock(
        side_effect=[
            {"output": {"task_status": "RUNNING"}},
            {
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"transcription_url": "https://dashscope-result.example.com/task-123.json"}],
                }
            },
        ]
    )
    result_response = MagicMock()
    result_response.status = 200
    result_response.json = AsyncMock(
        return_value={
            "transcripts": [
                {
                    "text": "你好，EvoPaw",
                    "sentences": [{"text": "你好，EvoPaw"}],
                }
            ]
        }
    )
    session.post.return_value.__aenter__.return_value = task_response
    session.get.return_value.__aenter__.return_value = result_response

    client = FunASRClient(
        api_key="test-key",
        model="fun-asr",
        session=session,
        submit_timeout_s=10,
        query_timeout_s=10,
        poll_interval_s=0.01,
    )

    result = await client.wait_for_result("task-123", timeout_s=1.0)

    assert result.transcript == "你好，EvoPaw"
```

- [ ] **Step 2: Run the client tests and confirm import failure**

Run:

```bash
pytest tests/unit/test_funasr_client.py -v
```

Expected:

- FAIL with `ModuleNotFoundError: No module named 'evopaw.asr'`

- [ ] **Step 3: Create the ASR result models**

Create `evopaw/asr/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AsrTaskHandle:
    task_id: str
    audio_url: str
    provider: str
    model: str


@dataclass(frozen=True)
class AsrResult:
    transcript: str
    task_id: str
    provider: str
    model: str
    duration_ms: int | None = None
```

- [ ] **Step 4: Implement the Fun-ASR client with submit and polling**

Create `evopaw/asr/funasr_client.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any

from evopaw.asr.models import AsrResult, AsrTaskHandle


class FunASRClient:
    SERVICE_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
    TASK_URL_TEMPLATE = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    def __init__(
        self,
        api_key: str,
        model: str,
        session,
        submit_timeout_s: float,
        query_timeout_s: float,
        poll_interval_s: float,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._session = session
        self._submit_timeout_s = submit_timeout_s
        self._query_timeout_s = query_timeout_s
        self._poll_interval_s = poll_interval_s

    async def submit(self, file_url: str) -> AsrTaskHandle:
        payload = {"model": self._model, "input": {"file_urls": [file_url]}}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        async with self._session.post(self.SERVICE_URL, headers=headers, json=payload, timeout=self._submit_timeout_s) as resp:
            data = await resp.json()
        task_id = data["output"]["task_id"]
        return AsrTaskHandle(task_id=task_id, audio_url=file_url, provider="aliyun_funasr", model=self._model)

    async def wait_for_result(self, task_id: str, timeout_s: float) -> AsrResult:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"Fun-ASR task timed out: {task_id}")
            data = await self._query_task(task_id)
            status = data["output"]["task_status"]
            if status == "SUCCEEDED":
                transcription_url = self._extract_transcription_url(data)
                transcription_data = await self._fetch_transcription(transcription_url)
                transcript = self._extract_transcript(transcription_data)
                if not transcript.strip():
                    raise RuntimeError(f"Fun-ASR returned empty transcript: {task_id}")
                return AsrResult(
                    transcript=transcript,
                    task_id=task_id,
                    provider="aliyun_funasr",
                    model=self._model,
                )
            if status in {"FAILED", "CANCELED"}:
                raise RuntimeError(f"Fun-ASR task failed: {task_id}")
            await asyncio.sleep(self._poll_interval_s)

    async def _query_task(self, task_id: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = self.TASK_URL_TEMPLATE.format(task_id=task_id)
        async with self._session.post(url, headers=headers, timeout=self._query_timeout_s) as resp:
            return await resp.json()

    async def _fetch_transcription(self, transcription_url: str) -> dict[str, Any]:
        async with self._session.get(transcription_url, timeout=self._query_timeout_s) as resp:
            return await resp.json()

    def _extract_transcription_url(self, data: dict[str, Any]) -> str:
        results = data.get("output", {}).get("results") or []
        if not results:
            raise RuntimeError("Fun-ASR result missing output.results")
        transcription_url = results[0].get("transcription_url") or ""
        if not transcription_url:
            raise RuntimeError("Fun-ASR result missing transcription_url")
        return transcription_url

    def _extract_transcript(self, data: dict[str, Any]) -> str:
        transcripts = data.get("transcripts") or []
        return "\n".join(
            item.get("text", "").strip()
            for item in transcripts
            if isinstance(item, dict) and item.get("text", "").strip()
        )
```

- [ ] **Step 5: Add the ASR package entry point**

Create `evopaw/asr/__init__.py`:

```python
"""ASR clients and orchestration helpers."""

from .funasr_client import FunASRClient
from .models import AsrResult, AsrTaskHandle

__all__ = ["AsrResult", "AsrTaskHandle", "FunASRClient"]
```

- [ ] **Step 6: Run the client tests and confirm they pass**

Run:

```bash
pytest tests/unit/test_funasr_client.py -v
```

Expected:

- PASS

- [ ] **Step 7: Commit the Fun-ASR client**

```bash
git add evopaw/asr/__init__.py evopaw/asr/models.py evopaw/asr/funasr_client.py tests/unit/test_funasr_client.py
git commit -m "feat: add funasr async client"
```

## Task 4: Add the High-Level SpeechRecognitionService

**Files:**
- Create: `evopaw/asr/service.py`
- Create: `tests/unit/test_asr_service.py`

- [ ] **Step 1: Write the failing service tests**

Create `tests/unit/test_asr_service.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from evopaw.asr.models import AsrResult, AsrTaskHandle
from evopaw.asr.service import SpeechRecognitionService
from evopaw.storage.oss_uploader import UploadedObject


async def test_transcribe_uploads_waits_and_deletes(tmp_path):
    local_file = tmp_path / "clip.audio"
    local_file.write_bytes(b"voice")

    uploader = MagicMock()
    uploader.upload_file = AsyncMock(
        return_value=UploadedObject(object_key="voice/key", url="https://oss.example.com/voice/key")
    )
    uploader.delete_object = AsyncMock()

    client = MagicMock()
    client.submit = AsyncMock(return_value=AsrTaskHandle("task-1", "https://oss.example.com/voice/key", "aliyun_funasr", "fun-asr"))
    client.wait_for_result = AsyncMock(return_value=AsrResult("你好，EvoPaw", "task-1", "aliyun_funasr", "fun-asr"))

    service = SpeechRecognitionService(uploader=uploader, client=client, max_wait_s=120)

    result = await service.transcribe(local_file, session_id="s-1", msg_id="m-1")

    assert result.transcript == "你好，EvoPaw"
    uploader.delete_object.assert_called_once_with("voice/key")


async def test_transcribe_deletes_remote_object_on_failure(tmp_path):
    local_file = tmp_path / "clip.audio"
    local_file.write_bytes(b"voice")

    uploader = MagicMock()
    uploader.upload_file = AsyncMock(
        return_value=UploadedObject(object_key="voice/key", url="https://oss.example.com/voice/key")
    )
    uploader.delete_object = AsyncMock()

    client = MagicMock()
    client.submit = AsyncMock(side_effect=RuntimeError("submit failed"))

    service = SpeechRecognitionService(uploader=uploader, client=client, max_wait_s=120)

    with pytest.raises(RuntimeError, match="submit failed"):
        await service.transcribe(local_file, session_id="s-1", msg_id="m-1")

    uploader.delete_object.assert_called_once_with("voice/key")
```

- [ ] **Step 2: Run the service tests and confirm import failure**

Run:

```bash
pytest tests/unit/test_asr_service.py -v
```

Expected:

- FAIL with `ModuleNotFoundError: No module named 'evopaw.asr.service'`

- [ ] **Step 3: Implement the orchestration service**

Create `evopaw/asr/service.py`:

```python
from __future__ import annotations

from pathlib import Path

from evopaw.asr.models import AsrResult


class SpeechRecognitionService:
    def __init__(self, uploader, client, max_wait_s: float) -> None:
        self._uploader = uploader
        self._client = client
        self._max_wait_s = max_wait_s

    async def transcribe(self, local_file: Path, session_id: str, msg_id: str) -> AsrResult:
        uploaded = await self._uploader.upload_file(local_file, session_id=session_id, msg_id=msg_id)
        try:
            handle = await self._client.submit(uploaded.url)
            return await self._client.wait_for_result(handle.task_id, timeout_s=self._max_wait_s)
        finally:
            await self._uploader.delete_object(uploaded.object_key)
```

- [ ] **Step 4: Run the service tests and confirm they pass**

Run:

```bash
pytest tests/unit/test_asr_service.py -v
```

Expected:

- PASS

- [ ] **Step 5: Commit the service layer**

```bash
git add evopaw/asr/service.py tests/unit/test_asr_service.py
git commit -m "feat: add speech recognition orchestration service"
```

## Task 5: Integrate Audio Transcription into Runner

**Files:**
- Modify: `evopaw/runner.py`
- Modify: `tests/unit/test_runner.py`

- [ ] **Step 1: Write the failing Runner tests for short audio, long audio, and failure**

Add these tests to `tests/unit/test_runner.py`:

```python
class FakeSpeechService:
    def __init__(self, transcript: str = "你好，EvoPaw", exc: Exception | None = None, delay: float = 0.0):
        self.transcript = transcript
        self.exc = exc
        self.delay = delay

    async def transcribe(self, local_file: Path, session_id: str, msg_id: str):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return type("Result", (), {"transcript": self.transcript})()


async def test_audio_short_message_replies_with_transcript_and_answer(tmp_path, session_mgr, mock_sender):
    downloader = MagicMock()
    downloader.download = AsyncMock(return_value=tmp_path / "workspace" / "sessions" / "s-1" / "uploads" / "clip.audio")
    speech_service = FakeSpeechService()

    runner = Runner(
        session_mgr=session_mgr,
        sender=mock_sender,
        agent_fn=echo_agent,
        downloader=downloader,
        speech_service=speech_service,
        idle_timeout=2.0,
    )

    inbound = make_inbound(content="", msg_id="om_audio_1")
    inbound.attachment = Attachment("audio", "audio_001", "clip.audio", 3000)

    await runner.dispatch(inbound)
    _, reply, _ = await mock_sender.wait_for_message()

    assert "语音转写：" in reply
    assert "你好，EvoPaw" in reply
    assert "回答：" in reply


async def test_audio_long_message_sends_ack_before_final_reply(tmp_path, session_mgr, mock_sender):
    downloader = MagicMock()
    downloader.download = AsyncMock(return_value=tmp_path / "clip.audio")
    speech_service = FakeSpeechService(delay=0.05)

    runner = Runner(
        session_mgr=session_mgr,
        sender=mock_sender,
        agent_fn=echo_agent,
        downloader=downloader,
        speech_service=speech_service,
        idle_timeout=2.0,
        audio_short_wait_s=0.01,
        audio_max_wait_s=1.0,
        audio_long_threshold_ms=1000,
    )

    inbound = make_inbound(content="", msg_id="om_audio_2")
    inbound.attachment = Attachment("audio", "audio_002", "clip.audio", 2000)

    await runner.dispatch(inbound)
    first = await mock_sender.wait_for_message()
    second = await mock_sender.wait_for_message()

    assert "正在转写和分析" in first[1]
    assert "语音转写：" in second[1]
```

- [ ] **Step 2: Run the targeted Runner tests and confirm they fail**

Run:

```bash
pytest tests/unit/test_runner.py::test_audio_short_message_replies_with_transcript_and_answer \
       tests/unit/test_runner.py::test_audio_long_message_sends_ack_before_final_reply -v
```

Expected:

- FAIL because `Runner.__init__()` does not accept `speech_service`
- FAIL because audio-specific helper logic does not exist

- [ ] **Step 3: Extend Runner construction and add audio helper methods**

Update `Runner.__init__()` in `evopaw/runner.py`:

```python
def __init__(
    self,
    session_mgr: SessionManager,
    sender: SenderProtocol,
    agent_fn: AgentFn | None = None,
    idle_timeout: float = 300.0,
    downloader: FeishuDownloader | None = None,
    speech_service=None,
    audio_short_wait_s: float = 10.0,
    audio_max_wait_s: float = 120.0,
    audio_long_threshold_ms: int = 15000,
    audio_ack_text: str = "语音已收到，正在转写和分析，请稍候。",
) -> None:
    self._speech_service = speech_service
    self._audio_short_wait_s = audio_short_wait_s
    self._audio_max_wait_s = audio_max_wait_s
    self._audio_long_threshold_ms = audio_long_threshold_ms
    self._audio_ack_text = audio_ack_text
```

Add helpers:

```python
def _build_audio_user_message(self, transcript: str, sandbox_path: str) -> str:
    return (
        "用户发送了一条语音消息。\n\n"
        f"语音转写：\n{transcript}\n\n"
        "原始音频文件已保存到：\n"
        f"`{sandbox_path}`\n\n"
        "请优先根据语音转写理解用户意图；如有歧义，可结合原始音频文件路径做进一步处理。"
    )


def _format_audio_reply(self, transcript: str, reply: str) -> str:
    return f"语音转写：\n{transcript}\n\n回答：\n{reply}"
```

- [ ] **Step 4: Implement the audio branch inside `_handle()`**

Replace the attachment pre-processing block with:

```python
user_content = inbound.content
reply_override: str | None = None

if inbound.attachment and self._downloader:
    sandbox_path = (
        f"/workspace/sessions/{session.id}/uploads/"
        f"{inbound.attachment.file_name}"
    )
    local_path = await self._downloader.download(inbound.msg_id, inbound.attachment, session.id)
    if local_path is None:
        user_content = f"[附件下载失败] {inbound.content}".strip()
    elif inbound.attachment.msg_type == "audio" and self._speech_service is not None:
        asr_task = asyncio.create_task(
            self._speech_service.transcribe(local_path, session_id=session.id, msg_id=inbound.msg_id)
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._audio_max_wait_s
        ack_sent = False
        should_ack_early = (
            inbound.attachment.duration_ms is not None
            and inbound.attachment.duration_ms > self._audio_long_threshold_ms
        )
        if should_ack_early:
            await self._sender.send_text(key, self._audio_ack_text, inbound.root_id)
            ack_sent = True
        try:
            if not ack_sent:
                asr_result = await asyncio.wait_for(asyncio.shield(asr_task), timeout=self._audio_short_wait_s)
            else:
                remaining = max(0.0, deadline - loop.time())
                asr_result = await asyncio.wait_for(asr_task, timeout=remaining)
        except asyncio.TimeoutError:
            if not ack_sent:
                await self._sender.send_text(key, self._audio_ack_text, inbound.root_id)
                ack_sent = True
            remaining = max(0.0, deadline - loop.time())
            asr_result = await asyncio.wait_for(asr_task, timeout=remaining)
        transcript = asr_result.transcript
        user_content = self._build_audio_user_message(transcript, sandbox_path)
        reply_override = transcript
    else:
        user_content = _build_attachment_message(sandbox_path=sandbox_path, original_text=inbound.content)
```

- [ ] **Step 5: Format final replies and failure fallbacks**

Wrap the agent call and final send block:

```python
try:
    reply = await self._agent_fn(
        user_content, history, session.id, inbound.routing_key, inbound.root_id, session.verbose
    )
except Exception:
    if reply_override is not None:
        reply = "处理出错，请稍后重试。"
    else:
        raise

if reply_override is not None:
    reply = self._format_audio_reply(reply_override, reply)
```

Add the audio failure fallback near the transcription branch:

```python
except TimeoutError:
    await self._sender.send(key, "语音转写超时，请稍后重试，或改发文字消息。", inbound.root_id)
    return
except Exception:
    await self._sender.send(key, "语音转写失败，请重试，或改发文字消息。", inbound.root_id)
    return
```

- [ ] **Step 6: Run the full Runner test module**

Run:

```bash
pytest tests/unit/test_runner.py -v
```

Expected:

- Existing slash command tests still pass
- New audio tests pass

- [ ] **Step 7: Commit the Runner integration**

```bash
git add evopaw/runner.py tests/unit/test_runner.py
git commit -m "feat: add audio transcription flow to runner"
```

## Task 6: Wire Config, Metrics, and Main Bootstrap

**Files:**
- Modify: `evopaw/main.py`
- Modify: `evopaw/observability/metrics.py`
- Modify: `tests/unit/test_metrics.py`
- Create: `tests/unit/test_main.py`
- Modify: `config.yaml.template`

- [ ] **Step 1: Write the failing bootstrap and metrics tests**

Create `tests/unit/test_main.py`:

```python
from unittest.mock import MagicMock

from evopaw.main import _build_speech_service


def test_build_speech_service_returns_none_when_disabled():
    cfg = {"asr": {"enabled": False}}
    assert _build_speech_service(cfg) is None


def test_build_speech_service_returns_service_when_enabled(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("OSS_BUCKET", "bucket")

    service = _build_speech_service(
        {
            "asr": {"enabled": True, "model": "fun-asr", "max_wait_s": 120, "submit_timeout_s": 10, "query_timeout_s": 10, "poll_interval_s": 1.0},
            "oss": {"enabled": True, "endpoint": "https://oss-cn-beijing.aliyuncs.com", "bucket": "bucket", "key_prefix": "evopaw/voice", "signed_url_ttl_s": 3600},
        }
    )

    assert service is not None
```

Extend `tests/unit/test_metrics.py`:

```python
from evopaw.observability.metrics import (
    export_metrics,
    observe_asr_latency,
    record_asr_request,
    record_audio_message,
    record_oss_upload_failure,
)


def test_audio_metrics_are_exported():
    record_audio_message()
    record_asr_request("aliyun_funasr", "success")
    observe_asr_latency(0.25)
    record_oss_upload_failure()
    data, _ = export_metrics()
    text = data.decode()
    assert "evopaw_audio_messages_total" in text
    assert "evopaw_asr_requests_total" in text
    assert "evopaw_asr_latency_seconds" in text
    assert "evopaw_oss_upload_failures_total" in text
```

- [ ] **Step 2: Run the bootstrap and metrics tests and confirm they fail**

Run:

```bash
pytest tests/unit/test_main.py tests/unit/test_metrics.py -v
```

Expected:

- FAIL because `_build_speech_service()` does not exist
- FAIL because new metrics helpers do not exist

- [ ] **Step 3: Implement the metrics helpers**

Add to `evopaw/observability/metrics.py`:

```python
audio_messages_total = Counter(
    "evopaw_audio_messages_total",
    "Number of audio messages received",
    registry=REGISTRY,
)

asr_requests_total = Counter(
    "evopaw_asr_requests_total",
    "Number of ASR requests",
    ["provider", "status"],
    registry=REGISTRY,
)

asr_latency_seconds = Histogram(
    "evopaw_asr_latency_seconds",
    "Latency of ASR transcription requests",
    registry=REGISTRY,
)

oss_upload_failures_total = Counter(
    "evopaw_oss_upload_failures_total",
    "Number of OSS upload failures",
    registry=REGISTRY,
)


def record_audio_message() -> None:
    audio_messages_total.inc()


def record_asr_request(provider: str, status: str) -> None:
    asr_requests_total.labels(provider=provider, status=status).inc()


def observe_asr_latency(seconds: float) -> None:
    asr_latency_seconds.observe(seconds)


def record_oss_upload_failure() -> None:
    oss_upload_failures_total.inc()
```

- [ ] **Step 4: Add a testable speech-service builder to `main.py`**

Add this helper to `evopaw/main.py`:

```python
def _build_speech_service(cfg: dict):
    asr_cfg = cfg.get("asr", {})
    if not asr_cfg.get("enabled", False):
        return None

    oss_cfg = cfg.get("oss", {})
    bucket_name = oss_cfg.get("bucket") or os.getenv("OSS_BUCKET", "")
    if not bucket_name:
        raise RuntimeError("OSS bucket is required when ASR is enabled")

    auth = oss2.Auth(os.environ["OSS_ACCESS_KEY_ID"], os.environ["OSS_ACCESS_KEY_SECRET"])
    bucket = oss2.Bucket(auth, oss_cfg["endpoint"], bucket_name)
    uploader = OssUploader(
        bucket=bucket,
        key_prefix=oss_cfg.get("key_prefix", "evopaw/voice"),
        signed_url_ttl_s=oss_cfg.get("signed_url_ttl_s", 3600),
    )
    session = aiohttp.ClientSession()
    client = FunASRClient(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        model=asr_cfg.get("model", "fun-asr"),
        session=session,
        submit_timeout_s=asr_cfg.get("submit_timeout_s", 10),
        query_timeout_s=asr_cfg.get("query_timeout_s", 10),
        poll_interval_s=asr_cfg.get("poll_interval_s", 1.0),
    )
    return SpeechRecognitionService(
        uploader=uploader,
        client=client,
        max_wait_s=asr_cfg.get("max_wait_s", 120),
    )
```

- [ ] **Step 5: Wire the new service into the main Runner and config template**

Update `config.yaml.template`:

```yaml
asr:
  enabled: true
  provider: "aliyun_funasr"
  model: "fun-asr"
  short_wait_s: 10
  max_wait_s: 120
  poll_interval_s: 1.0
  long_audio_threshold_ms: 15000
  submit_timeout_s: 10
  query_timeout_s: 10
  ack_text: "语音已收到，正在转写和分析，请稍候。"

oss:
  enabled: true
  endpoint: "https://oss-cn-beijing.aliyuncs.com"
  bucket: "${OSS_BUCKET}"
  key_prefix: "evopaw/voice"
  signed_url_ttl_s: 3600
```

Update the Runner construction in `evopaw/main.py`:

```python
speech_service = _build_speech_service(cfg)

runner = Runner(
    session_mgr=session_mgr,
    sender=sender,
    agent_fn=agent_fn,
    downloader=downloader,
    speech_service=speech_service,
    idle_timeout=idle_timeout,
    audio_short_wait_s=cfg.get("asr", {}).get("short_wait_s", 10.0),
    audio_max_wait_s=cfg.get("asr", {}).get("max_wait_s", 120.0),
    audio_long_threshold_ms=cfg.get("asr", {}).get("long_audio_threshold_ms", 15000),
    audio_ack_text=cfg.get("asr", {}).get("ack_text", "语音已收到，正在转写和分析，请稍候。"),
)
```

- [ ] **Step 6: Run the targeted tests and confirm they pass**

Run:

```bash
pytest tests/unit/test_main.py tests/unit/test_metrics.py -v
```

Expected:

- PASS

- [ ] **Step 7: Commit config and bootstrap wiring**

```bash
git add evopaw/main.py evopaw/observability/metrics.py tests/unit/test_main.py tests/unit/test_metrics.py config.yaml.template
git commit -m "feat: wire speech recognition service from config"
```

## Task 7: Add Integration Coverage and User-Facing Docs

**Files:**
- Create: `tests/integration/test_feishu_audio_pipeline.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_feishu_audio_pipeline.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from evopaw.runner import Runner
from evopaw.session.manager import SessionManager
from evopaw.models import Attachment, InboundMessage


class CaptureSender:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, routing_key, content, root_id):
        self.messages.append(("send", routing_key, content, root_id))

    async def send_text(self, routing_key, content, root_id):
        self.messages.append(("send_text", routing_key, content, root_id))

    async def send_thinking(self, routing_key, root_id):
        return None

    async def update_card(self, card_msg_id, content):
        self.messages.append(("update_card", card_msg_id, content))


class FakeSpeechService:
    async def transcribe(self, local_file: Path, session_id: str, msg_id: str):
        return type("Result", (), {"transcript": "你好，EvoPaw"})()


async def test_audio_message_is_written_to_history_and_replied(tmp_path):
    session_mgr = SessionManager(data_dir=tmp_path)
    sender = CaptureSender()
    downloader = MagicMock()
    local_path = tmp_path / "workspace" / "sessions" / "s-1" / "uploads" / "clip.audio"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(b"voice")
    downloader.download = AsyncMock(return_value=local_path)

    async def agent_fn(user_message, history, session_id, routing_key, root_id, verbose):
        assert "语音转写：" in user_message
        assert "你好，EvoPaw" in user_message
        return "收到，你想让我总结这段语音。"

    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        downloader=downloader,
        speech_service=FakeSpeechService(),
        idle_timeout=2.0,
    )

    inbound = InboundMessage(
        routing_key="p2p:ou_test",
        content="",
        msg_id="om_audio_3",
        root_id="om_audio_3",
        sender_id="ou_test",
        ts=1000,
        attachment=Attachment("audio", "audio_003", "clip.audio", 2800),
    )

    await runner.dispatch(inbound)
    await asyncio.sleep(0.1)

    assert any("语音转写：" in msg[2] for msg in sender.messages if msg[0] == "send")
```

- [ ] **Step 2: Run the integration test and confirm it fails**

Run:

```bash
pytest tests/integration/test_feishu_audio_pipeline.py -v
```

Expected:

- FAIL until the Runner audio flow and history formatting are fully integrated

- [ ] **Step 3: Update README with setup and operational notes**

Add this section to `README.md`:

```md
## Feishu Voice Input with Fun-ASR

EvoPaw can process Feishu `audio` messages by downloading the audio file, uploading it to OSS, and transcribing it with Alibaba Bailian Fun-ASR.

Required environment variables:

- `DASHSCOPE_API_KEY`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_BUCKET`

Recommended config:

- Keep OSS objects private and use signed URLs
- Set `oss.signed_url_ttl_s` to at least `asr.max_wait_s + 300`
- Validate real Feishu audio samples in staging before production rollout
```

- [ ] **Step 4: Run the integration test and one representative unit suite**

Run:

```bash
pytest tests/integration/test_feishu_audio_pipeline.py tests/unit/test_runner.py -v
```

Expected:

- PASS

- [ ] **Step 5: Commit the integration test and docs**

```bash
git add tests/integration/test_feishu_audio_pipeline.py README.md
git commit -m "test: add integration coverage for feishu audio pipeline"
```

## Task 8: Final Verification Sweep

**Files:**
- Modify: none
- Test: `tests/unit/test_feishu_listener.py`
- Test: `tests/unit/test_downloader.py`
- Test: `tests/unit/test_oss_uploader.py`
- Test: `tests/unit/test_funasr_client.py`
- Test: `tests/unit/test_asr_service.py`
- Test: `tests/unit/test_runner.py`
- Test: `tests/unit/test_main.py`
- Test: `tests/unit/test_metrics.py`
- Test: `tests/integration/test_feishu_audio_pipeline.py`

- [ ] **Step 1: Run the full targeted test matrix**

Run:

```bash
pytest \
  tests/unit/test_feishu_listener.py \
  tests/unit/test_downloader.py \
  tests/unit/test_oss_uploader.py \
  tests/unit/test_funasr_client.py \
  tests/unit/test_asr_service.py \
  tests/unit/test_runner.py \
  tests/unit/test_main.py \
  tests/unit/test_metrics.py \
  tests/integration/test_feishu_audio_pipeline.py -v
```

Expected:

- All targeted suites pass

- [ ] **Step 2: Run one lint-free import smoke test**

Run:

```bash
python -m compileall evopaw
```

Expected:

- No syntax errors

- [ ] **Step 3: Record staging validation checklist in the PR description or work log**

Use this exact checklist:

```md
- [ ] 3-second Chinese voice message returns one final reply with transcript + answer
- [ ] 20-second Chinese voice message sends ack first, then final reply
- [ ] Mixed Chinese/English voice message transcribes correctly enough for the agent to answer
- [ ] Unsupported or broken audio file returns the fallback error text
- [ ] No OSS object remains after a successful transcription
```

- [ ] **Step 4: Commit only if verification required code or docs fixes**

```bash
git status --short
```

Expected:

- No unexpected modified files
- If verification exposed issues, fix them first and then create a focused follow-up commit

## Self-Review

### Spec coverage

- Feishu `audio` 解析与下载：Task 1
- OSS 中转与签名 URL：Task 2
- Fun-ASR 提交、轮询、结果解析：Task 3
- 上传 + 转写 + 清理编排：Task 4
- Runner 的混合回执策略、最终回复格式、失败降级：Task 5
- 配置、指标、主进程装配：Task 6
- 集成测试与 README 运维说明：Task 7
- 总体验证：Task 8

### Placeholder scan

- 未使用 `TODO`、`TBD`、`implement later`
- 每个实现步骤都给出实际代码块
- 每个测试步骤都给出实际命令

### Type consistency

- `Attachment.duration_ms` 在 Task 1 定义，并在 Task 5 使用
- `AsrTaskHandle`、`AsrResult` 在 Task 3 定义，并在 Task 4/5 使用
- `SpeechRecognitionService.transcribe()` 在 Task 4 定义，并在 Task 5/6/7 使用
- `OssUploader.upload_file()` / `delete_object()` 在 Task 2 定义，并在 Task 4 使用
