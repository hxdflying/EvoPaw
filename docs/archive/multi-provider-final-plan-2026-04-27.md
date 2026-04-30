---
status: completed
completed_at: 2026-04-28
rollout_summary: docs/archive/multi-provider-rollout-summary-2026-04-27.md
---

# EvoPaw 多 Provider 改造最终落地计划

日期：2026-04-27
作者：基于 `docs/hermes-vs-nanobot-multi-provider-analysis-2026-04-22.md` 审查后产出
取代：上述原计划文档（保留作为背景资料；执行时以本文为准）

> **状态**：P1–P5 全部已落地（2026-04-28）。本文保留原始计划与设计决策；
> 各阶段「实际落地结果」段落汇总见
> [`docs/archive/multi-provider-rollout-summary-2026-04-27.md`](archive/multi-provider-rollout-summary-2026-04-27.md)。
> 本文中保留的 ✅ 标记 + 落地段落是为了让读者无需跳转就能看到对应阶段的实施结果，
> 但若只想看「最终改动了什么」，建议直接读 rollout summary。

---

## 0. 阅读指引

- 第 1 节：原计划审查结论（哪些保留、哪些修正、哪些删除）。
- 第 2 节：当前 evopaw 对 Claude Agent SDK 的真实耦合面（已重新核对行号与位置）。
- 第 3 节：从 hermes-agent 与 nanobot 实际能借鉴什么（已基于 raw 代码核实）。
- 第 4 节：总体设计原则（核心抽象 + 角色化路由）。
- 第 5 节：分阶段实施路线（P1–P6，每阶段含目标 / 改动面 / 验收 / 回滚）。
- 第 6 节：跨阶段关注点（测试、配置迁移、可观测性、凭证、Skill 兼容）。
- 第 7 节：参考代码骨架（仅示意，不是最终签名）。
- 第 8 节：风险登记。
- 第 9 节：建议第一周可执行的具体动作。

---

## 1. 原计划审查结论

原文档主体方向正确，结论也务实：先做协议层 / registry，再做多 provider；保留 `claude_sdk_compat` 过渡层；按角色路由；最后再决定是否替换 Sub-Agent 运行时。但有几处需要修正或补强。

### 1.1 已核实属实的论断（保留）

- `NousResearch/hermes-agent` 仓库存在；`hermes_cli/runtime_provider.py` 中确实定义了 `_VALID_API_MODES = {chat_completions, codex_responses, anthropic_messages, bedrock_converse}` 和分层 resolver。
- `HKUDS/nanobot` 仓库存在（star/fork 量级显示是成熟项目）；`nanobot/providers/registry.py` 中 `ProviderSpec` 数据类、五种 backend（`openai_compat / anthropic / azure_openai / openai_codex / github_copilot`）确属实；`providers/base.py` 中 `LLMProvider / LLMResponse / ToolCallRequest / GenerationSettings` 接口属实；`agent/loop.py` 中 `AgentLoop` 接收 `provider: LLMProvider` 属实。
- 引用的 hermes issue 也存在：#8591（OpenRouter `provider` 字段泄漏到非 OR 请求）、#12381（TUI 路径 `_make_agent` 丢失 provider/api_mode）。这两个 issue 是「多 provider 系统天然容易出的 bug」的真实写照，原文档用它们做风险论据是合适的。

### 1.2 需要修正的事实性偏差

- **行号略偏**。原文档给出的若干行号是按旧版本写的，目前实际位置：
  - `evopaw/main.py` 中 CLI 检查在 `133-136` 行（不是 `79-85`）。
  - `evopaw/agents/main_agent.py` 中 SDK 导入在 `14-22`、`query()` 主循环在 `195` 附近。
  - **`evopaw/agents/skill_agent.py:46-54` 并不是 `Bash/Read/Write/Edit/Grep/Glob` 工具列表的真实定义点**——`skill_agent.py` 全文只有 76 行，且仅调用 `build_sub_agent_options`。真正写死工具白名单的位置是 `evopaw/llm/claude_client.py:82`。本文以此为准。
  - `evopaw/memory/context_mgmt.py` 中创建 OpenAI 兼容 client 的位置是 `107-134`；`evopaw/memory/indexer.py` 中是 `52-78`。
- **`config.yaml.template` 实际已有 `agent.planner_model` / `agent.sub_agent_model`** 两个字段，且确实没有 provider/registry/role 概念。这一点原文档说法正确，但只对了一半——还有 `asr.provider`（语音识别）和记忆层暗用 DashScope，整体上 evopaw 已经是「局部多 provider」，所以 P1 设计要把语音、记忆这些已有的 provider 用法也纳入统一抽象。

### 1.3 需要补强的薄弱点

原文档存在以下「在改造时一定会遇到、但没写清楚」的问题，本文最终计划全部补上：

1. **streaming / hooks 适配缺口**：现在 verbose 模式靠 Claude SDK 的 `PreToolUse / PostToolUse` 钩子推送飞书。OpenAI Chat Completions / Anthropic Messages 没有同名钩子，只有 streaming + tool_call 事件。需要明确「runtime-neutral 的事件总线」如何映射。
2. **工具协议互转的具体形态没写**：原文档提到 `openai_tools.py / anthropic_tools.py` 适配器，但没说 `task_context: str`（当前 SkillLoaderTool 的输入参数模式）如何映射到 OpenAI tools schema 的 `parameters: JSON Schema`。这是 P3 必须解决的。
3. **Sub-Agent 替换工作量被低估**：原文档说「再引入通用本地工具执行器」时只列了文件名。实际上 Claude SDK 自带的 Bash/Read/Write/Edit/Grep/Glob 涵盖权限模式、cwd 隔离、超时、bypass permissions 等语义，自造一套 = 从零写一个 mini code agent，工程量明显大于 provider resolver。本文把这一阶段标记为「可选 / 长期」，并给出明确的判断条件。
4. **配置迁移未提**：现有部署有大量用户在用 `agent.planner_model` / `agent.sub_agent_model`。新加 `providers / roles` 块时必须保持双写一段时间。本文加入显式的兼容/弃用窗口。
5. **Skill 跨 provider 行为没明确**：18 个 Skill 中，`feishu_ops / scheduler_mgr / search_memory / memory-governance / memory-save` 都假设运行在「能 spawn shell + 写文件」的 Sub-Agent 中。如果 Sub-Agent runtime 改了，这些 Skill 必须同步评估。本文在 P5 给出兼容矩阵。
6. **可观测性**：现有 `prometheus_client` 指标没有 `provider_id` / `runtime_family` 标签，多 provider 上线后 cost / latency 不可分。本文把这一项作为 P1 的强制改动。
7. **nanobot 的 AgentLoop 实际很重**：构造函数有 25+ 参数（MessageBus、workspace、mcp_servers、channels_config、hooks、cron_service…）。原文档把它简化为「只关心 provider」是误导。我们要学的是它的 **provider 抽象**，不是直接照搬 AgentLoop 的形态。

### 1.4 原文档可以删除或淡化的部分

- 「具体类示例」中给出的 Pydantic 字段过于细节（如 `token_param_style: Literal[...]`），在没看到第二个真实 backend 之前会过度设计。本文把这些示例标为「示意，最终以代码 PR 为准」。
- 「按角色路由」原文档建议直接顶层加 `roles:`。事实上 hermes 自身也不是这种形态，而是分散在 `auxiliary.vision / auxiliary.compression / delegation / fallback_model` 中。两种写法都可行；本文选用 hermes 风格但用 evopaw 自己的角色名（`main / subagent / memory_summary / memory_embedding / asr / vision / fallback`），避免硬抄一个 `roles:` 块。

---

## 2. 当前 evopaw 真实耦合面（核对版）

| 位置 | 耦合点 | 性质 |
|---|---|---|
| `evopaw/main.py:133-136` | 启动期 `check_claude_cli()` 不通过即退出 | 硬依赖 Claude Code CLI |
| `evopaw/agents/main_agent.py:14-22, 195` | 直接 `from claude_agent_sdk import query, ResultMessage, AssistantMessage, ToolUseBlock, CLINotFoundError, CLIConnectionError`，主循环 `async for message in query(...)` | 主 Agent runtime 等于 SDK |
| `evopaw/agents/skill_agent.py:12-17, 62` | 调用 `build_sub_agent_options + query()` | Sub-Agent runtime 等于 SDK |
| `evopaw/llm/claude_client.py:82` | `allowed_tools=["Bash","Read","Write","Edit","Grep","Glob"]` 写死 | Sub-Agent 工具集是 SDK 自带 |
| `evopaw/tools/skill_loader.py:25, 243-312` | `from claude_agent_sdk import create_sdk_mcp_server, tool` + `@tool` 装饰器 | 工具暴露层 = SDK MCP 形态 |
| `evopaw/agents/main_agent.py:148-158` | 图片仅生成 Claude 原生 image block | 多模态结构强绑 Anthropic 形态 |
| `evopaw/agents/hooks.py` | verbose 模式靠 PreToolUse/PostToolUse | 事件机制依赖 SDK |
| `config.yaml.template:17-24` | `agent.planner_model / sub_agent_model` 两个裸字符串 | 没有 provider 概念 |
| `evopaw/memory/context_mgmt.py:107-134` | OpenAI SDK + DashScope endpoint，模型名走 `EVOPAW_MEMORY_SUMMARY_MODEL` 环境变量 | 已是 OpenAI-compatible 路径 |
| `evopaw/memory/indexer.py:52-78` | OpenAI SDK + DashScope embedding | 同上 |
| `evopaw/asr/funasr_realtime_client.py` + `config.yaml.template:55-82` | `provider: aliyun_funasr_realtime` | 已是「按角色用不同 provider」的活例子 |

