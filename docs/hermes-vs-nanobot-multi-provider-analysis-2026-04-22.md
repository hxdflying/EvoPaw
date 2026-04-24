# Hermes-Agent 与 Nanobot 多模型兼容方案对比，以及 EvoPaw 的改进建议

日期：2026-04-22  
目标：分析 `NousResearch/hermes-agent` 和 `HKUDS/nanobot` 是如何做到兼容多家模型厂商 API 的，并结合 `evopaw` 当前代码结构，给出一套可落地的多 provider 改造建议。

---

## 结论先行

如果只说一句话：

> `hermes-agent` 是“运行时解析和路由能力很强”的路线，`nanobot` 是“核心抽象更干净、扩展成本更低”的路线；`evopaw` 最适合采用两者的组合，而不是照搬任何一个。

更具体一点：

1. `nanobot` 的核心方法是：用一个统一的 `LLMProvider` 接口和一个 `Provider Registry`，把几十家模型厂商收敛成少数几种 backend 类型。
2. `hermes-agent` 的核心方法是：把“provider 选择”和“请求构造方式”拆开，用统一的 runtime resolver 决定这次请求最终该走哪种协议与哪组凭证。
3. `evopaw` 当前最大问题不是“没有 OpenAI SDK”，而是**主 Agent、Sub-Agent、Tool 暴露层都直接绑在 Claude Agent SDK 上**。
4. 对 `evopaw` 来说，最优路线不是“先支持所有厂商”，而是先把运行时拆成协议层，再让多厂商自然落到这些协议层上。

我给出的最终建议是：

- 核心抽象层优先学习 `nanobot`
- 运行时解析和按角色路由优先学习 `hermes-agent`
- 迁移策略上保留 `claude_sdk_compat` 作为过渡层，不要一开始就试图把所有执行面一次性切走

---

## 一、EvoPaw 当前为什么只能依赖 Claude Agent SDK

先看 `evopaw` 当前的耦合现实。

### 1. 启动入口直接依赖 Claude CLI

`evopaw/main.py` 在启动时会先检查 `claude` CLI 是否存在；不存在直接报错退出。

这意味着当前系统默认假设：

- 主 Agent 运行时一定是 Claude Agent SDK
- Sub-Agent 运行时一定是 Claude Agent SDK

相关代码：

- `evopaw/main.py:79-85`

### 2. 主 Agent 调用面直接使用 `query()`

`evopaw/agents/main_agent.py` 直接从 `claude_agent_sdk` 导入：

- `query`
- `ResultMessage`
- `CLINotFoundError`
- `CLIConnectionError`

然后在主循环里直接 `async for message in query(...)`。

相关代码：

- `evopaw/agents/main_agent.py:14-20`
- `evopaw/agents/main_agent.py:173-190`

### 3. Tool 暴露层直接依赖 Claude SDK 的 MCP 工具装饰器

`skill_loader` 当前不是 provider-neutral 的工具层，而是直接用：

- `create_sdk_mcp_server`
- `@tool`

把 Skill 注册成 Claude Agent SDK 能理解的 MCP server。

相关代码：

- `evopaw/tools/skill_loader.py:24-25`
- `evopaw/tools/skill_loader.py:208-306`

### 4. task 型 Skill 的执行器也直接依赖 Claude SDK

`evopaw/agents/skill_agent.py` 里 task 型 Skill 不是通过本地统一执行器跑的，而是再次起一个 Claude SDK sub-agent，并开放：

- `Bash`
- `Read`
- `Write`
- `Edit`
- `Grep`
- `Glob`

相关代码：

- `evopaw/agents/skill_agent.py:12-19`
- `evopaw/agents/skill_agent.py:46-54`
- `evopaw/llm/claude_client.py:65-86`

### 5. 当前配置模型只是“Claude 模型名”，不是 provider runtime

`config.yaml.template` 里当前只配置：

- `planner_model`
- `sub_agent_model`

没有 provider registry，也没有 runtime family，也没有 per-role routing。

相关代码：

- `config.yaml.template:17-24`

### 6. 有意思的现实：记忆层其实已经不是纯 Claude 了

虽然主 Agent 完全绑在 Claude SDK 上，但记忆压缩和 embedding 已经使用 OpenAI-compatible 的 DashScope / Qwen 了。

