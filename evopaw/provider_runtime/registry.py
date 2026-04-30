"""Provider Registry — 内置 provider 清单 + 配置合并

设计取舍：
- 第一阶段只内置 `claude_sdk` / `anthropic` / `dashscope` 三个 ProviderSpec；
  其它（openrouter / moonshot / deepseek / openai / 自定义）等 P3 接入 OpenAIChatBackend
  时再补，避免在没有第二个真实 backend 之前过度设计。
- `build_registry()` 接受 config.yaml 中 `providers:` 块，与 DEFAULT 合并；
  config 中同名 provider 字段覆盖默认；config 中新名 provider 直接加入 registry。
"""

from __future__ import annotations

from typing import Mapping

from .models import ProviderSpec

# ──────────────────────────────────────────────────────────────────
# 内置 ProviderSpec（最小集合，第一阶段够用）
# ──────────────────────────────────────────────────────────────────

DEFAULT_PROVIDERS: dict[str, ProviderSpec] = {
    "claude_sdk": ProviderSpec(
        provider_id="claude_sdk",
        runtime_family="claude_sdk_compat",
        api_key_env=None,  # CLI 走 OAuth；如显式给 ANTHROPIC_API_KEY 也兼容，但非必须
        default_api_base=None,
        default_model="claude-sonnet-4-6",
        supports_vision=True,  # 显式声明（与 family default 一致）
        supports_tool_calls=True,
        supports_streaming=True,
        supports_prompt_caching=True,
    ),
    "anthropic": ProviderSpec(
        provider_id="anthropic",
        runtime_family="anthropic_messages",
        api_key_env="ANTHROPIC_API_KEY",
        default_api_base="https://api.anthropic.com",
        default_model="claude-sonnet-4-6",
        supports_vision=True,  # 显式声明
        supports_tool_calls=True,
        supports_streaming=True,
        supports_prompt_caching=True,
    ),
    "dashscope": ProviderSpec(
        provider_id="dashscope",
        runtime_family="openai_chat",
        api_key_env="QWEN_API_KEY",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen3-turbo",
        # DashScope OpenAI 兼容端不支持 image_url block（vision 模型走另一族 schema）；
        # 显式 False，避免 family default 解释。
        supports_vision=False,
        supports_tool_calls=True,
        supports_streaming=True,
        supports_prompt_caching=False,
        # DashScope 兼容 OpenAI 的 chat completions，但允许通过 extra_body
        # 注入 enable_thinking 控制 Qwen 思考模式（详见 §6.7）。
        extra_body_whitelist=frozenset({"enable_thinking"}),
        # memory_summary / memory_extract 等记忆角色历来要求关闭思考模式以加速摘要；
        # 这里作为 provider 级默认下沉，避免 memory 层 hardcode。
        default_extra_body={"enable_thinking": False},
    ),
}


# ──────────────────────────────────────────────────────────────────
# Registry 装配
# ──────────────────────────────────────────────────────────────────


def build_registry(
    providers_cfg: Mapping[str, Mapping] | None = None,
) -> dict[str, ProviderSpec]:
    """合并 DEFAULT_PROVIDERS 与 config.yaml 的 `providers:` 块。

    合并规则：
      - config 中同名 provider：把 config 字段覆盖到默认上（保留默认中未指定的字段）。
      - config 中新名 provider：必填 `runtime_family`，其余字段缺省。

    入参 `providers_cfg` 形如：
        {
          "dashscope": {"default_model": "qwen3-max"},
          "moonshot":  {"runtime_family": "openai_chat",
                        "api_key_env": "MOONSHOT_API_KEY",
                        "default_api_base": "https://api.moonshot.cn/v1"},
        }

    返回不可变字典语义：调用方不应直接修改返回值（虽未 freeze，但约定如此）。
    """
    registry: dict[str, ProviderSpec] = dict(DEFAULT_PROVIDERS)

    if not providers_cfg:
        return registry

    for name, raw in providers_cfg.items():
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            raise ValueError(
                f"providers.{name} 配置必须是 mapping，实际为 {type(raw).__name__}"
            )

        existing = registry.get(name)
        if existing is not None:
            # 同名：把已有字段当默认，config 字段覆盖
            merged = {**existing.model_dump(), **dict(raw), "provider_id": name}
            # frozenset 字段需单独转换，避免 list -> frozenset 序列化丢失
            if "extra_body_whitelist" in raw:
                merged["extra_body_whitelist"] = frozenset(raw["extra_body_whitelist"])
            registry[name] = ProviderSpec(**merged)
        else:
            data = {**dict(raw), "provider_id": name}
            if "extra_body_whitelist" in data:
                data["extra_body_whitelist"] = frozenset(data["extra_body_whitelist"])
            if "runtime_family" not in data:
                raise ValueError(
                    f"providers.{name} 是新 provider，必须显式提供 runtime_family"
                )
            registry[name] = ProviderSpec(**data)

    return registry


def get_provider(
    registry: Mapping[str, ProviderSpec],
    provider_id: str,
) -> ProviderSpec:
    """从 registry 取 provider，不存在抛 KeyError 并附完整列表辅助调试。"""
    try:
        return registry[provider_id]
    except KeyError as e:
        raise KeyError(
            f"未知 provider_id={provider_id!r}，已注册：{sorted(registry.keys())}"
        ) from e


def list_providers(
    registry: Mapping[str, ProviderSpec],
) -> list[str]:
    """列出 registry 中所有 provider_id（用于诊断 / metrics）."""
    return sorted(registry.keys())