**结论**：核心对话路径完全绑在 Claude SDK 上；而记忆 / 向量 / 语音三条副线已经是多 provider。这一现实让「按角色路由」不是设计创新，而是把现状显式化。

---

## 3. 从 Hermes / Nanobot 实际能借鉴什么（精炼版）

### 3.1 Nanobot：核心抽象与 registry（学结构）

直接可借的：

- `ProviderSpec` 集中保存 provider metadata（`backend / default_api_base / detect_by_*` / `strip_model_prefix / supports_max_completion_tokens / supports_prompt_caching / model_overrides / thinking_style / reasoning_as_content / env_extras`）。
- 用 5 个 backend 类型（`openai_compat / anthropic / azure_openai / openai_codex / github_copilot`）覆盖几十家 provider。evopaw 第一阶段只需要前两个。
- `LLMResponse` 把 `content / tool_calls / finish_reason / usage / reasoning_content / thinking_blocks` 归一化。

不要直接抄的：

- `AgentLoop` 自身有 25+ 构造参数（MessageBus / workspace / cron_service / channels_config / mcp_servers / hooks / unified_session / disabled_skills / provider_snapshot_loader…）。它是一个完整 agent runtime，evopaw 已有自己的 Runner / SessionManager / CronService，不要替换。

### 3.2 Hermes：runtime resolver 与角色化（学行为）

直接可借的：

- `_VALID_API_MODES` 思想：用「协议族（api_mode / runtime_family）」而不是「品牌」做请求分流。evopaw 第一阶段定义 3 个协议族即可：`claude_sdk_compat / openai_chat / anthropic_messages`。
- 分层 resolver：显式参数 > 配置 > 环境变量 > 凭证池 > provider 默认 > URL 自动探测。
- 各「角色」独立配置自己的 provider/model/base_url，比如 `auxiliary.vision / auxiliary.compression / delegation / fallback_model`。

不要直接抄的：

- Hermes 把这种角色配置拆得很碎（auxiliary 下还嵌套），加上 OpenRouter / Bedrock / Codex 等专属字段，配置文件会很重。evopaw 应只保留 evopaw 自己用得上的角色。
- Hermes 的 issue #8591 / #12381 是真实警示：provider-specific 字段一旦泄漏到通用请求体就 400。下文 P3 把「字段白名单」作为强制要求。

---

## 4. 总体设计原则

1. **结构学 nanobot**：`ProviderSpec` registry + `LLMProvider`/`LLMResponse` 接口 + 少量 backend 类型承载多 provider。
2. **路由学 hermes**：每个角色独立解析自己的 `(provider_id, model, base_url, api_key)`，主对话和辅助任务允许走不同 provider。
3. **过渡保留 `claude_sdk_compat`**：第一版 backend 实现只是把现有 query() 调用包一层，不改行为。其它 backend 是「新增」，不是「替换」。
4. **协议级而非厂商级建模**：openai-compatible 端点、anthropic-messages 端点、claude-sdk-cli 三类。新厂商接入只是 metadata，不写新分支。
5. **请求体字段严格白名单**：每个 backend 出去的 HTTP body 只允许已知字段，避免 hermes #8591 类问题。
6. **Sub-Agent 长期保留 Claude SDK**：除非有充分理由，task 型 Skill 继续靠 SDK 自带的 Bash/Read/Write/Edit/Grep/Glob。本文不把「彻底替换 Sub-Agent」作为 must-have。
7. **可观测性先行**：在 P1 就给所有 LLM 调用打上 `provider_id / runtime_family / role` 三个标签。
8. **测试先于扩展**：每新增 backend，必须有 mock 层 + recording fixture，能在不连真模型的情况下回归。

---

## 5. 分阶段实施路线

为避免与项目自身的 Phase 0–9（CrewAI → Claude Agent SDK 迁移）混淆，本文用 **P1–P6** 表示多 provider 改造阶段。

### P1：Provider Runtime 抽象层（不改主能力，只加层）✅ **已完成（2026-04-28）**

> 落地摘要：新增 `evopaw/provider_runtime/` 模块（models / registry / capabilities / resolve）；
> `main.py` 启动期通过 `resolve_runtime("main"|"subagent", cfg)` 解析模型并按需触发 `check_claude_cli`；
> `memory/context_mgmt.py` 与 `memory/indexer.py` 增加 `configure_memory_runtime(cfg)` 注入点（未配置时
> 沿用旧路径，向后兼容）；`observability/metrics.py` 新增 `evopaw_llm_calls_total / input_tokens / output_tokens / latency_seconds`
> 四个指标，标签为 `provider_id / runtime_family / role`，并提供 `record_llm_call(...)` API；
> 新增 `tests/unit/test_provider_runtime.py` 共 45 个单测，全量套件 683 通过（原 638 + P1 新增 45）。


**目标**：把「provider 选择 / endpoint 解析 / 凭证读取 / capability 判断」从散落的位置（main.py / claude_client.py / context_mgmt.py / indexer.py / config）收敛到一个模块。

**新增模块**：

```
evopaw/provider_runtime/
├── __init__.py
├── models.py        # ProviderSpec / ResolvedRuntime / RequestPolicy
├── registry.py      # 内置 provider 列表（≥ anthropic / claude_sdk / openrouter / dashscope / moonshot / deepseek / openai / custom）
├── capabilities.py  # 协议族能力矩阵
└── resolve.py       # resolve_runtime(role, app_config) -> ResolvedRuntime
```

**配置改造**（保持向后兼容）：

```yaml
# 旧字段继续读，但标记为 deprecated（启动期 warning）
agent:
  planner_model: "claude-sonnet-4-6"
  sub_agent_model: "claude-haiku-4-5"

# 新字段（可选；不填则从旧字段推断）
providers:
  claude_sdk:
    runtime_family: claude_sdk_compat
    default_model: claude-sonnet-4-6
  anthropic:
    runtime_family: anthropic_messages
    api_key_env: ANTHROPIC_API_KEY
    default_api_base: https://api.anthropic.com
  dashscope:
    runtime_family: openai_chat
    api_key_env: QWEN_API_KEY
    default_api_base: https://dashscope.aliyuncs.com/compatible-mode/v1
  openrouter:
    runtime_family: openai_chat
    api_key_env: OPENROUTER_API_KEY
    default_api_base: https://openrouter.ai/api/v1

roles:
  main:              { provider: claude_sdk, model: claude-sonnet-4-6 }
  subagent:          { provider: claude_sdk, model: claude-haiku-4-5 }
  memory_summary:    { provider: dashscope,  model: qwen3-turbo }
  memory_embedding:  { provider: dashscope,  model: text-embedding-v3 }
  # asr / vision / fallback 留位，本阶段可不填
```

**改动面**（仅打通读取，不替换调用）：

- `main.py` / `claude_client.py` / `context_mgmt.py` / `indexer.py` 改为 `resolve_runtime(role, cfg)` 取 model/endpoint/api_key。
- `check_claude_cli()` 仍在，但只在「实际有角色解析到 `claude_sdk_compat`」时强制要求。
- `evopaw/observability/metrics_server.py` 增加 `provider_id / runtime_family / role` 三个标签。

**验收**：

1. 所有现有 496 单测通过。
2. 新增 ≥ 30 个单测，覆盖 registry 加载、resolver 优先级、capability 查询、deprecated 字段兼容。
3. `config.yaml` 不写新块时行为不变；写了新块时（仅 main/subagent 两个角色）行为不变。
4. Prometheus `evopaw_llm_calls_total{provider_id,runtime_family,role}` 指标可见。

**回滚**：本阶段全部为新增模块 + 字段读取代理，撤回新增模块即可恢复。

### P2：AgentBackend 协议层（保留 Claude SDK，改主 Agent 入口）✅ **已完成（2026-04-28）**

**目标**：把主 Agent 与 SDK 解耦，让 `main_agent.py` 不再直接 import `claude_agent_sdk`。

**接口（示意）**：

```python
class TurnRequest(BaseModel):
    role: str                        # "main" / "subagent"
    runtime: ResolvedRuntime
    system_prompt: str
    messages: list[ChatMessage]      # 归一化后的对话
    user_content: list[ContentPart]  # 文本 + 图片 + ...
    tools: list[ToolSpec]            # 工具规格（统一表示）
    cwd: str
    max_turns: int
    stream_sink: StreamSink | None   # 替代 verbose hooks

class TurnResult(BaseModel):
    text: str
    tool_calls: list[ToolCall]
    skills_called: list[str]
    usage: Usage
    raw: dict                        # 各 backend 自留
```

**实现**：

- `ClaudeSDKCompatBackend`：完全包装现有 `query()` 路径，hooks/MCP/options 都按现状构造。
- `main_agent.py` 改为：根据 `runtime.runtime_family` 选 backend → 调 `await backend.run_turn(req)`。
- `verbose hooks` 通过 `StreamSink` 抽象暴露，由 backend 内部把 `PreToolUse/PostToolUse` 转成 `StreamSink.on_tool_use(...)`。