相关代码：

- `evopaw/memory/context_mgmt.py:129-153`
- `evopaw/memory/indexer.py:59-86`

这说明 `evopaw` 其实已经是“局部多 provider”，只是**对话运行时**还没有抽象出来。

---

## 二、Hermes-Agent 是怎么做到兼容很多模型厂商的

Hermes 的方法不是“每个厂商写一套完全独立的 Agent”，而是：

> 先统一运行时解析，再按协议类型选择 transport。

### 1. Hermes 的核心思路：provider 解析和请求构造分离

Hermes README 和配置文档都反复强调：

- 你可以切换 `provider`
- 你可以指定 `model`
- 你可以覆盖 `base_url`
- 这些模式不只用于主模型，也用于 auxiliary/delegation/fallback

文档里的统一模式基本都是：

```yaml
provider: "..."
model: "..."
base_url: "..."
```

而且这套模式不是只给主会话用，辅助任务也沿用同一套结构。

对应资料：

- Hermes README
- Hermes Configuration

### 2. Hermes 把“厂商”和“协议模式”区分开

Hermes 真正关键的文件之一是 `hermes_cli/runtime_provider.py`。  
从这份代码可以看出，它没有把所有厂商等价处理，而是引入了 `api_mode` 的概念。

当前可见的运行模式至少包括：

- `chat_completions`
- `codex_responses`
- `anthropic_messages`
- `bedrock_converse`

也就是说，Hermes 真正的抽象不是“支持 20 家厂商”，而是：

- 大部分厂商归入 OpenAI 风格的 `chat_completions`
- 少数厂商或特殊端点使用特殊协议

这是 Hermes 能兼容大量 API 的关键。

### 3. Hermes 的 runtime resolver 很强

Hermes 的 resolver 会综合这些信息来决定本次请求怎么发：

- 显式请求的 provider
- 配置文件里的 provider
- 环境变量
- base URL
- credential pool
- model 名称

它甚至会做一些非常实际的自动判断：

- 如果 `base_url` 是 `api.openai.com`，某些模型需要走 `codex_responses`
- 如果 endpoint 以 `/anthropic` 结尾，则按 `anthropic_messages` 对待
- 如果是本地 server 且模型没填，它会试着调用 `/models` 自动探测

这意味着 Hermes 的“多 provider”不是简单字符串切换，而是**真正的 runtime resolution**。

### 4. Hermes 的优势：按角色路由

Hermes 配置文档里一个非常值得借鉴的点是：

- `auxiliary`
- `compression`
- `fallback_model`
- `delegation`

这些都能各自指定：

- `provider`
- `model`
- `base_url`
- `api_key`

这让 Hermes 可以做到：

- 主 Agent 用一个高质量模型
- delegation 用一个便宜模型
- vision 用另一个支持多模态的模型
- compression 用更便宜、更快的模型

这不是“多 provider 支持”，这是“多 provider 按职责分工”。

### 5. Hermes 的代价：复杂度很高

Hermes 的问题也非常明显：功能强，但复杂度高，组合态很多，所以很容易出 provider-specific bug。

公开 issue 已经暴露了这种代价，例如：

- OpenRouter 的 `provider` 字段泄漏到非 OpenRouter 请求体里
- TUI 路径没有正确传入 provider/api_mode
- 特定 provider 对 `base_url` 的处理与配置不一致

这说明 Hermes 的强大来自一个复杂的 runtime system，而复杂 runtime 的维护成本也是真实存在的。

### 6. 对 Hermes 方法的总结

Hermes 的多厂商兼容，本质上依赖这几个点：

1. 统一的 runtime provider resolver
2. `provider/model/base_url` 三元组
3. `api_mode` 这种协议级抽象
4. 对 auxiliary / fallback / delegation 的独立路由
5. 广泛接受 OpenAI-compatible endpoint 作为主兼容面

这是一种“运行时导向”的设计。

---

## 三、Nanobot 是怎么做到兼容很多模型厂商的

Nanobot 的方法更简洁，也更像一个小而清晰的内核。

它的核心思路可以概括为：

> 用少量统一 backend 类型，承载大量 provider metadata。

### 1. Nanobot 的核心抽象：`LLMProvider`

