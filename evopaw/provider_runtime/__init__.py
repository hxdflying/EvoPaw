"""Provider Runtime — 多 Provider 抽象层（P1）

把「provider 选择 / endpoint 解析 / 凭证读取 / capability 判断」从散落的
位置（main.py / claude_client.py / context_mgmt.py / indexer.py / config）
收敛到一个模块。

本阶段不替换任何调用方式，只新增读取代理；旧字段（agent.planner_model 等）
继续可读、可工作。
"""

from __future__ import annotations

from .capabilities import RuntimeFamily, supports_streaming, supports_tool_calls
from .models import ProviderSpec, ResolvedRuntime, RoleConfig
from .registry import (
    DEFAULT_PROVIDERS,
    build_registry,
    get_provider,
    list_providers,
)
from .resolve import (
    DEFAULT_ROLE_BINDINGS,
    DEFAULT_ROLE_MODELS,
    ResolveError,
    resolve_runtime,
)

__all__ = [
    "ProviderSpec",
    "ResolvedRuntime",
    "RoleConfig",
    "RuntimeFamily",
    "supports_streaming",
    "supports_tool_calls",
    "DEFAULT_PROVIDERS",
    "build_registry",
    "get_provider",
    "list_providers",
    "DEFAULT_ROLE_BINDINGS",
    "DEFAULT_ROLE_MODELS",
    "ResolveError",
    "resolve_runtime",
]
