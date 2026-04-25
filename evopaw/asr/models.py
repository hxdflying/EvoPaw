"""ASR 数据契约.

对应设计文档 §10.2 / §10.3。本模块只定义纯数据类，不包含任何 I/O。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AsrResult:
    """一次 one-shot 转写的成功结果.

    transcript 是按 ``begin_time`` 升序拼接、仅保留 ``sentence_end == true``
    的句子文本。段间用单个空格连接。
    """

    transcript: str
    provider: str  # 例如 "aliyun_funasr_realtime"
    model: str  # 例如 "fun-asr-realtime"
    task_id: str  # run-task 请求时的 32 位十六进制 ID，仅用于日志
    duration_ms: int | None = None  # 来自飞书 audio 事件，非百炼回包


@dataclass(frozen=True)
class AsrFailure(Exception):
    """一次 one-shot 转写的标准化失败.

    reason 取值固定为下列之一（与设计文档 §12 一致）::

        "download"      — 本地音频不可读（由上层 service 分类）
        "ws_connect"    — WebSocket 握手失败
        "submit"        — run-task 发送后 submit_timeout_s 内未收到 task-started
        "task_failed"   — 服务端返回 task-failed 事件
        "timeout"       — 整次转写超过 max_wait_s
        "empty"         — task-finished 但 transcript 拼接结果为空
        "disconnect"    — WebSocket 中途异常关闭
    """

    reason: str
    detail: str | None = None
    task_id: str | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        suffix = f" ({self.detail})" if self.detail else ""
        return f"AsrFailure[{self.reason}]{suffix}"