Nanobot 在 `nanobot/providers/base.py` 里定义了统一的 provider 接口和标准返回结构。

从公开代码可见，它至少统一了这些概念：

- `LLMResponse`
- `ToolCallRequest`
- `GenerationSettings`

而 `LLMResponse` 里统一承载：

- `content`
- `tool_calls`
- `finish_reason`
- `usage`
- `reasoning_content`
- `thinking_blocks`

这意味着：

- OpenAI 风格
- Anthropic 风格
- 带 reasoning 的模型
- 带 thinking block 的模型

都会先被归一化，再交给上层 AgentLoop。

### 2. Nanobot 的核心控制面：Provider Registry

Nanobot 最关键的文件是 `nanobot/providers/registry.py`。

它把 provider 元数据集中在一个 `ProviderSpec` 注册表里，字段包括：

- `name`
- `keywords`
- `env_key`
- `display_name`
- `backend`
- `default_api_base`
- `is_gateway`
- `is_local`
- `detect_by_key_prefix`
- `detect_by_base_keyword`
- `strip_model_prefix`
- `supports_max_completion_tokens`
- `model_overrides`
- `supports_prompt_caching`

这套设计的价值非常大：

1. 增加 provider 时，不需要到处加 `if/elif`
2. provider 特性集中管理
3. “厂商差异”不会污染 Agent 主循环

### 3. Nanobot 的关键技巧：大多数 provider 都落到少数 backend

Nanobot 并不是为每一家 provider 写一套独立实现。

从 registry 可以看到，很多 provider 最终都映射到少数几种 backend：

- `openai_compat`
- `anthropic`
- `azure_openai`
- `openai_codex`
- `github_copilot`

也就是说：

- OpenRouter、DashScope、Moonshot、DeepSeek、Gemini、Zhipu、Groq、vLLM、Ollama、LM Studio、SiliconFlow 等，大多都只是不同 metadata 的 `openai_compat`
- 只有协议差异显著时，才单独换 backend

这是 Nanobot 兼容大量厂商的真正秘诀。

### 4. Nanobot 的配置设计非常干净

Nanobot 的配置文档里把 provider 分成两层：

1. `providers`：定义可用 provider 的配置
2. `agents.defaults`：指定默认用哪个 provider / model

例如：

```json
{
  "providers": {
    "openrouter": { "apiKey": "...", "apiBase": "https://openrouter.ai/api/v1" },
    "ollama": { "apiBase": "http://localhost:11434" }
  },
  "agents": {
    "defaults": {
      "provider": "ollama",
      "model": "llama3.2"
    }
  }
}
```

这比“主配置里只有一个模型字符串”的设计更适合扩展。

### 5. Nanobot 把“custom provider”作为一等公民

Nanobot 配置文档里一个非常重要的细节是：

- 任意 OpenAI-compatible endpoint 用 `custom`
- 如果是 Responses-compatible endpoint，用 `azure_openai` 形状

也就是说，Nanobot 不是按厂商品牌建模，而是按**协议兼容面**建模。

这点非常值得 `evopaw` 学。

### 6. Nanobot 的 AgentLoop 本身不关心厂商

`nanobot/agent/loop.py` 里 `AgentLoop` 构造函数直接接收 `provider: LLMProvider`。

这意味着上层 loop 只关心：

- provider 是否能 chat
- provider 是否会返回 tool calls
- provider 如何返回 usage

而不关心：

- 这是 OpenAI、Anthropic、Groq、Moonshot 还是本地 vLLM

这就是架构边界清晰的体现。

### 7. 对 Nanobot 方法的总结

Nanobot 的多厂商兼容，本质上依赖这几个点：

1. 一个清晰的 `LLMProvider` 接口
2. 一个集中化的 `Provider Registry`
3. 少数 backend 类型承载多数 provider
4. 显式区分 protocol family，而不是把每个厂商都当新世界
5. Agent loop 对 provider 无感知

这是一种“内核抽象导向”的设计。

---

## 四、Hermes 与 Nanobot 的方法对比

下面是最关键的对比。

