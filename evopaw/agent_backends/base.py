"""AgentBackend 协议层 —— 主 Agent 与具体 LLM runtime 之间的最小公共接口。

本模块只定义协议、Pydantic 模型、异常类，不导入任何 LLM SDK。
- `TurnRequest` 是「一次主 Agent 轮次」的快照：runtime / system_prompt /
  user_content / cwd / max_turns / stream_sink / 私有 backend_hints。
  跨 provider 工具描述统一走 `backend_hints`（claude_sdk_compat 用
  `mcp_servers`，openai_chat / anthropic_messages 用 `skill_dispatcher`），
  不再单独保留 `tools` 字段。
- `TurnResult` 是统一的产物：text / skills_called / tool_calls / usage / raw。
- `StreamSink` 抽象 verbose 模式的事件推送，等价于现有 PreToolUse / PostToolUse
  钩子；具体 backend 内部负责把 SDK 钩子或 SSE 增量事件转换成 `on_tool_use(...)` /
  `on_tool_result(...)` 调用。
- `AgentBackend` 是单一入口 `async run_turn(req) -> TurnResult`。
- `backend_hints` 是「私有透传」字段：claude_sdk_compat 用来携带
  `mcp_servers={"evopaw": <server>}`；openai_chat / anthropic_messages 携带
  `skill_dispatcher`。其它 backend 各自约定 key，不互相消费。

异常归一化只放最小集合（`ProviderTransientError / ProviderInvalidRequest /
ProviderAuthError / ProviderRateLimited / ProviderUnknownError`），避免过度设计。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from evopaw.provider_runtime import ResolvedRuntime, RuntimeFamily

__all__ = [
    "AgentBackend",
    "ProviderAuthError",
    "ProviderInvalidRequest",
    "ProviderMaxTurnsExceeded",
    "ProviderRateLimited",
    "ProviderTransientError",
    "ProviderUnknownError",
    "RuntimeFamily",
    "StreamSink",
    "ToolCall",
    "ToolDecision",
    "ToolGate",
    "TurnRequest",
    "TurnResult",
    "Usage",
]


# ──────────────────────────────────────────────────────────────────
# 工具调用记录 / 用量
# ──────────────────────────────────────────────────────────────────


class ToolCall(BaseModel):
    """模型一轮回复中触发的工具调用记录。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    input: dict = Field(default_factory=dict)
    # 工具结果（如有）；本阶段 ClaudeSDKCompatBackend 不强制填充。
    output: Any = None


class Usage(BaseModel):
    """统一 token 用量。各 backend 自己映射；缺失时填 0。"""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


# ──────────────────────────────────────────────────────────────────
# StreamSink —— 替代 verbose hooks 的运行时事件抽象
# ──────────────────────────────────────────────────────────────────


@runtime_checkable
class StreamSink(Protocol):
    """事件接收端。verbose 模式下由 backend 在工具调用前后回调。

    所有方法都是 async，调用方（backend）应当把异常吞掉以保护主流程
    （行为与现有 hooks.py 中 `build_verbose_hooks` 的 try/except 等价）。
    """

    async def on_tool_use(self, name: str, input_data: dict) -> None: ...

    async def on_tool_result(self, name: str, output: Any) -> None: ...


# ──────────────────────────────────────────────────────────────────
# ToolGate —— 受限工具调用拦截接口
# ──────────────────────────────────────────────────────────────────


class ToolDecision(BaseModel):
    """ToolGate.before_tool_use 的返回值。

    - 仅支持工具调用拦截（allow / block）与输入改写；
    - **不允许** mutate conversation messages（messages 不进入 ToolDecision 字段）；
    - block 时的 `reason` 文本会作为该次 tool_call 的结果回写到 messages，让 LLM
      知道为什么被拒绝；reason 缺省时 backend 用一段中性文本兜底。
    - rewritten_input 仅在 action=='allow' 时生效，None 表示沿用原 input。
    """

    model_config = ConfigDict(extra="forbid")

    action: str = Field(
        default="allow",
        description="'allow' 或 'block'；其它值视为 allow 兜底（不抛错）。",
    )
    reason: str = Field(default="", description="block 时回写给 LLM 的解释。")
    rewritten_input: dict | None = Field(
        default=None,
        description="action='allow' 时可选 rewrite；None 表示沿用原 input。",
    )