**改动面**：`main_agent.py`、新增 `evopaw/agent_backends/{base.py, claude_sdk.py}`、`hooks.py` 适配 StreamSink。

**验收**：

1. 现有 496 单测全部通过。
2. 集成测试 `tests/integration/test_e2e_conversation.py` 在 Claude SDK 路径下行为不变。
3. 主 Agent `import` 表里不再包含 `claude_agent_sdk`（除了 `claude_sdk.py` 这一处）。
4. verbose 模式对 Skill 调用、Tool 调用的飞书推送行为完全不变。

**回滚**：分支级回滚（保留 P1）。

### P3：OpenAIChatBackend + 工具协议互转（首个真正非 SDK 的 backend）✅ **已完成（2026-04-28）**

**目标**：让 evopaw 主对话能直接跑通 OpenRouter / DashScope / Moonshot / DeepSeek / 本地 vLLM 这类 OpenAI-compatible 端点。

**新增**：

- `evopaw/agent_backends/openai_chat.py`：基于 `httpx` 直连 `/chat/completions`，支持 streaming（SSE）、tool_calls、usage。
- `evopaw/skills_runtime/`（从 `tools/skill_loader.py` 拆出，**纯逻辑无 SDK 依赖**）：
  - `registry.py`：`load_skills.yaml` + SKILL.md 解析（搬过来即可）
  - `instructions.py`：渐进式披露 + execution_directive 拼装
  - `dispatcher.py`：分发 reference / task / history_reader
  - `adapters/claude_mcp.py`：把 dispatcher 包成现有 MCP 工具（=今天的 skill_loader）
  - `adapters/openai_tools.py`：把 dispatcher 暴露为 OpenAI 工具调用 schema

**工具协议互转的关键决定**：

- `skill_loader` 当前对外是单工具（`{skill_name, task_context}`）。在 OpenAI 路径下保持这一形态——**不要**展开成 18 个独立工具，避免 prompt 工程被打散。
- `task_context` 当前是字符串（可 JSON-encoded）。在 OpenAI tools schema 里它就是 `{type: "object", properties: {skill_name: {type:"string"}, task_context: {type:"string"}}}`。
- Skill 能力清单（XML `<available_skills>`）继续注入到 system prompt（不依赖任何 SDK）。

**字段白名单**（学 hermes #8591 的教训）：

- `OpenAIChatBackend` 出站请求体只允许：`model / messages / tools / tool_choice / max_tokens / max_completion_tokens / temperature / top_p / stream / response_format`。
- OpenRouter 专属字段（`provider`, `route`, `transforms`）通过 `RequestPolicy.extra_body_whitelist` 白名单显式注入，且仅在 `provider_id == "openrouter"` 时生效。
- DashScope 专属 `extra_body.enable_thinking` 同样在 policy 中显式声明。

**改动面**：新增 backend / skills_runtime / 测试；`main_agent.py` 在 P2 已经可以替换 backend，本阶段只是新增分支。

**验收**：

1. 至少 2 个 OpenAI-compatible provider（OpenRouter + DashScope）跑通：
   - 纯文本对话（含历史）
   - 单轮 skill_loader 调用（reference 型）
   - 单轮 skill_loader 调用（task 型，触发 Sub-Agent；Sub-Agent 仍走 Claude SDK）
2. 集成测试新增 `tests/integration/test_openai_backend.py`，覆盖 streaming / tool_call / usage。
3. Prometheus 指标对两个 provider 可见 token 与 latency。
4. 所有现有 18 个 Skill 在 Claude 后端行为不变；reference 型 Skill 在 OpenAI 后端可正常返回。

**回滚**：默认 `roles.main.provider = claude_sdk`，新 backend 不影响默认路径。

### P4：AnthropicMessagesBackend + 多模态 content builder ✅ **已完成（2026-04-28）**

**目标**：把 Claude 自身从「等同于 Claude SDK」降级为「一种后端」，并修复多模态在多 provider 下的不一致。

**新增**：

- `evopaw/agent_backends/anthropic_messages.py`：直连 `https://api.anthropic.com/v1/messages`。
- `evopaw/content_builders/{claude_blocks.py, openai_blocks.py, anthropic_blocks.py}`：图片 / 文档 / 工具 result 的协议级序列化。
- `main_agent.py` 在拼装 `user_content` 前调用 `pick_content_builder(runtime_family)`。

**特别注意**：

- Anthropic 直连和 Claude SDK 不能混用 prompt cache 标记位，policy 里要区分。
- Anthropic 工具调用语义（`input_schema`）与 OpenAI（`parameters`）不同；P3 的 `ToolSpec` 抽象在这里要扩出 `to_anthropic()` / `to_openai()` 双视图。

**验收**：

1. Claude（通过 SDK）/ Anthropic 直连 / OpenAI 兼容 三种路径都能跑「图片 + skill_loader 调用」。
2. content builder 单测覆盖 PNG/JPEG/PDF（如有）输入路径。

### P5：skill_loader 全面适配 + Skill 兼容矩阵 ✅ **已完成（2026-04-28）**

**目标**：把 P3 留下的 `skills_runtime/` 与 dispatcher 真正落实到所有现有 Skill；明确每个 Skill 的跨 provider 兼容程度。

**Skill 矩阵**（建议在 `docs/skills-provider-matrix.md` 维护）：

| Skill | 类型 | 是否依赖 Sub-Agent shell 工具 | 跨 provider 状态 |
|---|---|---|---|
| pdf / docx / pptx / xlsx | task | 是（脚本调用 + 文件 IO） | 主 Agent 任意 provider；Sub-Agent 仍走 Claude SDK |
| feishu_ops / scheduler_mgr | task | 是（lark-oapi、cron 文件） | 同上 |
| tavily_search / arxiv_search / web_browse | task | 是（HTTP + 文件） | 同上 |
| memory-save / memory-governance / search_memory | task | 是（pgvector / 文件） | 同上 |
| skill-creator / daily-summary / investment-* | task | 是 | 同上 |
| history_reader | inline | 否 | 任意 provider 直接可用（dispatcher 内联返回） |

结论：P5 不要求 Sub-Agent 跨 provider，只要求**主 Agent 跨 provider 时所有 reference Skill 工作、所有 task Skill 仍能 fallback 到 Claude SDK Sub-Agent**。

**验收**：

1. 18 个 Skill 全部在 Claude 后端、OpenAI 后端、Anthropic 后端三种主 Agent 路径下完成一次端到端冒烟。
2. `tests/integration/test_skill_loader_e2e.py` 增加多后端参数化。
3. 文档矩阵入库。

### P6（可选 / 长期）：替换 Sub-Agent 运行时

**默认结论**：不做。除非满足以下任一条件：

- Claude SDK CLI 在生产稳定性出现持续问题（CLI 闪退 / 升级破坏性变更频繁）。
- 用其它 provider 跑 Sub-Agent 的成本/质量收益经实测 ≥ 30%。
- 需要在不能装 Claude CLI 的环境部署（受限网络 / 内网模型）。

**如果真的要做**：

1. 自建 `evopaw/execution_runtime/`：实现 `Bash / Read / Write / Edit / Grep / Glob` 六个工具，复用现有 cwd 隔离与超时机制；明确放弃 Claude SDK 的 `bypassPermissions` 语义，改为白名单 cwd + 命令超时。
2. 这一步的工程量评估为 ≥ 4 周（含权限模型、超时与子进程清理、跨平台兼容、与 18 个 Skill 的回归），单独立项。

---

## 6. 跨阶段关注点

### 6.1 配置兼容与迁移

- 旧字段（`agent.planner_model / agent.sub_agent_model`）保留至少两个发布周期。读取时如新字段缺省，用旧字段推断角色（`main / subagent` 默认 `claude_sdk` provider）。
- 启动期把「未声明 provider 块」「使用 deprecated 字段」打成 WARNING 日志（非 ERROR），让现有部署平滑过渡。
- `config.yaml.template` 增加新块的注释示例，但默认仍使用旧字段以避免破坏 docker compose 用户。

### 6.2 测试策略

- **单元**：registry / resolver / capability / content_builder / tools schema 转换。
- **集成（不连真模型）**：用 `respx` / `aioresponses` mock OpenAI 与 Anthropic 端点；提供 fixture 文件回放。
- **集成（连真模型）**：标 `pytest.mark.live`，CI 默认跳过；本地用 `pytest -m live` 触发。
- **跨 provider 参数化**：核心对话路径用 `@pytest.mark.parametrize("backend", [...])` 同一套断言跑三种 backend。
- **Skill 矩阵**：P5 的端到端冒烟脚本作为单独 stage，CI nightly 触发。

### 6.3 凭证与安全

- API key 仅通过 `api_key_env` 读取环境变量，**永远不写入 LLM context、不写入 `workspace/.config/`**（飞书凭证注入逻辑维持现状不变）。
- `ResolvedRuntime` 在序列化（日志/metrics/error report）时强制 `api_key=None`。
- OpenAI-compatible 端点的 `Authorization` 头由 backend 内部装配，`TurnRequest` 不携带凭证。

### 6.4 可观测性