| 维度 | Hermes-Agent | Nanobot | 对 EvoPaw 的启发 |
|---|---|---|---|
| 核心抽象 | runtime resolver + api_mode | LLMProvider interface + registry | 两者都要学，但顺序不同 |
| 统一兼容面 | 先 resolve provider，再选协议路径 | 先用 registry 收敛 metadata，再走 backend | EvoPaw 应该先做 registry，再做 resolver |
| 配置模式 | `provider/model/base_url` 在主模型、辅助模型、fallback、delegation 中普遍复用 | `providers` + `agents.defaults` 分层很清晰 | EvoPaw 应该引入 provider blocks 和 role-based routing |
| 多 provider 策略 | 高能力、高复杂度 | 低复杂度、高可维护 | EvoPaw 更适合先靠近 nanobot |
| Local / custom endpoint | 很强，支持 custom/base_url/auto-detect/local | 也强，`custom` + local providers 非常明确 | 两边都值得借鉴 |
| 特殊协议处理 | 很重视 `api_mode` 差异 | 用 backend 类型吸收差异 | EvoPaw 应该同时有 `runtime_family` 和 `backend`/`api_mode` 概念 |
| 角色化路由 | 很强 | 相对简单 | EvoPaw 应借 Hermes 的按职责路由 |
| 风险 | 配置和运行时组合态多，易出 provider-specific bug | 某些高级 provider 特性需要继续扩展 | EvoPaw 不要一开始就追 Hermes 全量复杂度 |

### 我的判断

如果只选一个风格作为 `evopaw` 的主要参考，我会选 **Nanobot 的核心抽象方式**。

原因很简单：

- `evopaw` 当前代码量和复杂度更接近“小而专用的 agent runtime”
- 它还没有一个 provider-neutral 的 agent loop
- 如果一上来就照搬 Hermes 的全量 runtime 复杂度，改造风险会过大

但如果只学 Nanobot，也不够。

因为 `evopaw` 未来真正需要的不是“静态支持多个 provider”，而是：

- 主 Agent 用什么
- Sub-Agent 用什么
- 记忆压缩用什么
- embedding 用什么
- 未来 vision / fallback 用什么

这一点上，Hermes 的 role-based routing 明显更成熟。

所以最优组合是：

> 用 Nanobot 的结构打底，用 Hermes 的 runtime resolution 和 role routing 做升级。

---

## 五、EvoPaw 应该怎么改，才能真正支持多种模型厂商

这里给出直接针对 `evopaw` 的建议。

## 5.1 总原则：先抽协议层，不要先堆厂商列表

`evopaw` 不应该先做这种设计：

- `if provider == "openai": ...`
- `elif provider == "moonshot": ...`
- `elif provider == "deepseek": ...`

这样最后一定变成分支爆炸。

应该先定义：

### Provider Identity

描述“这是谁”：

- `openrouter`
- `anthropic`
- `moonshot`
- `dashscope`
- `custom`

### Runtime Family / Transport

描述“怎么发请求”：

- `claude_sdk_compat`
- `openai_chat`
- `anthropic_messages`
- 以后如需要再加 `responses_api`

### Capability / Param Policy

描述“有哪些约束”：

- 是否支持 tool calls
- 是否支持 vision
- 用 `max_tokens` 还是 `max_completion_tokens`
- 是否需要 strip model prefix
- 是否支持 prompt caching

这正是 nanobot 与 Hermes 的共同本质。

---

## 5.2 第一优先级：引入 provider runtime 抽象

建议新增：

```text
evopaw/provider_runtime/
├── __init__.py
├── models.py
├── registry.py
├── resolve.py
├── policy.py
└── capabilities.py
```

建议最少定义这些类型：

```python
class ProviderSpec(BaseModel):
    provider_id: str
    display_name: str
    runtime_family: Literal["claude_sdk_compat", "openai_chat", "anthropic_messages"]
    api_key_env: str | None
    default_api_base: str | None
    is_gateway: bool = False
    is_local: bool = False
    strip_model_prefix: bool = False
    supports_vision: bool = False
    supports_tool_calls: bool = True
    token_param_style: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    supports_prompt_caching: bool = False

class ResolvedRuntime(BaseModel):
    provider_id: str
    runtime_family: str
    model: str
    api_base: str | None
    api_key: str | None
    role: str
```

### 为什么这一步最重要

因为现在 `evopaw/main.py`、`main_agent.py`、`skill_agent.py`、`claude_client.py` 都在各自读模型配置、假设运行时、假设异常类型。

