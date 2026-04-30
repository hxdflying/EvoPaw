"""Provider Runtime 数据模型

`ProviderSpec` —— Provider 静态 metadata（registry 内置 + config 覆盖合并后的最终视图）。
`ResolvedRuntime` —— 单次「角色解析」的产物：(provider_id, runtime_family, model, api_base, api_key)。
`RoleConfig` —— config.yaml 中 `roles.{role}` 块的解析结果。

设计取舍（参见 docs/multi-provider-final-plan-2026-04-27.md §4 §7）：
- `runtime_family` 用「协议族」而不是「品牌」，第一阶段三族即可（claude_sdk_compat /
  openai_chat / anthropic_messages）。新厂商接入只是 metadata，不改 backend 分支。
- `extra_body_whitelist` 借鉴 hermes #8591 教训：provider-specific 字段必须显式声明，
  避免在通用请求体上泄漏（如 OpenRouter `provider` 字段被推到 DashScope 请求里）。
- `ResolvedRuntime.api_key` 在序列化（log/metrics/error）时由调用方负责脱敏；
  本模型不内置脱敏逻辑，避免过度设计。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 第一阶段定义 3 个协议族即可。后续 P3/P4 再扩展（如 bedrock_converse、codex_responses）。
RuntimeFamily = Literal["claude_sdk_compat", "openai_chat", "anthropic_messages"]


class ProviderSpec(BaseModel):
    """Provider 静态 metadata。

    模型不包含运行时凭证（api_key），凭证仅通过 `api_key_env` 指向的环境变量读取，
    在 `resolve_runtime` 阶段才注入到 `ResolvedRuntime.api_key`。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str = Field(..., description="provider 唯一标识，如 'dashscope'")
    runtime_family: RuntimeFamily
    api_key_env: str | None = Field(
        default=None,
        description="API key 来源的环境变量名；claude_sdk_compat 可为空（CLI 自带 OAuth）",
    )
    default_api_base: str | None = None
    default_model: str | None = None

    # capabilities（第一阶段够用即可，避免过度设计）
    # `supports_vision` 三态：None=用 family default；True/False=显式覆盖。
    # 选择三态的原因：openai_chat family default vision=False（兼容端差异大），
    # 用 bool 默认 True 时所有新加 OpenAI-compatible provider 会被 logical AND
    # 自动压成 False，让显式 True 失效。其它 capability 字段差异不大，仍用 bool。
    supports_vision: bool | None = None
    supports_tool_calls: bool = True
    supports_streaming: bool = True
    supports_prompt_caching: bool = False

    # 出站请求体白名单：仅在 runtime_family=='openai_chat' 时生效；
    # 用来显式声明 provider-specific 字段（如 OpenRouter 的 `provider`、
    # DashScope 的 `enable_thinking`），避免误泄漏到通用 chat completions 请求。
    extra_body_whitelist: frozenset[str] = Field(default_factory=frozenset)

    # provider 级 extra_body 默认值。resolver 会按 `extra_body_whitelist` 过滤后，
    # 与 roles.{role}.extra_body 合并（role 覆盖 provider）。例如 DashScope 的
    # `enable_thinking=False` 用于关闭 Qwen 思考模式。
    default_extra_body: dict[str, Any] = Field(default_factory=dict)

    is_gateway: bool = Field(
        default=False,
        description="是否是网关型 provider（如 OpenRouter），用于 metrics 标签",
    )
    is_local: bool = Field(default=False, description="是否本地模型（如 vLLM）")


class RoleConfig(BaseModel):
    """`roles.{role}` 块的解析结果（来自 config.yaml）。

    所有字段都可选；缺省时由 resolver 走「显式参数 > 配置 > 环境变量 > provider 默认」
    优先级链填充。
    """

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    api_base: str | None = None
    api_key_env: str | None = Field(
        default=None,
        description="可覆盖 ProviderSpec.api_key_env，例如同一 provider 下用不同 key",
    )
    extra_body: dict[str, Any] | None = Field(
        default=None,
        description=(
            "provider-specific extra body，例如 DashScope 的 `enable_thinking`、"
            "OpenRouter 的 `provider`/`route`。resolver 会按 ProviderSpec.extra_body_whitelist "
            "过滤后注入到 ResolvedRuntime.extra_body；白名单外的字段会被丢弃并打 warning。"
        ),
    )


class ResolvedRuntime(BaseModel):
    """单次角色解析的最终视图。

    给 backend 用的一次性快照：模型 / 端点 / 凭证 / 协议族都已确定。
    序列化进入日志/metrics/error 时，调用方应当把 `api_key` 置 None。
    """

    model_config = ConfigDict(extra="forbid")

    role: str = Field(..., description="角色名，如 'main' / 'memory_summary'")
    provider_id: str
    runtime_family: RuntimeFamily
    model: str
    api_base: str | None = None
    api_key: str | None = Field(
        default=None,
        description="解析后的 api key；序列化前必须脱敏。claude_sdk_compat 通常为 None。",
    )

    # 透传 provider-specific extra body（已经过白名单）；backend 内部按需使用。
    extra_body: dict = Field(default_factory=dict)

    # capability 快照：resolver 计算时按 ProviderSpec + family default 落库，
    # 主链路（main_agent.py）不再重新 build_registry，直接读 ResolvedRuntime。
    supports_vision: bool = True

    def redacted(self) -> "ResolvedRuntime":
        """返回脱敏后的副本，可安全用于日志 / metrics / 错误报告。"""
        return self.model_copy(update={"api_key": None})