- Prometheus 必须打的标签：`provider_id`（如 `openrouter`）、`runtime_family`（如 `openai_chat`）、`role`（如 `main`）。
- 至少四个指标：
  - `evopaw_llm_calls_total{provider_id,runtime_family,role,outcome}`
  - `evopaw_llm_input_tokens_total{...}` / `evopaw_llm_output_tokens_total{...}`
  - `evopaw_llm_latency_seconds_bucket{...}`
- `usage` 字段在 `LLMResponse` 上是「归一化字典」（`prompt_tokens / completion_tokens / total_tokens` 三键固定），由各 backend 自己映射。

### 6.5 错误归一化

- 每个 backend 必须把 HTTP/SDK 错误映射为统一异常类（`ProviderTransientError / ProviderInvalidRequest / ProviderRateLimited / ProviderAuthError / ProviderUnknownError`）。
- 这层是 hermes #12381 类问题的防线：上游报错时 Runner 能区分「应当重试 / 应当降级 fallback / 应当报告用户」。

### 6.6 与现有「ASR / 记忆」provider 的协调

- 记忆层 `_summarize_chunk` / `embed_texts` 改为 `runtime = resolve_runtime("memory_summary"|"memory_embedding", cfg)`，但保持 OpenAI 兼容协议不变（这两条调用路径已经是 OpenAI-compatible，本身不需要新 backend）。
- ASR 维持独立配置；P1 的 registry 不强制收编 ASR，只在文档中声明「ASR 走自己的 Fun-ASR client，不经过 LLM provider runtime」。

### 6.7 Skill 跨 provider 的注意事项

- task 型 Skill 的 `task_context` 是字符串。OpenAI 后端调用时，模型可能返回 JSON 对象（而非字符串）。dispatcher 内部要兼容 dict→json.dumps 的回退（这一点 `skill_loader.py:253` 已经做对，迁移时保持）。
- `feishu_ops` 等 Skill 当前依赖 `routing_key` 由 dispatcher 注入到 SKILL 指令里，多 provider 后保持注入点不变。

---

## 7. 参考代码骨架（仅示意，最终以 PR 为准）

```python
# evopaw/provider_runtime/models.py
from typing import Literal
from pydantic import BaseModel

RuntimeFamily = Literal["claude_sdk_compat", "openai_chat", "anthropic_messages"]

class ProviderSpec(BaseModel):
    provider_id: str
    runtime_family: RuntimeFamily
    api_key_env: str | None = None
    default_api_base: str | None = None
    default_model: str | None = None
    is_gateway: bool = False
    is_local: bool = False
    strip_model_prefix: bool = False
    supports_vision: bool = True
    supports_tool_calls: bool = True
    supports_prompt_caching: bool = False
    extra_body_whitelist: frozenset[str] = frozenset()

class ResolvedRuntime(BaseModel):
    provider_id: str
    runtime_family: RuntimeFamily
    model: str
    api_base: str | None
    api_key: str | None
    role: str

# evopaw/agent_backends/base.py
class AgentBackend(Protocol):
    async def run_turn(self, req: TurnRequest) -> TurnResult: ...

# evopaw/agent_backends/claude_sdk.py
class ClaudeSDKCompatBackend:
    async def run_turn(self, req: TurnRequest) -> TurnResult:
        # 现有 main_agent.py 主循环搬到这里
        ...

# evopaw/agent_backends/openai_chat.py
class OpenAIChatBackend:
    async def run_turn(self, req: TurnRequest) -> TurnResult:
        # httpx + SSE，工具调用按 OpenAI tools schema
        ...
```

> 上面字段会在实施时迭代，**不要把它们当成最终签名**。

---

## 8. 风险登记

| 风险 | 触发条件 | 影响 | 缓解 |
|---|---|---|---|
| Claude SDK CLI 突变 | SDK 升级 | P2/P5 全链路阻塞 | 锁定 SDK 版本到 requirements.txt；P2 完成后理论上 SDK 可降级为「可选依赖」 |
| OpenAI provider 字段泄漏（hermes #8591） | OpenRouter 专属字段进了 DashScope 请求体 | 400 / 失败率上升 | extra_body_whitelist + 单测覆盖 |
| 工具调用语义不一致 | OpenAI / Anthropic / SDK 三方对 `tool_call_id` 的命名要求不同 | 多轮对话拼接失败 | `ToolSpec` / `ToolCall` 用统一中性 id，转换在 backend 内部 |
| verbose hooks 在 OpenAI backend 缺失 | 飞书推送中间过程依赖 hook | 用户感知能力退化 | 用 SSE 增量事件触发 StreamSink，等价于 PreToolUse/PostToolUse |
| 配置迁移破坏现有用户 | 升级后 `agent.planner_model` 不识别 | 启动失败 | 旧字段保留两个 release；缺省路径不变 |
| Sub-Agent 跨 provider 的隐性退化 | 用户把 `roles.subagent.provider` 改成非 claude_sdk | Skill 全面失败 | P5 默认 subagent 角色锁定为 `claude_sdk_compat`，配置层需要显式 override 才允许其它值 |
| 多模态在 OpenAI 兼容端点上的差异 | 不同 provider 对 `image_url` 是否接受 base64 不一致 | 图片调用偶发失败 | content_builder 输出可同时携带 url 与 base64 fallback |
| 测试基础设施不足 | 没有 mock 层 | CI 不稳 / live 调用费钱 | P1 起就建立 `tests/_mocks/` + `respx` fixture |

---

## 9. 第一周可执行的具体动作

1. 直接看第2步骤。
2. 新建 `evopaw/provider_runtime/` 模块骨架（models / registry / resolve / capabilities），先只填 `claude_sdk` + `dashscope` + `anthropic` 三个 ProviderSpec。
3. 改 `evopaw/memory/context_mgmt.py` 与 `evopaw/memory/indexer.py`，让模型与 base_url 通过 `resolve_runtime("memory_summary" / "memory_embedding", cfg)` 取得；保留环境变量回退（`EVOPAW_MEMORY_SUMMARY_MODEL` 等），打 deprecation warning。
4. 改 `evopaw/llm/claude_client.py` 与 `evopaw/agents/main_agent.py`，让 `planner_model / sub_agent_model` 也走 resolver；不改 query() 调用方式。
5. 在 `evopaw/observability/` 增加 `provider_id / runtime_family / role` 标签。
6. 写 `tests/unit/test_provider_runtime.py`：
   - registry 加载 / 校验
   - resolver 优先级（显式 > 配置 > 环境 > 默认）
   - 旧字段兼容（仅写 `agent.planner_model` 仍能解析为 `roles.main`）
7. `docs/multi-provider-final-plan-2026-04-27.md`（本文）作为执行依据；提交 PR 时引用。

完成上面 7 步即视为 P1 落地，后续 P2 才进入主 Agent 入口的真实改造。

**P1 实际落地结果（2026-04-28）**：

- 新增模块：`evopaw/provider_runtime/{__init__,models,registry,capabilities,resolve}.py`，内置 `claude_sdk / anthropic / dashscope` 三个 ProviderSpec。
- `evopaw/main.py:130-167` 改造：先 `resolve_runtime("main"|"subagent", cfg)`，再仅在解析到 `claude_sdk_compat` 时强制 `check_claude_cli()`；`planner_model / sub_agent_model` 统一从 `ResolvedRuntime.model` 读出。
- `evopaw/memory/context_mgmt.py` 与 `evopaw/memory/indexer.py` 增加 `configure_memory_runtime(cfg)` 注入函数；`main.py` 启动期统一调用。未配置时模块沿用旧的 `EVOPAW_MEMORY_*_MODEL` + DashScope 硬编码端点路径，原有 patch 测试不受影响。
- `evopaw/observability/metrics.py` 增加 `evopaw_llm_calls_total / evopaw_llm_input_tokens_total / evopaw_llm_output_tokens_total / evopaw_llm_latency_seconds` 四个指标，标签为 `provider_id / runtime_family / role`，附带 `record_llm_call(...)` 辅助 API（指标在 P2 接入 backend 后实际打点）。
- `config.yaml.template` 新增 `providers / roles` 注释示例（默认仍走旧字段），不破坏现有 docker compose 用户。
- 新增 `tests/unit/test_provider_runtime.py` 共 45 个单测，覆盖：
  - `DEFAULT_PROVIDERS` × 3 与 frozen 行为
  - `build_registry` 默认 / 同名覆盖 / 新加 provider / 校验失败 / `extra_body_whitelist` list→frozenset
  - `capabilities` 协议族与 ProviderSpec 的逻辑与
  - `resolve_runtime` 优先级链（overrides > role > legacy > env > default）
  - 错误路径（未知角色、未知 provider、缺 model、role 配置非 mapping）
  - api_base / api_key 注入与 `redacted()` 脱敏
  - deprecation warning 一次性触发与不触发条件
- 全量单元测试 683 通过（原 638 + 本次 +45），无回归。

---

## 10. 第二周可执行的具体动作（P2：AgentBackend 协议层）✅ **已完成（2026-04-28）**

P1 已经把「角色 → ResolvedRuntime」打通，但主 Agent 主循环仍直接 `from claude_agent_sdk import query, ...`。
P2 的目标是：**主 Agent 不再 import `claude_agent_sdk`，只对一个抽象 `AgentBackend` 编程**。
本阶段不引入第二个真实 backend；`ClaudeSDKCompatBackend` 完全包住现有 `query()` 调用路径，
行为零变化。