先引入统一 resolver，后面任何 provider 支持才会有落点。

---

## 5.3 第二优先级：把 `skill_loader` 从 Claude MCP 里拆出来

这是 `evopaw` 真正的硬骨头。

现在 `skill_loader.py` 混在一起做了三件事：

1. skill 注册表
2. skill 指令拼装
3. Claude MCP server 适配

而多 provider 架构下，这三件事必须拆开。

建议改成：

```text
evopaw/skills_runtime/
├── registry.py          # 解析 load_skills.yaml / SKILL.md
├── instructions.py      # 生成 description / execution_directive
├── dispatcher.py        # history_reader / reference / task 的统一分发
├── adapters/
│   ├── claude_mcp.py    # 现有 create_sdk_mcp_server 包装
│   ├── openai_tools.py  # 转 OpenAI tools schema
│   └── anthropic_tools.py  # 转 Anthropic Messages tools schema
```

### 为什么必须拆

因为当前主 Agent 的“外部能力接口”是：

> `skill_loader MCP server`

这不是通用 Agent 工具层，而是 Claude SDK 的工具层。

如果不先把 `skill_loader` 抽成 provider-neutral 核心：

- 你永远只能用 Claude SDK 调主 Agent
- 所有 OpenAI-compatible provider 只能停留在“未来支持”

---

## 5.4 第三优先级：定义统一的 Turn Backend

建议新增一个对话后端接口：

```python
class AgentBackend(Protocol):
    async def run_turn(self, request: TurnRequest) -> TurnResult: ...
```

`TurnRequest` 至少包含：

- system_prompt
- conversation/history
- current user message
- tool specs
- cwd / session metadata
- hook / streaming callback

`TurnResult` 至少包含：

- text
- tool_calls
- usage
- reasoning_content / thinking_blocks
- raw provider metadata

然后实现三种 backend：

### `ClaudeSDKCompatBackend`

用途：

- 封装现有 `query()` 路径
- 保证当前功能不被破坏

### `OpenAIChatBackend`

用途：

- 支持 OpenRouter、DashScope、Moonshot、DeepSeek、Groq、vLLM、LM Studio、Ollama 等大部分 OpenAI-compatible provider

### `AnthropicMessagesBackend`

用途：

- 支持 Anthropic 官方 API
- 未来避免把 Claude 绑定在 SDK 上

### 为什么不建议一开始就做更多 backend

因为按协议族群看，前两个半就够覆盖大多数目标：

- `openai_chat` 覆盖大多数厂商和本地服务
- `anthropic_messages` 覆盖 Claude 官方 API
- `claude_sdk_compat` 作为过渡和 Sub-Agent 兼容层

---

## 5.5 第四优先级：引入按角色路由，而不是全局一个模型

这点要学 Hermes。

`evopaw` 未来不应该只有：

- `planner_model`
- `sub_agent_model`

而应该像这样：

```yaml
providers:
  claude_sdk:
    runtime_family: claude_sdk_compat
    model: claude-sonnet-4-6

  anthropic:
    runtime_family: anthropic_messages
    api_key_env: ANTHROPIC_API_KEY
    api_base: https://api.anthropic.com

  openrouter:
    runtime_family: openai_chat
    api_key_env: OPENROUTER_API_KEY
    api_base: https://openrouter.ai/api/v1

  moonshot:
    runtime_family: openai_chat
    api_key_env: MOONSHOT_API_KEY
    api_base: https://api.moonshot.ai/v1

  dashscope:
    runtime_family: openai_chat
    api_key_env: QWEN_API_KEY
    api_base: https://dashscope.aliyuncs.com/compatible-mode/v1

roles:
  main: claude_sdk
  subagent: claude_sdk
  compression: dashscope
  embedding: dashscope
  fallback: openrouter
```

### 好处

1. 主 Agent 和记忆压缩不必绑在同一个厂商
2. 你能单独切换某个角色，而不用全局大手术
3. 与 `evopaw` 当前“记忆层已用 DashScope”的现实更一致

---

## 5.6 第五优先级：统一参数策略，不要让 provider-specific 字段泄漏

这是从 Hermes 的问题里学到的。

