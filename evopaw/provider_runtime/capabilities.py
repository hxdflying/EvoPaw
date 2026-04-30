"""Provider 能力矩阵（按协议族）

第一阶段只需「这一族协议是否支持 X」级别的查询。具体到某个模型的能力差异
（如 Qwen 模型是否支持 vision）由 ProviderSpec.supports_* 字段承载，本模块
仅按协议族给出默认值。
"""

from __future__ import annotations

from .models import ProviderSpec, RuntimeFamily

# 协议族级别的默认能力。若 ProviderSpec.supports_* 显式设值，则以 ProviderSpec 为准。
_FAMILY_DEFAULTS: dict[RuntimeFamily, dict[str, bool]] = {
    "claude_sdk_compat": {
        "streaming": True,
        "tool_calls": True,
        "vision": True,
        "prompt_caching": True,
    },
    "openai_chat": {
        "streaming": True,
        "tool_calls": True,
        "vision": False,  # 各 OpenAI-compatible provider 差异较大，按 ProviderSpec 覆盖
        "prompt_caching": False,
    },
    "anthropic_messages": {
        "streaming": True,
        "tool_calls": True,
        "vision": True,
        "prompt_caching": True,
    },
}


def supports_streaming(spec: ProviderSpec) -> bool:
    return spec.supports_streaming and _FAMILY_DEFAULTS[spec.runtime_family]["streaming"]


def supports_tool_calls(spec: ProviderSpec) -> bool:
    return spec.supports_tool_calls and _FAMILY_DEFAULTS[spec.runtime_family]["tool_calls"]


def supports_vision(spec: ProviderSpec) -> bool:
    # 三态语义：spec.supports_vision 显式 True/False 时优先；None 走 family default。
    # 其它 capability 仍用 logical AND（差异不大，未触发问题）。
    if spec.supports_vision is not None:
        return bool(spec.supports_vision)
    return _FAMILY_DEFAULTS[spec.runtime_family]["vision"]


def supports_prompt_caching(spec: ProviderSpec) -> bool:
    return spec.supports_prompt_caching and _FAMILY_DEFAULTS[spec.runtime_family]["prompt_caching"]