下面 8 步对应 §9 的颗粒度（可独立 PR / 可滚动验收）：

1. 直接看第 2 步骤。
2. 新建 `evopaw/agent_backends/` 模块骨架：
   - `__init__.py` 暴露 `AgentBackend / TurnRequest / TurnResult / StreamSink / get_backend(runtime)`
   - `base.py` 定义协议（`AgentBackend` Protocol、`TurnRequest / TurnResult / ToolCall / Usage / ContentPart` Pydantic 模型、`StreamSink` Protocol）
   - 仅声明 / 不连任何 SDK；本步骤产物可独立编译并被新单测覆盖
3. 新建 `evopaw/agent_backends/claude_sdk.py`：
   - `ClaudeSDKCompatBackend.run_turn(req: TurnRequest) -> TurnResult`
   - 把 `evopaw/agents/main_agent.py:191-218`（Claude SDK 主循环 + skills_called 收集 + 异常归一化）整段搬过来
   - 内部仍用 `build_main_agent_options(...)` 与 `query(...)`；构造 options 时 `model = req.runtime.model`
   - 把 `final_text / tool_calls / skills_called / usage`（`ResultMessage` 上的字段）映射到 `TurnResult`
4. 新建 `evopaw/agent_backends/registry.py`（或在 `__init__.py` 内）：
   - `BACKEND_BY_FAMILY: dict[RuntimeFamily, AgentBackend]`
   - `get_backend(runtime: ResolvedRuntime) -> AgentBackend`：第一阶段只注册 `claude_sdk_compat`；
     `openai_chat / anthropic_messages` 在 P3 / P4 注册，本步骤遇到这两族抛 `NotImplementedError("等 P3/P4 实现")`
5. 改造 `evopaw/agents/hooks.py` 为 `StreamSink` 适配：
   - 新增 `class FeishuStreamSink(StreamSink)`：把 `on_tool_use(name, input)` / `on_tool_result(name, output)` 写成 `await sender.send_text(...)`
   - 保留 `build_verbose_hooks(callback)` 的旧函数签名（被现有测试 `test_hooks.py` 覆盖）；
     在 `claude_sdk.py` 内部把 `StreamSink` 适配为 PreToolUse / PostToolUse 字典
6. 改造 `evopaw/agents/main_agent.py`：
   - 删除 `from claude_agent_sdk import query, AssistantMessage, ResultMessage, ToolUseBlock, ...` 这一行
   - 在 `agent_fn` 内部把 system_prompt + history + user_content + tools + cwd 装进 `TurnRequest`
   - 调用 `backend = get_backend(main_runtime)` → `result = await backend.run_turn(req)`
   - skills_called / record_skills / ctx 持久化 / async_index_turn 这些「外围逻辑」保持原位
   - **关键**：`build_skill_loader_server(...)` 当前直接产出 SDK MCP server。本阶段不动它，
     只把它的 server 对象塞到 `TurnRequest.tools` 的「私有 backend hint」字段里，
     由 `ClaudeSDKCompatBackend` 内部再装进 `mcp_servers`；其它 backend 在 P3 才会用 `tools` 字段
7. 把 `build_agent_fn(..., planner_model=, sub_agent_model=, ...)` 的签名同步收口：
   - 改为接受 `main_runtime: ResolvedRuntime, sub_runtime: ResolvedRuntime`（`main.py` 已经在算这两个值）
   - 旧的 `planner_model` / `sub_agent_model` 入参保留（默认从 runtime 推断），但标 deprecated，方便老测试不改
8. 测试与验收：
   - 新增 `tests/unit/test_agent_backends_base.py`：`TurnRequest / TurnResult / Usage` 字段约束、`StreamSink` Protocol 契约
   - 新增 `tests/unit/test_claude_sdk_backend.py`：mock `query()` 的 async generator，覆盖：
     - 正常返回 → `TurnResult.text` 与 `skills_called` 正确
     - `CLINotFoundError / CLIConnectionError` → 归一化为 `ProviderTransientError`（异常归一化先在 backend 内最小集落地，正式分类等 P3）
     - verbose StreamSink 在 PreToolUse 触发时被调用
   - 现有 `tests/unit/test_main_agent.py` 不允许 import `claude_agent_sdk`（grep 校验）
   - 现有 496 + P1 45 + P2 新增 ≥ 25 单测 全部通过
   - 集成测试 `tests/integration/test_e2e_conversation.py`（如有）在 Claude SDK 路径下行为不变

**验收门槛**（与 §5 P2「验收」严格对齐）：
- [ ] `evopaw/agents/main_agent.py` 内 `import claude_agent_sdk` 出现 0 次
- [ ] `pytest tests/unit/ -q` 全绿
- [ ] verbose 模式飞书推送内容（PreToolUse / PostToolUse）字节级一致（即测对接 mock sender 后 captured 文本不变）
- [ ] 新增的 `evopaw/agent_backends/` 在 `pyproject.toml` / 构建上无新增依赖（仍只依赖 claude-agent-sdk + pydantic）

**回滚策略**：分支级回滚保留 P1（provider_runtime 模块、metrics 标签、memory 模块的 configure_runtime 入口）；
仅撤销 `agent_backends/` 与 `main_agent.py` 改动即可恢复。

**P2 实际落地结果（2026-04-28）**：

- 新增模块 `evopaw/agent_backends/{__init__,base,claude_sdk}.py`：
  - `base.py` 定义 `Usage / ContentPart / ChatMessage / ToolSpec / ToolCall / TurnRequest / TurnResult` Pydantic 模型（均 `extra="forbid"`），`StreamSink / AgentBackend` 两个 `@runtime_checkable` Protocol，以及 `ProviderTransientError / ProviderInvalidRequest / ProviderAuthError / ProviderRateLimited / ProviderUnknownError` 异常族（全部 `RuntimeError` 子类）。无 SDK 依赖，纯协议层。
  - `__init__.py` 暴露上述协议 + `register_backend(family, be)` + `get_backend(runtime)`：仅在 `runtime_family == "claude_sdk_compat"` 第一次访问时懒加载 `claude_sdk.py` 模块；`openai_chat / anthropic_messages` 抛 `NotImplementedError("等 P3/P4 实现")`。
  - `claude_sdk.py` 实现 `ClaudeSDKCompatBackend.run_turn(req)`：把 `main_agent.py:191-218` 的 query() 主循环 + skills_called 收集 + `(CLINotFoundError, CLIConnectionError) → ProviderTransientError` / 其它 `→ ProviderUnknownError` 异常归一化整段搬过来；通过 `req.backend_hints["mcp_servers"]` 透传 `skill_loader` SDK MCP server 对象（其它 backend 忽略此字段）；`_build_hooks_from_stream_sink(stream_sink)` 把 `StreamSink` 适配为 PreToolUse / PostToolUse 字典，行为字节级一致；成功 / 失败两条路径均调用 `record_llm_call(...)` 打 P1 落地的 metrics。
- `evopaw/agents/main_agent.py` 重写：删除 `from claude_agent_sdk import ...` 一行（grep `claude_agent_sdk` 在 `main_agent.py` 内已为 0 次 import 命中，仅剩 docstring 注释）；`build_agent_fn(...)` 新增 `main_runtime / sub_runtime: ResolvedRuntime | None` 可选 kwargs，`_build_default_runtime(role, model)` 在调用方仍只传旧的 `planner_model / sub_agent_model` 字符串时回退构造 `ResolvedRuntime`；`agent_fn` 内构造 `TurnRequest` 后调 `await get_backend(main_runtime).run_turn(req)`。
- `evopaw/agents/hooks.py` 新增 `class FeishuStreamSink`：`on_tool_use(name, input)` / `on_tool_result(name, output)` 文案（`💭 即将调用工具 X` / `✅ 工具 X 完成`）与原 `build_verbose_hooks` 字节级一致；保留 `build_verbose_hooks(callback)` 旧函数签名，使 `tests/unit/test_hooks.py` 不受影响。
- `evopaw/main.py` 把已有的 `main_runtime / sub_runtime` 显式透传给 `build_agent_fn(...)`。
- 新增 `tests/unit/test_agent_backends_base.py`（32 单测）：覆盖 Pydantic 字段约束、`StreamSink / AgentBackend` Protocol 鸭子类型、异常类继承、`get_backend` 懒加载与单例 / `register_backend` 优先级 / `openai_chat | anthropic_messages` 抛 `NotImplementedError`。
- 新增 `tests/unit/test_claude_sdk_backend.py`（25 单测）：用 `patch.multiple("evopaw.agent_backends.claude_sdk", ...)` mock query() / SDK 类型符号；覆盖正常返回 → `TurnResult.text & skills_called` / `model` 来自 `runtime.model` / `mcp_servers` 来自 `backend_hints` / StreamSink → PreToolUse / PostToolUse 触发链 / `CLINotFoundError | CLIConnectionError → ProviderTransientError` / 其它异常 → `ProviderUnknownError` / `record_llm_call` 在成功与失败两条路径都被调用。
- `tests/unit/test_main_agent.py` 改造为 `_patch_backend(fake)` 风格：`patch("evopaw.agents.main_agent.get_backend", return_value=FakeBackend())`，所有断言转为校验 `fake.calls[0]: TurnRequest` 字段；新增 `test_no_direct_sdk_import_in_this_file` import 卫士。
- 验收门槛全部满足：
  - `grep -n claude_agent_sdk evopaw/agents/main_agent.py` 仅 1 行 docstring，0 次 import；
  - `python3 -m pytest tests/unit/ -q` 745 通过（P1 683 + P2 新增 62），15 warnings，无 fail；
  - verbose 推送文案与 hook 适配字节级一致（`test_claude_sdk_backend.py::test_pre_tool_use_invokes_stream_sink` 与 `test_post_tool_use_invokes_stream_sink` 已断言）；
  - `pyproject.toml` 无新增依赖（仍只依赖 `claude-agent-sdk + pydantic`）。