一旦 `evopaw` 支持多个 provider，就会遇到这些问题：

- 有的 provider 接受 `max_tokens`
- 有的 provider 更偏向 `max_completion_tokens`
- 有的 provider 支持额外的 `extra_body`
- 有的 provider 不接受 OpenRouter 特有字段
- 有的 gateway 需要 strip model prefix

所以必须有一个参数策略层，例如：

```python
class RequestPolicy:
    token_param_style: str
    strip_model_prefix: bool
    supports_prompt_caching: bool
    supports_reasoning_effort: bool
    extra_body_whitelist: set[str]
```

这样可以避免：

- 把 OpenRouter 的路由字段传给普通 provider
- 把 Anthropic 风格字段传到 OpenAI-compatible endpoint
- 在主逻辑里散布大量 provider-specific if/else

---

## 5.7 第六优先级：对多模态输入做 content builder 抽象

现在 `evopaw` 的图片处理只会生成 Claude 原生 image block。

相关代码：

- `evopaw/agents/main_agent.py:140-151`

这意味着：

- Claude SDK 路径没问题
- 但 OpenAI-compatible provider 要求的通常是 `image_url` 或 OpenAI content block 结构

因此需要引入：

```text
evopaw/content_builders/
├── claude_blocks.py
├── openai_blocks.py
└── anthropic_blocks.py
```

这样主 Agent 根据 `runtime_family` 选择 content builder，而不是把 Claude block 写死在主循环里。

---

## 5.8 第七优先级：task 型 Skill 的长期方案要明确

这是最难的部分。

即使主 Agent 迁到 OpenAI-compatible runtime，上层还有一个更深的现实：

> task 型 Skill 现在其实靠的是 Claude SDK 自带的本地工具运行时。

这套运行时不是“任何 provider 都有”的。

所以建议明确分两阶段：

### 阶段 A：先保留 `ClaudeSDKSkillExecutor`

也就是：

- 主 Agent 可以换成多 provider
- 但 task 型 Skill 仍由 Claude SDK sub-agent 执行

这样最小化改造面。

### 阶段 B：如果未来要彻底去 Claude SDK，再做通用本地工具执行器

例如：

```text
evopaw/execution_runtime/
├── shell.py
├── fs_read.py
├── fs_write.py
├── fs_edit.py
├── grep.py
├── glob.py
└── runner.py
```

这一步等价于自己造一个“最小版本地工具运行时”，成本明显高于 provider resolver。

所以不应在第一阶段强推。

---

## 六、推荐的分阶段实施路线

下面给出我认为最适合 `evopaw` 的实施顺序。

## Phase 1：抽象配置与 resolver，不改主能力

目标：

- 引入 `provider_runtime/`
- 允许在配置里声明多个 provider
- 先不改变主 Agent 对 Claude SDK 的依赖

完成后收益：

- 配置不再只有一个模型字符串
- 记忆层、主 Agent、Sub-Agent 有统一 runtime 入口

## Phase 2：拆 `skill_loader` 核心逻辑与适配器

目标：

- Skill 注册表、Skill 指令生成、Skill 分发逻辑 provider-neutral
- 当前只保留 `claude_mcp` 适配器

完成后收益：

- 为 OpenAI/Anthropic 直接工具调用打基础

## Phase 3：新增 `OpenAIChatBackend`

目标：

- 主 Agent 可直接跑 OpenRouter / Moonshot / DashScope / 本地 vLLM
- 先覆盖最常见 OpenAI-compatible provider

完成后收益：

- `evopaw` 真正具备“多 provider 主对话运行时”

## Phase 4：新增 `AnthropicMessagesBackend`

目标：

- Claude 官方 API 不再等价于 Claude SDK
- 将 Claude SDK 从“唯一主运行时”降级为“兼容后端”

完成后收益：

- Claude 与其他 provider 在架构层面平权

## Phase 5：决定是否替换 Sub-Agent 运行时

目标：

- 评估是否继续保留 Claude SDK skill executor
- 如有必要，再引入通用本地工具执行器

完成后收益：

- 才有可能彻底摆脱 Claude Agent SDK 的硬依赖

---

## 七、我对 EvoPaw 的最终建议

如果你问我最务实的建议是什么，我会给这几条。

### 1. 不要一开始追求“支持很多厂商”，先支持“少数协议族”

