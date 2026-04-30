"""ResponseFinalizer —— 主 Agent 最终回复改写 pipeline。

`ResponseFinalizer` 在 backend 返回 `final_text` 之后、ctx/raw 持久化之前执行，
用于安全 redact、富文本前置清理、签名等最终响应处理。

Runner 对语音消息的二次包装在 finalizer 之后执行，不属于本模块职责。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResponseFinalizeContext:
    """传给 finalizer 的只读上下文。

    保持 frozen=True，避免 finalizer 误改 session_id 等关键字段。
    """

    session_id: str
    routing_key: str
    root_id: str
    skills_called: list[str] = field(default_factory=list)
    role: str = "main"


@runtime_checkable
class ResponseFinalizer(Protocol):
    """最终回复改写器协议。

    实现时：
    - 输入 `(text, context)`，返回新的 text；
    - 不应抛错；上层 `CompositeResponseFinalizer` 会捕获并降级为上一步文本，
      但 finalizer 自身仍应优先选择「无操作返回原文」而非依赖外层降级。
    """

    async def finalize(self, text: str, context: ResponseFinalizeContext) -> str: ...


class CompositeResponseFinalizer:
    """串行 pipe 多个 finalizer。

    - 任意 finalizer 抛错时记 warning 并沿用上一步文本，不阻断 pipeline。
    - 空列表也是合法构造，等价于 no-op finalizer，原文返回。

    实现 `ResponseFinalizer` Protocol（runtime_checkable，无需显式继承）。
    """

    def __init__(self, finalizers: list[ResponseFinalizer] | None = None) -> None:
        self._finalizers: list[ResponseFinalizer] = (
            list(finalizers) if finalizers else []
        )

    async def finalize(self, text: str, context: ResponseFinalizeContext) -> str:
        current = text
        for f in self._finalizers:
            try:
                current = await f.finalize(current, context)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "ResponseFinalizer %s 失败，沿用上一步文本",
                    type(f).__name__, exc_info=True,
                )
        return current