@runtime_checkable
class ToolGate(Protocol):
    """工具调用拦截 / 改写器。

    实现规约：
    - **只覆盖工具调用**：HTTP backend 在 dispatcher.dispatch(name, args) 之前调用，
      `claude_sdk_compat` backend 由 SDK 自管，不接入本 Protocol（source-level 保护）。
    - **block 必须有审计**：backend 在 block 路径上记 warning 日志 + 计数器；
      gate 本身只描述决策，不负责记录。
    - **异常被吞**：实现抛错时 backend 视为 allow，避免 gate bug 卡死主流程。
    - **不改 messages**：返回值无 messages 字段，避免拦截器直接改写对话历史。
    """

    async def before_tool_use(
        self, name: str, input_data: dict,
    ) -> ToolDecision: ...


# ──────────────────────────────────────────────────────────────────
# TurnRequest / TurnResult
# ──────────────────────────────────────────────────────────────────


class TurnRequest(BaseModel):
    """单次主 Agent 轮次的输入。"""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    role: str = Field(..., description="角色名，如 'main' / 'subagent'")
    runtime: ResolvedRuntime
    system_prompt: str
    # user_content 同时支持纯字符串与多模态 block 列表。
    user_content: str | list[dict]
    cwd: str
    max_turns: int = 50
    timeout_s: float = Field(
        default=120.0,
        gt=0,
        description=(
            "HTTP backend 单次请求超时秒数（httpx.AsyncClient 的总超时）。"
            "claude_sdk_compat backend 由 SDK 自管，不消费此字段。"
        ),
    )
    # 通用 generation 参数：HTTP backend (openai_chat / anthropic_messages) 直接消费；
    # claude_sdk_compat 由 SDK 自管，不消费这些字段。None 表示走 backend 内的默认值
    # （Anthropic Messages 必填 max_tokens，缺省时 backend 内回退到 4096）。
    max_tokens: int | None = Field(
        default=None,
        gt=0,
        description="生成上限 token 数；None=backend 默认值。",
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        description="采样温度；None=不在请求体里设置（让 provider 用其自身默认）。",
    )
    top_p: float | None = Field(
        default=None,
        gt=0.0,
        le=1.0,
        description="nucleus 采样 top_p；None=不在请求体里设置。",
    )
    stream_sink: StreamSink | None = Field(
        default=None,
        description="verbose 模式事件接收端；None 表示不推送。",
    )
    tool_gate: ToolGate | None = Field(
        default=None,
        description=(
            "工具调用拦截 / 改写器；HTTP backend 在 dispatch 前调用。"
            "claude_sdk_compat backend 不消费此字段（SDK 自管工具调用）。"
        ),
    )
    backend_hints: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "私有透传：当前用于把 Claude SDK MCP server 对象从 main_agent.py "
            "传到 ClaudeSDKCompatBackend，对其它 backend 不可见。"
        ),
    )


class TurnResult(BaseModel):
    """单次主 Agent 轮次的输出。"""

    model_config = ConfigDict(extra="forbid")

    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    skills_called: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    raw: dict = Field(
        default_factory=dict,
        description="backend 自留字段，调用方按需读取（不进入持久化）。",
    )


# ──────────────────────────────────────────────────────────────────
# AgentBackend Protocol
# ──────────────────────────────────────────────────────────────────


@runtime_checkable
class AgentBackend(Protocol):
    """单一入口：跑一次主 Agent 轮次。"""

    async def run_turn(self, req: TurnRequest) -> TurnResult: ...


# ──────────────────────────────────────────────────────────────────
# 异常归一化（最小集合，正式分类等 P3 / P4）
# ──────────────────────────────────────────────────────────────────


class ProviderTransientError(RuntimeError):
    """连接/超时/CLI 异常等可重试错误。"""


class ProviderInvalidRequest(RuntimeError):
    """请求体校验失败（4xx 中除 401/403/429 外）。"""


class ProviderAuthError(RuntimeError):
    """凭证缺失或鉴权失败（401 / 403）。"""


class ProviderRateLimited(RuntimeError):
    """被限流（429）。"""


class ProviderMaxTurnsExceeded(RuntimeError):
    """工具调用循环达到 ``TurnRequest.max_turns`` 仍未收敛到 final text。

    与「provider 返回空回复」分开归类，便于：
      - metrics outcome 用 ``max_turns_exceeded``
      - 主 Agent 给用户更具针对性的提示（建议提高 max_turns 或缩小任务）
    """


class ProviderUnknownError(RuntimeError):
    """无法归类的错误。"""