第一批只做：

- `claude_sdk_compat`
- `openai_chat`
- `anthropic_messages`

这已经足够覆盖绝大多数你真正想接的模型。

### 2. 不要先改 Skill 执行器，先改主 Agent runtime

主 Agent 只有一个外部接口 `skill_loader`，可替换性比 task sub-agent 高得多。  
先把主 Agent 多 provider 化，性价比最高。

### 3. 设计上更应该像 nanobot，运营上更应该像 Hermes

解释一下：

- 核心代码结构要像 nanobot：简单、清晰、registry 驱动、provider-neutral
- 角色分工和运行时路由要像 Hermes：main/delegation/aux/fallback 分开配置

### 4. 把“custom OpenAI-compatible endpoint”做成一等公民

这是两边都做对了的地方。

`evopaw` 不应该未来每接一个本地模型或平台代理都加一个新分支。  
最好的设计是：

- 具名 provider 是常见快捷方式
- `custom` 是通用兜底

### 5. 把 provider-specific 参数白名单化

这一条非常重要。  
多 provider 系统最容易出错的地方不是模型名，而是请求体细节。

必须集中管理：

- 哪些字段谁能收
- 哪些模型要覆写 temperature
- 哪些 gateway 需要 strip model prefix
- 哪些 provider 接受 reasoning 开关

### 6. 把“多 provider”视为架构改造，不是 SDK 替换

`evopaw` 当前不是简单地“换 SDK”就能多 provider。

真正需要改造的是：

- 运行时解析层
- Tool adapter
- 主对话 backend
- 多模态内容构造
- Sub-Agent 的长期执行策略

只换一层 SDK，不会自动得到多 provider。

---

## 八、最终判断

### Hermes-Agent 给你的最大启发

不是“支持的 provider 多”，而是：

> 一个成熟 Agent Runtime 必须具备运行时解析、按角色路由、协议模式选择和 provider-specific 策略隔离。

### Nanobot 给你的最大启发

不是“代码轻”，而是：

> 多 provider 的根本不是写更多分支，而是用一个统一接口和一个集中 registry 把复杂性收敛起来。

### 对 EvoPaw 的最终路线建议

最推荐的路线是：

1. 先引入 **Nanobot 风格**的 provider registry + provider-neutral interface
2. 再引入 **Hermes 风格**的 runtime resolver + role-based routing
3. 过渡期保留 `ClaudeSDKCompatBackend`
4. 优先新增 `OpenAIChatBackend`
5. 最后再决定是否替换 task Skill 的 Claude SDK sub-agent

换句话说：

> `evopaw` 不应该直接从“Claude SDK 单一路径”跳到“支持 20 家 provider 的复杂运行时”；它应该先变成一个有清晰协议边界的 agent runtime，然后多 provider 才会自然发生。

---

## 参考资料

### Hermes-Agent

- README: https://github.com/NousResearch/hermes-agent/blob/main/README.md
- Configuration: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md
- FAQ: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/faq.md
- CLI config example: https://raw.githubusercontent.com/NousResearch/hermes-agent/main/cli-config.yaml.example
- Runtime resolver: https://raw.githubusercontent.com/NousResearch/hermes-agent/main/hermes_cli/runtime_provider.py
- Issue #8591: https://github.com/NousResearch/hermes-agent/issues/8591
- Issue #10622: https://github.com/NousResearch/hermes-agent/issues/10622
- Issue #12381: https://github.com/NousResearch/hermes-agent/issues/12381
- Issue #5875: https://github.com/NousResearch/hermes-agent/issues/5875

### Nanobot

- README / repo: https://github.com/HKUDS/nanobot
- Configuration: https://github.com/HKUDS/nanobot/blob/main/docs/configuration.md
- Provider Registry: https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/providers/registry.py
- Provider base interface: https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/providers/base.py
- Agent loop: https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/agent/loop.py

### EvoPaw 本地代码参考

- `evopaw/main.py`
- `evopaw/llm/claude_client.py`
- `evopaw/agents/main_agent.py`
- `evopaw/agents/skill_agent.py`
- `evopaw/tools/skill_loader.py`
- `evopaw/memory/context_mgmt.py`
- `evopaw/memory/indexer.py`