---

## 11. 第三周可执行的具体动作（P3：OpenAIChatBackend + skills_runtime 拆出）✅ **已完成（2026-04-28）**

P2 已经把主 Agent 与 SDK 解耦到 `AgentBackend` 协议。P3 的目标是：**首个真正非 SDK 的 backend
落地，让主对话直接跑通 OpenAI-compatible 端点**（DashScope / OpenRouter / Moonshot / DeepSeek
/ 本地 vLLM）。Sub-Agent 仍走 Claude SDK（计划 §5 P3 验收 1.3 + §5 P5 结论）。

下面 8 步对应 §9 / §10 颗粒度（可独立 PR / 可滚动验收）：

1. 直接看第 2 步骤。
2. 拆 `evopaw/skills_runtime/` 模块（与 SDK 解耦）：
   - `registry.py`：搬 `_build_skill_registry / _extract_frontmatter_description`
   - `instructions.py`：搬 `_build_description_xml / _get_skill_instructions`（渐进披露阶段一/二）
   - `dispatcher.py`：新增 `class SkillDispatcher` + `_handle_history_reader`，封装三类分发
     （未知 / history_reader / reference / task）；返回 `str` 给上层 backend 自由组装
   - 行为零变化：原 `_xxx` 名字 re-export，现有 `test_skill_loader.py` 不破
3. 创建 `skills_runtime/adapters/claude_mcp.py`：
   - `build_skill_loader_server(...)` 整段搬过来；唯一调用点 `await dispatcher.dispatch(...)`
   - `tools/skill_loader.py` 改为薄壳 re-export（保留旧 import 路径与 underscore 私有函数）
4. 创建 `skills_runtime/adapters/openai_tools.py`：
   - `build_openai_tool_schema(dispatcher)` → 返回单个 OpenAI function tool dict
   - 不在这里执行 dispatch（dispatch 由 backend 在主循环内 await）
5. 新建 `evopaw/agent_backends/openai_chat.py`：
   - 用 `httpx.AsyncClient` 调 `{api_base}/chat/completions`；超时 120s
   - 工具调用循环：`finish_reason='tool_calls'` 时按顺序 dispatch 每个 tool_call，把字符串
     结果作为 `role=tool` 塞回 messages，再次请求；最多 `req.max_turns` 轮
   - StreamSink：每个 tool_call 前后触发 `on_tool_use` / `on_tool_result`，文案与
     `ClaudeSDKCompatBackend` 字节级一致
   - 异常归一化：401/403 → `ProviderAuthError`；429 → `ProviderRateLimited`；4xx 其它 →
     `ProviderInvalidRequest`；5xx / `httpx.ConnectError / TimeoutException` →
     `ProviderTransientError`；其它 → `ProviderUnknownError`
   - usage：累加每轮响应 `usage` 字段（多轮工具调用 token 合并到 `record_llm_call`）
   - extra_body 白名单：仅注入 `runtime.extra_body` 中已通过 `ProviderSpec.extra_body_whitelist`
     的字段；并防御性过滤掉与 `_GENERIC_BODY_FIELDS` 冲突的键，避免覆盖 `model / messages /
     tools / tool_choice / max_tokens / temperature / top_p / stream / response_format`
6. 在 `agent_backends/__init__.py` 的 `get_backend()` 中懒加载 `OpenAIChatBackend`，
   去掉 family=='openai_chat' 抛 `NotImplementedError` 的分支；`anthropic_messages`
   仍保留 `NotImplementedError`，等 P4。
7. `evopaw/agents/main_agent.py` 适配（最小改动）：按 `main_runtime.runtime_family` 分支
   构造 `backend_hints`：
   - `claude_sdk_compat` → `{"mcp_servers": {"evopaw": skill_server}}`（与 P2 一致）
   - `openai_chat` → `{"skill_dispatcher": SkillDispatcher(...)}`（OpenAIChatBackend 直接 await dispatch）
8. 测试与验收：
   - 新增 `tests/unit/test_skills_runtime_dispatcher.py`：未知 / reference / task / history_reader / task_context
     dict→json / dispatcher.history_all 拷贝快照
   - 新增 `tests/unit/test_skills_runtime_adapters.py`：openai_tools 顶层结构 / function 名称 / 参数 schema /
     description 含 session 路径；claude_mcp adapter 工厂可正常构建；`tools/skill_loader.py` re-export 不破
   - 新增 `tests/unit/test_openai_chat_backend.py`：mock `httpx.AsyncClient.post` 覆盖文本回复 /
     单轮 skill_loader / 多轮 tool_calls 循环 / dispatcher 异常吞掉成 tool message / no-dispatcher 友好回退 /
     StreamSink 触发与异常吞掉 / 401/429/400/500 / ConnectError / TimeoutException → 5 种 Provider*Error /
     `extra_body` 透传与白名单防御 / tools schema 注入 / `record_llm_call` 在成功与失败两路都被调用
   - `tests/unit/test_agent_backends_base.py`：删除 openai_chat 抛 NotImplementedError 的旧断言，
     改为 `test_openai_chat_lazy_loads`（懒加载 + 单例）；`register_backend` 测试改用
     `anthropic_messages` 占位
   - `requirements.txt` 增加 `httpx>=0.27`（计划 §5 P3 明确依赖；P2 阶段的"无新增依赖"门槛仅约束 P2）
   - 全量 `pytest tests/unit/ -q` 全绿

**验收门槛**（与 §5 P3「验收」严格对齐）：
- [x] OpenAIChatBackend 能跑通「文本对话 + 单轮 skill_loader 调用 + 多轮 tool_calls 循环」（mock httpx 全覆盖）
- [x] 401 / 429 / 400 / 500 / ConnectError / TimeoutException 分别归一化为 5 种 Provider*Error
- [x] verbose StreamSink 在 tool_call 前后被调用；异常被吞掉不破坏主流程
- [x] `usage` 与 `latency` 通过 `record_llm_call` 打到 P1 已声明的 metrics
- [x] `extra_body` 仅允许白名单字段（防御性过滤通用字段冲突）
- [x] `skill_loader` 保持单工具形态（不展开 18 个工具，与 Claude 路径文案一致）
- [x] 现有 `tests/unit/test_skill_loader.py` 42 个测试全绿（`tools/skill_loader.py` re-export 兼容入口）
- [x] 全量 `pytest tests/unit/ -q` 全绿

**回滚策略**：分支级回滚保留 P1 / P2（`provider_runtime / agent_backends/{base,claude_sdk}`）；
仅撤销 `agent_backends/openai_chat.py / skills_runtime/`（恢复 `tools/skill_loader.py` 旧实现）即可。

**P3 实际落地结果（2026-04-28）**：

- 新增 `evopaw/skills_runtime/` 子包：
  - `registry.py + instructions.py + dispatcher.py`：从 `tools/skill_loader.py` 抽出，无 SDK 依赖；
    `class SkillDispatcher.dispatch(skill_name, task_context) -> str` 是 OpenAI / Claude 两路 backend 共享的
    业务核心；history_reader 仍内联在 dispatcher 内（不创建 Sub-Agent）。
  - `adapters/claude_mcp.py`：搬 `build_skill_loader_server`，唯一调用点 `dispatcher.dispatch(...)`；
    Claude SDK 的 `create_sdk_mcp_server / @tool` 仅在本模块 import。
  - `adapters/openai_tools.py`：`build_openai_tool_schema(dispatcher)` 返回单个 OpenAI function tool dict，
    description 直接复用 `dispatcher.get_description()`。
- `evopaw/tools/skill_loader.py` 改为薄壳：`from evopaw.skills_runtime.adapters.claude_mcp import
  build_skill_loader_server` 等 re-export，保留 `_build_*` 私有名字，让 `test_skill_loader.py` 42 个
  测试零修改全绿。
- 新增 `evopaw/agent_backends/openai_chat.py`：
  - `httpx.AsyncClient + chat/completions`；2 阶段以上 tool_calls 循环；StreamSink 触发与异常吞噬；
    5 种 HTTP / 网络异常归一化；usage 多轮累加；`record_llm_call` 在成功 / 失败两路都打点。
  - extra_body 白名单 + 防御性过滤通用字段，避免 hermes #8591 类泄漏。
- `evopaw/agent_backends/__init__.py::get_backend`：`openai_chat` 走 `OpenAIChatBackend` 懒加载单例；
  `anthropic_messages` 仍保留 NotImplementedError 等 P4（**P4 已落地，详见 §12**）。
