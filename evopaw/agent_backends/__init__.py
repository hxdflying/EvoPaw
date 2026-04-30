"""AgentBackend 协议层（P2）—— 主 Agent 与 LLM runtime 之间的解耦层。

模块对外暴露：
- `AgentBackend / TurnRequest / TurnResult / StreamSink / Usage / ToolCall`
- `ProviderTransientError`（及兄弟异常）
- `get_backend(runtime)`：根据 ResolvedRuntime.runtime_family 返回对应 backend 单例。

P4 之后三族都已落地：
- `claude_sdk_compat` → ClaudeSDKCompatBackend（P2，封装现有 query() 路径）
- `openai_chat`        → OpenAIChatBackend（P3，httpx 直连 chat/completions）
- `anthropic_messages` → AnthropicMessagesBackend（P4，httpx 直连 /v1/messages）

均走懒加载单例：第一次 `get_backend()` 命中对应 family 时才 import 实现模块，
避免「未启用 X provider 的部署」也强制要求 X 的依赖可用。
"""

from __future__ import annotations

from evopaw.provider_runtime import ResolvedRuntime, RuntimeFamily

from .base import (
    AgentBackend,
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderMaxTurnsExceeded,
    ProviderRateLimited,
    ProviderTransientError,
    ProviderUnknownError,
    StreamSink,
    ToolCall,
    TurnRequest,
    TurnResult,
    Usage,
)


__all__ = [
    "AgentBackend",
    "ProviderAuthError",
    "ProviderInvalidRequest",
    "ProviderMaxTurnsExceeded",
    "ProviderRateLimited",
    "ProviderTransientError",
    "ProviderUnknownError",
    "StreamSink",
    "ToolCall",
    "TurnRequest",
    "TurnResult",
    "Usage",
    "get_backend",
    "register_backend",
]


# ──────────────────────────────────────────────────────────────────
# Backend registry
# ──────────────────────────────────────────────────────────────────
#
# 用懒加载策略：claude_sdk_compat backend 只有在第一次 get_backend() 命中时才会
# import claude_agent_sdk，避免「未启用 claude provider 的部署」也强制要求 SDK 可用。
#
# 注册表 _BACKEND_LOADERS 把 family 字面量 → 工厂闭包；新增 backend 改这一处即可。

from typing import Callable  # noqa: E402

_BACKEND_BY_FAMILY: dict[RuntimeFamily, AgentBackend] = {}


def _load_claude_sdk_compat() -> AgentBackend:
    from .claude_sdk import ClaudeSDKCompatBackend

    return ClaudeSDKCompatBackend()


def _load_openai_chat() -> AgentBackend:
    from .openai_chat import OpenAIChatBackend

    return OpenAIChatBackend()


def _load_anthropic_messages() -> AgentBackend:
    from .anthropic_messages import AnthropicMessagesBackend

    return AnthropicMessagesBackend()


_BACKEND_LOADERS: dict[RuntimeFamily, Callable[[], AgentBackend]] = {
    "claude_sdk_compat":   _load_claude_sdk_compat,
    "openai_chat":         _load_openai_chat,
    "anthropic_messages":  _load_anthropic_messages,
}


def register_backend(family: RuntimeFamily, backend: AgentBackend) -> None:
    """注册一个 backend。重复注册会覆盖。

    主要给测试用（注入 mock backend）；运行时由 `get_backend` 内部按需懒注册。
    """
    _BACKEND_BY_FAMILY[family] = backend


def get_backend(runtime: ResolvedRuntime) -> AgentBackend:
    """根据 runtime.runtime_family 取对应 backend。

    Raises:
        NotImplementedError: 对应 backend 还未实现 / 未注册。
    """
    family = runtime.runtime_family

    backend = _BACKEND_BY_FAMILY.get(family)
    if backend is not None:
        return backend

    loader = _BACKEND_LOADERS.get(family)
    if loader is None:
        raise NotImplementedError(f"未知 runtime_family={family!r}")

    backend = loader()
    _BACKEND_BY_FAMILY[family] = backend
    return backend