- `evopaw/agents/main_agent.py`：按 `runtime_family` 分支构造 `backend_hints`（claude → MCP server；
  openai → SkillDispatcher）；其它分支（`async_index_turn / ctx 持久化 / record_skills`）保持原位。
- `requirements.txt` 增加 `httpx>=0.27`。
- 新增测试：`tests/unit/test_skills_runtime_dispatcher.py`（15）+ `test_skills_runtime_adapters.py`（6）+
  `test_openai_chat_backend.py`（35），共 56 单测。
- `tests/unit/test_agent_backends_base.py` 调整：删除 `test_unimplemented_openai_chat_raises`，新增
  `test_openai_chat_lazy_loads`；`test_register_backend_takes_precedence` 改用 `anthropic_messages`
  作为占位（避免污染 openai_chat 单例）。
- 全量 `pytest tests/unit/ -q` **801 通过**（P2 745 + P3 新增 56），15 warnings，无回归。

---

## 12. 第四周可执行的具体动作（P4：AnthropicMessagesBackend + 多模态 content builder）✅ **已完成（2026-04-28）**

### 12.1 落地步骤（按执行顺序）

1. **新增 `evopaw/content_builders/` 包**：
   - `claude_blocks.py`：`build_image_block(b64, mime) → {"type":"image","source":{"type":"base64","media_type":..,"data":..}}`；
     `build_user_content(text, image_b64, mime_type) → str | list[dict]`，无图返回字符串，
     有图返回 `[{type:text}, image_block]`；mime 缺省回退 `image/jpeg`。
   - `anthropic_blocks.py`：第一阶段两族（claude_sdk_compat / anthropic_messages）共享同一形态 ——
     `build_image_block / build_user_content` 全部 re-export 自 `claude_blocks`，避免文案重复维护。
   - `openai_blocks.py`：`build_image_block(b64, mime) → {"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}}`；
     `build_user_content` 形态与 claude 同源但 image block 改成 OpenAI vision 形态。
   - `__init__.py::pick_content_builder(family)`：family→module 映射，
     `claude_sdk_compat→claude_blocks / anthropic_messages→anthropic_blocks / openai_chat→openai_blocks`，
     未知 family 抛 `ValueError`。
2. **新增 `evopaw/skills_runtime/adapters/anthropic_tools.py::build_anthropic_tool_schema(dispatcher)`**：
   返回 Anthropic 平铺 schema（`{name, description, input_schema}`），description 复用
   `dispatcher.get_description()`（即 `<available_skills>` 渐进披露 XML），与 OpenAI 路径文案完全一致；
   字段名严格用 `input_schema`（不是 OpenAI 的 `parameters`），无 `type:function` 包装。
3. **新增 `evopaw/agent_backends/anthropic_messages.py::AnthropicMessagesBackend`**（约 280 行）：
   - 端点 `POST {api_base}/v1/messages`；headers 用 `x-api-key + anthropic-version: 2023-06-01`，
     **不**用 `Authorization: Bearer`（与 OpenAI 路径区分）。
   - 请求体形态：顶层 `system` 字段（不再放进 messages）、必填 `max_tokens`（默认 4096）、
     `messages: [{role, content: blocks}]`、可选 `tools` + `tool_choice={"type":"auto"}`。
   - 工具调用解析：assistant `content` 列表中的 `{type:"tool_use", id, name, input}`；
     `stop_reason == "tool_use"` 触发下一轮；工具结果通过 user 消息回写
     `[{"type":"tool_result","tool_use_id":..,"content":..}]`。
   - usage：`input_tokens / output_tokens`，total = sum；多轮累加；
     `record_llm_call(outcome=success/auth_error/rate_limited/transient/invalid_request/unknown_error)`。
   - extra_body 白名单 + 防御性过滤通用字段（model / messages / system / max_tokens 等不允许覆盖出站 body），
     沿用 P3 的 hermes #8591 类泄漏防御。
   - 异常归一化：401/403→Auth、429→Rate、4xx→Invalid、5xx→Transient；ConnectError/TimeoutException→Transient；
     其它→Unknown。Provider*Error 透传不重新包裹。
4. **`evopaw/agent_backends/__init__.py::get_backend` 注册**：
   `family == "anthropic_messages"` 走 `AnthropicMessagesBackend()` 懒加载单例（与 P3 openai_chat 同结构）；
   删除原 NotImplementedError 分支。
5. **`evopaw/tools/add_image_tool_local.py` 抽出 `load_image_data`**：
   返回 `(base64_str, mime_type) | None`，把读盘 / 路径校验 / 扩展名校验 / 大小校验从原
   `load_image_for_claude` 中下沉；后者改为 thin wrapper 调用 `load_image_data` 后包成
   Claude 形态 image block，保留旧测试零改动。
6. **`evopaw/agents/main_agent.py` 三族分发改造**：
   - 新增 `from evopaw.content_builders import pick_content_builder` 与 `from .add_image_tool_local import load_image_data, extract_image_path`。
   - 拼装 `user_content` 前先 `builder = pick_content_builder(main_runtime.runtime_family)`，
     有图时 `builder.build_user_content(text, image_b64, mime_type)`，无图返回纯字符串
     （三族行为完全对齐，差异只在底层 image block 形态）。
   - `backend_hints` 分支：`claude_sdk_compat` 走 `build_skill_loader_server` → `mcp_servers`；
     `openai_chat / anthropic_messages` 共用 `SkillDispatcher` → `skill_dispatcher`。
7. **单元测试新增**（共 62 个）：
   - `tests/unit/test_content_builders.py`（16）：claude/anthropic 同源 + openai 形态、纯文本、默认 mime、
     空 b64、`pick_content_builder` 三族 + 未知抛 ValueError。
   - `tests/unit/test_anthropic_messages_backend.py`（30）：
     `_classify_http_error` ×5、`_parse_usage` ×3、`_normalize_tool_input` ×5、
     `TestRunTurnTextOnly` ×7（端点 `/v1/messages`、x-api-key headers、默认 max_tokens=4096、
     缺 api_base 抛 InvalidRequest、blocks passthrough）、
     `TestRunTurnToolUse` ×5（单工具调用、usage 累加、两轮 tool_use、dispatcher 异常吞没、无 dispatcher 兜底）、
     `TestRunTurnStreamSink` ×2、`TestErrorNormalization` ×6、
     `TestExtraBodyWhitelist` ×2（passthrough / 通用字段过滤）、
     `TestToolsSchemaInjection` ×2（input_schema 无 parameters / function 包装；无 dispatcher 不发 tools）。
   - `tests/unit/test_skills_runtime_adapters.py` 新增 `TestAnthropicToolsSchema` ×4：
     平铺形态、description 来自 dispatcher.get_description()、input_schema 结构、session 路径反射。
   - `tests/unit/test_agent_backends_base.py`：删除 `test_unimplemented_anthropic_messages_raises`；
     新增 `test_anthropic_messages_lazy_loads`；`test_register_backend_takes_precedence` 在 setup 阶段先
     pop 缓存，避免懒加载实例污染断言。
   - `tests/unit/test_main_agent.py` 新增 `TestRuntimeFamilyDispatch` ×5：
     anthropic_messages → skill_dispatcher 注入、openai_chat → skill_dispatcher 注入、
     claude_sdk_compat → mcp_servers 注入、anthropic 形态 image block、openai 形态 image_url block。
8. **验收**：
   - `pytest tests/unit/ -q` 全绿：**863 通过**（P3 基线 801 + P4 新增 62），无回归，15 warnings。
   - 三族 backend 都已落地懒加载单例：`get_backend` 路由表完成。
   - 未来扩展点：bedrock_converse / openai_codex 只需新增 `agent_backends/<family>.py` + 在
     `get_backend` 添加分支，content_builder 与 tools schema 适配按需补即可。

### 12.2 关键决策

- **anthropic_blocks 复用 claude_blocks 实现**：第一阶段两族 wire 形态相同（base64 source.type / media_type / data），
  没必要复制一份代码。如果未来 Claude SDK 引入新字段（prompt cache marker / pdf citation 等），
  再在两族分叉。
- **load_image_data 与 load_image_for_claude 共存**：旧 8 个测试覆盖 Claude block 形态保持，
  避免误删；新代码统一走 `load_image_data` + content_builder。
- **Anthropic vs OpenAI tool schema 分两份 adapter**：避免在 dispatcher 内做 family 分支判断，
  让 backend 自取所需，dispatcher 保持纯逻辑。
- **`tool_choice` 形态差异**：OpenAI 用字符串 `"auto"`、Anthropic 用对象 `{"type":"auto"}`；
  各 backend 内部处理，TurnRequest 不需要透传 tool_choice 配置。
- **多轮 tool_use 状态机**：在 anthropic_messages.py 内自管 `messages` 列表，
  每轮 append assistant（含全部 content blocks，保留 tool_use id）+ user（tool_result blocks）。
  StreamSink 异常吞没（与 P3 openai_chat 一致）。

### 12.3 验收门槛

- ✅ `pytest tests/unit/ -q` 全绿（863 / 0 失败 / 15 warnings）
- ✅ Anthropic 直连 / Claude SDK / OpenAI 兼容 三种 main runtime 都能通过统一的 main_agent 入口
- ✅ 多模态：三族都能挂图（content_builder 决定 wire 形态）
- ✅ skill_loader 工具调用：三族都用同一个 SkillDispatcher 业务逻辑（adapter 层处理 schema 差异）
- ✅ 凭证不进 LLM context、不写 workspace；observability 标签齐全（provider_id / runtime_family / role / outcome）
- ⏸ 集成测试连真模型留到 P5 矩阵阶段（按计划走 nightly）

### 12.4 回滚策略

- 单文件回滚：`agent_backends/__init__.py` 把 `anthropic_messages` 分支恢复为 `NotImplementedError`，
  并 `git revert` 新增的 `anthropic_messages.py` / `content_builders/` / `anthropic_tools.py`。
- main_agent 回滚：恢复旧 `from evopaw.tools.add_image_tool_local import load_image_for_claude`
  + Claude 形态硬编码；`load_image_data` 保留无害（只是没人调用）。
- 测试已隔离在 `test_content_builders.py` / `test_anthropic_messages_backend.py` 两个新文件，
  回滚时整体删除即可；`test_main_agent.py` / `test_skills_runtime_adapters.py` / `test_agent_backends_base.py`
  的新增 class 也是局部新增，可定点撤销。

---

## 13. 第五周可执行的具体动作（P5：skill_loader 全面适配 + Skill 兼容矩阵）✅ **已完成（2026-04-28）**

### 13.1 落地前提核对

P3/P4 已经把基础设施落齐，P5 验收的核心断言其实是「这些基础设施确实统一作用于全部 19 个真实 Skill」。逐项核对：

| P5 验收项 | 是否依赖新代码 | 现状 |
|---|---|---|
| 三族主 runtime 共享同一份 dispatcher 业务逻辑 | 否 | P3/P4 已落地：claude_mcp adapter（`build_skill_loader_server`）内部构造 `SkillDispatcher` 并把 `dispatch` 包成 `@tool`；openai/anthropic backend 通过 `backend_hints={"skill_dispatcher": ...}` 拿到同一个对象 |
| task 型 skill 始终 fallback 到 Claude SDK Sub-Agent | 否 | `SkillDispatcher.dispatch` 中 task 分支直接 `await run_skill_agent(...)`，与主 runtime 无关 |
| reference 型 skill 直接返回 SKILL.md | 否 | `dispatch` 中 reference 分支返回 `<skill_instructions>...</skill_instructions>` |
| `history_reader` 内联 | 否 | `_handle_history_reader(history_all, ctx_str)` 在 dispatch 早期分支命中 |
| 19 个 enabled skill 都能加载 | 否 | `_build_skill_registry` 走 `load_skills.yaml` + `SKILL.md` 存在性 |

**结论**：P5 不需要重新写业务代码，只需要把验收点变成可重复执行的回归用例 + 一份明确的兼容矩阵文档。

### 13.2 落地步骤

1. **新增 `docs/skills-provider-matrix.md`**：
   - §1：三族主 runtime + Sub-Agent 现状 + Skill 类型说明（reference / task / 内联）。
   - §2：完整 19 个 enabled Skill 的矩阵表（含 `hk-investment-morning-report`，矩阵给出每个 skill 在三族下的状态、Sub-Agent 依赖项）。
   - §3：三族主 Agent 路径 ASCII 图，明确 `SkillDispatcher.dispatch` 是单一汇聚点；adapter 层负责 schema 形态转换。
   - §4：凭证 / 会话隔离回顾（强调 `session_id` 不进 LLM context）。
   - §5：P5 测试覆盖清单 + nightly live 留白。
   - §6：维护守则（load_skills.yaml 改动同步本文表格；新 provider 加列；新 Sub-Agent 替换重写）。
2. **新增 `tests/integration/test_skill_loader_e2e.py`** —— 不依赖真实 LLM API，覆盖：
   - `TestSkillCatalogIntegrity` ×5：遍历真实 `evopaw/skills/load_skills.yaml`，
     至少一个 reference、type 仅限 `task`/`reference`、dispatcher 全部加载、
     `<available_skills>` XML 列出每个 skill。
   - `TestThreeRuntimeFamiliesShareDispatcher` ×5（含 2 组 parametrize）：
     `claude_sdk_compat` 走 `mcp_servers`；`openai_chat`/`anthropic_messages` 走 `skill_dispatcher`；
     三族下 dispatcher.list_skill_names() 都覆盖全部 19 个真实 Skill。
   - `TestTaskSkillsAlwaysFallBackToClaudeSubAgent` ×（18 task + 1 reference）：
     每个 task skill 触发 `run_skill_agent`，模型名包含 `haiku`；reference skill（含 `history_reader`）
     不调 Sub-Agent，分别返回 `<skill_instructions>...</skill_instructions>` 与内联 JSON。
   - `TestEndToEndTaskSkillAcrossRuntimes` ×8（3 task × 2 family + 2 family × history_reader）：
     从 `build_agent_fn` 入口走到 `dispatcher.dispatch`，验证 task 触发 Sub-Agent、`task_context`
     原样透传；history_reader 不触发 Sub-Agent，分页 JSON 正确。
   - 共 **37 用例**。
3. **更新计划文档 §5 P5 加 ✅**；补本节 §13 落地总结。

### 13.3 验收

- `pytest tests/integration/test_skill_loader_e2e.py -v` → **37/37 通过**（< 1 秒）。
- `pytest tests/unit/ -q` → **863 通过**，无回归。
- 矩阵文档与测试守护：未来在 `load_skills.yaml` 增删 / 改 type 时，
  `TestSkillCatalogIntegrity::test_dispatcher_loads_all_enabled_skills` 与
  `test_dispatcher_description_mentions_each_skill` 会强制提示「记得改 matrix 文档」。
- 留给 nightly：三族 × 真实 LLM 端点 e2e 标 `pytest.mark.live`，CI nightly 触发；
  Sub-Agent 跨 provider 属 P6 范围，本期不做。

### 13.4 关键决策

- **不写业务代码**：P3/P4 已经把 dispatcher 抽出，再写新代码就是重复劳动。P5 的价值在于
  「用回归测试守住已经实现的不变量」+「把 Skill × Provider 的兼容关系白纸黑字写下来」。
- **e2e 测试不连真实 LLM**：FakeBackend + mock `run_skill_agent` 就足以验证 dispatcher
  路径完整。真模型端点交给 nightly live job，避免 CI 时间和 quota 占用。
- **覆盖 19 个 Skill 全集，不抽样**：`pytest.mark.parametrize("skill_name", _TASK_SKILLS)`
  让每个 task skill 都有独立用例。新增 skill 时若忘了更新 matrix 文档，
  `TestSkillCatalogIntegrity` 会立即报红。
- **Sub-Agent 模型名硬约束 `haiku`**：用 `"haiku" in kwargs["model"].lower()` 做断言，
  确保未来若有人不小心改成其他模型（比如 sonnet）测试会立即失败，提示重新评估成本。

### 13.5 回滚策略

- 整个 P5 没有动业务代码（只新增文档 + 测试），回滚极简：
  - `git rm docs/skills-provider-matrix.md`
  - `git rm tests/integration/test_skill_loader_e2e.py`
  - 撤销本文 §5 P5 ✅ 标记与本节 §13。
- 即便完全回滚，主 Agent / Sub-Agent / dispatcher 行为不受影响。

---

## 附录 A：与原计划的对应表

| 原计划章节 | 处理方式 | 在本文位置 |
|---|---|---|
| 一、当前耦合 | 行号修正 + 增加现状汇总表 | §2 |
| 二、Hermes 方法 | 保留要点；删除「runtime resolver 很强」中未验证的细节 | §3.2 |
| 三、Nanobot 方法 | 修正「AgentLoop 只关心 provider」的过度简化 | §3.1 |
| 四、对比表 | 收敛为「学结构 / 学行为」二分 | §4 |
| 五、改造建议 | 全部进入分阶段 P1–P6 | §5 |
| 六、阶段路线 | 重写：每阶段补验收/回滚/工作量边界 | §5 |
| 七、最终建议 | 浓缩到 §4 总体原则 + §9 第一周动作 | §4, §9 |
| 八、最终判断 | 删除（与 §4 重复） | — |

## 附录 B：参考资料（已二次核实）

- `nanobot/providers/registry.py` — `ProviderSpec` + 30 个 provider 条目，5 种 backend
- `nanobot/providers/base.py` — `LLMProvider / LLMResponse / ToolCallRequest / GenerationSettings`
- `nanobot/agent/loop.py` — `AgentLoop` 接收 `provider: LLMProvider`，构造参数 25+
- `hermes_cli/runtime_provider.py` — `_VALID_API_MODES = {chat_completions, codex_responses, anthropic_messages, bedrock_converse}`
- hermes-agent issue #8591 — OpenRouter `provider` 字段泄漏
- hermes-agent issue #12381 — TUI 路径 `_make_agent` 丢 provider/api_mode
- evopaw 本地：`evopaw/main.py:133-136`、`evopaw/agents/main_agent.py:14-22 / 195`、`evopaw/agents/skill_agent.py:全文 76 行`、`evopaw/llm/claude_client.py:82`、`evopaw/tools/skill_loader.py:25 / 243-312`、`evopaw/memory/context_mgmt.py:107-134`、`evopaw/memory/indexer.py:52-78`、`config.yaml.template:17-24, 55-82`
