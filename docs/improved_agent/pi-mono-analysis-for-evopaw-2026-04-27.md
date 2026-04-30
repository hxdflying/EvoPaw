# Pi Monorepo 深度分析，以及对 EvoPaw 多 Provider 与 Agent Runtime 改造的启发

日期：2026-04-27  
分析对象：[`badlogic/pi-mono`](https://github.com/badlogic/pi-mono)  
源码快照：`fbb5eed1910ecc0af86ec8f56e050f7e6b03479f`，提交时间 `2026-04-27 23:49:14 +0200`  
本地分析路径：`/tmp/pi-mono`  
目标：理解 Pi 系列项目的设计，重点分析 README `Packages` 小节中的各包，并判断它们和 EvoPaw 的关系，尤其是多 provider 改造、agent 基础设施、缓存/上下文管理、工具运行时和自托管模型部署。

---

## 0. 结论先行

`pi-mono` 不是单一 coding agent，而是一组围绕 agent runtime 的 TypeScript 包：

- `@mariozechner/pi-ai`：统一多 provider LLM API。
- `@mariozechner/pi-agent-core`：状态化 agent loop、工具执行、事件流。
- `@mariozechner/pi-coding-agent`：完整终端 coding agent CLI，也是 Pi 生态的产品化入口。
- `@mariozechner/pi-mom`：Slack bot，把消息委派给 Pi agent。
- `@mariozechner/pi-tui`：终端 UI 框架。
- `@mariozechner/pi-web-ui`：Web AI chat 组件。
- `@mariozechner/pi-pods` / `@mariozechner/pi`：vLLM/GPU pod 管理工具。

对 EvoPaw 最重要的是前三个，尤其是 `pi-ai` 和 `pi-agent-core`。

我的判断：

1. **Pi 的多 provider 抽象比 go-tiny-claw 成熟很多，也比 Nanobot 更接近生产工程。**  
   它把 `provider`、`api`、`model metadata`、`compat policy`、`stream events`、`usage/cost`、`thinking`、`image`、`tool calls` 都纳入统一模型。

2. **Pi 的 agent runtime 思路很适合 EvoPaw 借鉴，但不建议短期把 EvoPaw 底层直接替换成 Pi。**  
   EvoPaw 是 Python + Feishu + Claude Agent SDK + Skills + 容器隔离；Pi 是 TypeScript/Node 生态。直接嵌入会引入跨语言 sidecar、凭证同步、事件协议、安全边界和部署复杂度。

3. **Pi 最值得 EvoPaw 学的是“内部标准消息/工具 IR + API registry + compat policy + event stream”。**  
   这正好补齐我们在 Hermes/Nanobot/go-tiny-claw 分析里提出的 `AgentBackend`、`ProviderRegistry`、`ToolBridge`、`content builder`。

4. **Pi 不应该替代 EvoPaw 的记忆层。**  
   Pi 有 prompt cache 选项、sessionId、JSONL session、compaction 和 branch summary，但它不是一个长期记忆/语义检索系统。EvoPaw 的 `ctx.json + raw.jsonl + pgvector` 仍应保留。Pi 的缓存语义可以借鉴到 provider runtime，但不应把 EvoPaw 底层记忆“缓存到 Pi 系列”。

5. **Pi-pods 对 EvoPaw 很有实际价值。**  
   如果 EvoPaw 要接本地/自托管 Qwen、GLM、GPT-OSS、DeepSeek 等模型，`pi-pods` 提供的是“把 GPU pod 变成 OpenAI-compatible endpoint”的运维工具。这可以作为 EvoPaw `openai_compatible` provider 的底层模型供应，而不是替换 EvoPaw agent runtime。

6. **推荐路线：参考 Pi 的结构，在 Python 内部实现 EvoPaw 自己的 provider runtime；Pi 作为可选 sidecar 或自托管模型基础设施，而不是核心依赖。**

一句话概括：

> Pi 系列对 EvoPaw 的最大价值，不是“拿来替换 Claude SDK”，而是提供了一套已经跑通的 agent runtime 分层样板：多 provider API 层、状态化 agent loop、事件流、工具执行 hooks、session tree、compaction、扩展系统和本地模型部署。

---

## 1. 项目概览

`badlogic/pi-mono` README 给出的定位是：用于构建 AI agents 和管理 LLM 部署的工具集合。GitHub 页面在本次检索时显示该仓库约 `41.5k` stars、`4.8k` forks、默认分支 `main`，语言以 TypeScript 为主。

根目录 README 的 `Packages` 表列出 7 个包：

| 包 | README 描述 | 我对定位的判断 |
|---|---|---|
| [`@mariozechner/pi-ai`](https://github.com/badlogic/pi-mono/tree/main/packages/ai) | Unified multi-provider LLM API | 多 provider LLM 抽象层，最值得 EvoPaw 学 |
| [`@mariozechner/pi-agent-core`](https://github.com/badlogic/pi-mono/tree/main/packages/agent) | Agent runtime with tool calling and state management | 状态化 agent loop，类似我们计划中的 `AgentBackend` + `ToolBridge` |
| [`@mariozechner/pi-coding-agent`](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent) | Interactive coding agent CLI | 产品化 coding agent，含 TUI、session、skills、extensions、RPC/SDK |
| [`@mariozechner/pi-mom`](https://github.com/badlogic/pi-mono/tree/main/packages/mom) | Slack bot that delegates messages to pi coding agent | Slack 版“聊天入口 + agent 委派”，和 EvoPaw 的飞书入口最相似 |
| [`@mariozechner/pi-tui`](https://github.com/badlogic/pi-mono/tree/main/packages/tui) | Terminal UI library with differential rendering | 终端 UI 基础设施，EvoPaw 直接价值低 |
| [`@mariozechner/pi-web-ui`](https://github.com/badlogic/pi-mono/tree/main/packages/web-ui) | Web components for AI chat interfaces | Web chat/artifacts 组件，可启发 TestAPI/管理台 |
| [`@mariozechner/pi-pods`](https://github.com/badlogic/pi-mono/tree/main/packages/pods) | CLI for managing vLLM deployments on GPU pods | 自托管模型部署层，对 EvoPaw 接本地模型有价值 |

根 `package.json` 使用 npm workspaces，而不是 pnpm。源码快照中各核心包版本为 `0.70.5`，根包自身版本为 `0.0.3`。GitHub 页面 release 区域在本次浏览时显示 latest release `v0.70.3`，说明源码主干和公开 release 标记可能存在短时差异；后续落地时应锁定具体 npm 版本或 git commit。

---

## 2. `@mariozechner/pi-ai`：统一多 Provider LLM API

### 2.1 这个包解决什么问题

`pi-ai` 是 Pi 系列最核心的底层包。它的 README 把自己定义为：

- 统一 LLM API。
- 自动模型发现。
- provider 配置。
- token/cost tracking。
- 简单 context persistence。
- 同一会话中跨模型/跨 provider handoff。

一个关键约束是：它只收录支持 tool calling/function calling 的模型，因为 Pi 的目标是 agentic workflow。

这点对 EvoPaw 很重要。EvoPaw 的主 Agent 当前只有一个 `skill_loader` MCP 工具；如果未来支持非 Claude runtime，所选模型必须稳定支持工具调用，否则主 Agent 无法调 Skill。

### 2.2 Provider 覆盖面

README 当前列出的 provider 包括：

- OpenAI
- Azure OpenAI Responses
- OpenAI Codex
- DeepSeek
- Anthropic
- Google
- Vertex AI
- Mistral
- Groq
- Cerebras
- Cloudflare Workers AI
- xAI
- OpenRouter
- Vercel AI Gateway
- MiniMax
- GitHub Copilot
- Google Gemini CLI
- Antigravity
- Amazon Bedrock
- OpenCode Zen
- OpenCode Go
- Fireworks
- Kimi For Coding
- Any OpenAI-compatible API：Ollama、vLLM、LM Studio 等

源码 `packages/ai/src/models.generated.ts` 中，本次快照统计到：

- 26 个 provider 顶层条目。
- 905 个模型条目。

这比我们之前分析的 Nanobot 和 Hermes 都更“当前化”：Pi 不只是抽象 provider，还维护了大量模型 metadata，包括 `contextWindow`、`maxTokens`、`input`、`reasoning`、`cost`、`baseUrl`、`api`。

### 2.3 Pi 的核心抽象：Provider、API、Model 三分离

`pi-ai` 的关键设计不是“每个厂商写一套逻辑”，而是把概念拆成三层：

#### Provider

`provider` 是厂商品牌或模型来源，例如：

- `anthropic`
- `openai`
- `deepseek`
- `openrouter`
- `amazon-bedrock`
- `kimi-coding`
- `cloudflare-workers-ai`

#### API

`api` 是请求协议或 transport family，例如：

- `anthropic-messages`
- `openai-completions`
- `openai-responses`
- `azure-openai-responses`
- `openai-codex-responses`
- `google-generative-ai`
- `google-gemini-cli`
- `google-vertex`
- `mistral-conversations`
- `bedrock-converse-stream`

这和 Hermes 的 `api_mode` 非常接近，也和我们文档里建议的 `runtime_family` 一致。

#### Model

`Model<TApi>` 是运行时真正使用的对象。源码中的字段包括：

```typescript
interface Model<TApi extends Api> {
  id: string;
  name: string;
  api: TApi;
  provider: Provider;
  baseUrl: string;
  reasoning: boolean;
  input: ("text" | "image")[];
  cost: {
    input: number;
    output: number;
    cacheRead: number;
    cacheWrite: number;
  };
  contextWindow: number;
  maxTokens: number;
  headers?: Record<string, string>;
  compat?: ...;
}
```

这比 EvoPaw 当前的 `planner_model: "claude-sonnet-4-6"` 和 `sub_agent_model: "claude-haiku-4-5"` 强很多。Pi 的模型不是一个字符串，而是“模型能力 + 成本 + 协议 + endpoint + 兼容策略”的组合。

### 2.4 统一消息与内容块

`pi-ai` 定义了统一消息结构：

- `UserMessage`
- `AssistantMessage`
- `ToolResultMessage`

内容块包括：

- `TextContent`
- `ThinkingContent`
- `ImageContent`
- `ToolCall`

`AssistantMessage` 自带：

- `api`
- `provider`
- `model`
- `usage`
- `stopReason`
- `errorMessage`
- `responseId`

这正是 EvoPaw 多 provider 改造缺的内部 IR。当前 EvoPaw 在 `main_agent.py` 里直接处理 Claude SDK 的 `AssistantMessage / ResultMessage / ToolUseBlock`，没有 provider-neutral message schema。

Pi 的做法说明：多 provider 不是先写很多 `if provider == ...`，而是先把所有 provider 的输出归一到统一 message/content/tool/usage 结构。

### 2.5 Streaming event protocol

`pi-ai` 的事件协议非常完整：

- `start`
- `text_start`
- `text_delta`
- `text_end`
- `thinking_start`
- `thinking_delta`
- `thinking_end`
- `toolcall_start`
- `toolcall_delta`
- `toolcall_end`
- `done`
- `error`

这对 EvoPaw 的 verbose 模式很有启发。当前 EvoPaw verbose 依赖 Claude SDK hooks，把 `PreToolUse/PostToolUse` 推送到飞书。未来如果接 OpenAI-compatible provider，需要一个 provider-neutral event stream：

- 模型开始响应
- 文本增量
- thinking/reasoning 增量
- tool call 参数增量
- tool call 完成
- tool result
- usage/cost
- 错误/中止

Pi 已经把这套事件定义为底层协议。

### 2.6 Tool calling 与 TypeBox

Pi 使用 TypeBox 定义工具参数 schema，并提供工具参数校验：

- 工具定义使用 JSON Schema 风格。
- streaming 时支持 partial JSON tool arguments。
- 完整 tool call 后可以校验参数。
- 校验失败作为 tool error 返回给模型，让模型重试。

这比 EvoPaw 当前 `skill_loader` 的 `{"skill_name": str, "task_context": str}` 更强。EvoPaw 当前把 `task_context` 作为字符串或 JSON 透传，缺少 per-skill 参数 schema 的 runtime 校验。

但 EvoPaw 也有自己的优势：Skills 是 Markdown + 脚本生态，更适合飞书工作助手。建议不是照搬 TypeBox，而是在 Python 中引入等价的 JSON Schema/Pydantic 校验层。

### 2.7 Reasoning / Thinking 抽象

Pi 对 reasoning 做了两层支持：

- 统一接口：`reasoning: "minimal" | "low" | "medium" | "high" | "xhigh"`。
- provider-specific options：OpenAI `reasoningEffort`、Anthropic `thinkingEnabled/thinkingBudgetTokens`、Google `thinking.budgetTokens` 等。

这正好对应 EvoPaw 未来的 `thinking_level` 配置。我们不应该在主逻辑里写：

- OpenAI 用 `reasoning_effort`
- Anthropic 用 `thinking`
- Google 用另一个字段

而应该像 Pi 一样，在 provider policy 层把统一 reasoning level 映射到 provider 参数。

### 2.8 Compatibility policy：Pi 最值得借鉴的部分之一

`pi-ai` 对 OpenAI-compatible endpoint 的细节处理非常完整。`OpenAICompletionsCompat` 包括：

- 是否支持 `store`
- 是否支持 `developer` role
- 是否支持 `reasoning_effort`
- reasoning level 映射
- 是否支持 streaming usage
- 使用 `max_completion_tokens` 还是 `max_tokens`
- tool result 是否要求 `name`
- tool result 后是否需要补 assistant message
- thinking 是否要转成文本
- replay assistant message 是否要带空 `reasoning_content`
- thinking 参数格式：`openai`、`openrouter`、`deepseek`、`zai`、`qwen`、`qwen-chat-template`
- OpenRouter routing
- Vercel AI Gateway routing
- 是否支持 strict tool schema
- Anthropic 风格 cache control
- session affinity headers
- long cache retention

这基本就是我们从 Hermes issue 中总结出的风险：provider-specific 参数不能污染主逻辑，必须集中白名单化和策略化。

Pi 已经把这些问题抽象为 `compat`。EvoPaw 的 provider runtime 应直接学习这个结构。

### 2.9 Context serialization 与跨 provider handoff

Pi 的 `Context` 可 JSON 序列化，包含：

- `systemPrompt`
- `messages`
- `tools`

它支持同一 conversation 从 Claude 切到 GPT，再切到 Gemini。不同 provider 的 thinking blocks 会转换为文本标签；tool calls 和 tool results 会保留。

这对 EvoPaw 很关键。EvoPaw 当前每次 Claude SDK `query()` 是独立 session，历史通过 `_format_history()` 拼成纯文本。这个方式短期简单，但多 provider 之后会丢掉：

- tool call 结构
- tool result 结构
- reasoning/thinking
- image blocks
- usage/cost
- provider/model metadata

如果 EvoPaw 要支持真正的跨 provider runtime，就需要从“拼历史文本”升级为“结构化 message transcript”。Pi 已经证明这条路可行。

### 2.10 Prompt cache 与 sessionId

Pi 的 `StreamOptions` 包含：

- `cacheRetention: "none" | "short" | "long"`
- `sessionId`

部分 provider 可利用这些字段做 prompt cache、session affinity 或请求路由。例如 OpenAI/Anthropic/Cloudflare 等 provider 会有不同的缓存或 session header 语义。

这不是长期记忆，也不是 EvoPaw 的 pgvector 替代品。它更像“provider 请求层缓存优化”。EvoPaw 可以借鉴：

- 为每个 `routing_key` 或 `session_id` 生成稳定 provider `sessionId`。
- 在 role routing 配置里允许 `cache_retention`。
- usage 中记录 `cacheRead/cacheWrite`。

但不要把 EvoPaw 的 `ctx.json`、`raw.jsonl`、pgvector 迁到 Pi 的 prompt cache。两者解决的问题不同。

---

## 3. `@mariozechner/pi-agent-core`：状态化 Agent Loop

### 3.1 这个包解决什么问题

`pi-agent-core` 建立在 `pi-ai` 之上，提供：

- `Agent` 类。
- agent state。
- event streaming。
- tool execution。
- stateful messages。
- steering/follow-up queue。
- context transform。
- tool preflight/postprocess hooks。

它相当于 EvoPaw 未来 `AgentBackend` + `ToolBridge` + `HookDispatcher` 的一个 TypeScript 参考实现。

### 3.2 AgentMessage 与 LLM Message 分离

Pi 的核心设计是：

```text
AgentMessage[] -> transformContext() -> AgentMessage[] -> convertToLlm() -> Message[] -> LLM
```

`AgentMessage` 可以包含 UI、extension、自定义消息；LLM 只理解 `user / assistant / toolResult`。`convertToLlm` 是边界。

这对 EvoPaw 很适合。EvoPaw 有大量不应该直接进入 LLM 的系统信息：

- 飞书 routing metadata
- root_id
- verbose/progress 事件
- session_id 安全信息
- 审计日志
- API/TestAPI capture metadata
- 未来 permission approval 状态

Pi 的分层说明：EvoPaw 应区分“系统内部事件/消息”和“发给模型的 LLM message”。

### 3.3 Event flow

Agent 事件包括：

- `agent_start`
- `agent_end`
- `turn_start`
- `turn_end`
- `message_start`
- `message_update`
- `message_end`
- `tool_execution_start`
- `tool_execution_update`
- `tool_execution_end`

这比 EvoPaw 当前 hooks 更通用。EvoPaw 后续可以定义自己的 `AgentEvent`，由 FeishuSender、TestAPI、metrics、audit、debug verbose 共同消费。

### 3.4 工具执行模式

Pi 支持：

- `parallel`：默认。先顺序 preflight，再并发执行允许的工具，最后按 assistant 原始 tool call 顺序写入 tool result message。
- `sequential`：逐个执行。
- per-tool `executionMode` 可强制某个工具要求顺序。

这对 EvoPaw 的 task Skill 很有启发。当前 EvoPaw task Skill 交给 Claude SDK sub-agent，工具执行由 Claude Code 内部完成；我们无法细粒度控制并发、preflight、postprocess。未来如果做 provider-neutral tool runtime，Pi 的执行模式值得借鉴：

- 读类工具可并发。
- 写类工具应进入文件级 mutation queue。
- bash 或外部副作用工具默认顺序或需要 permission gate。

### 3.5 beforeToolCall / afterToolCall

Pi 的 hook 设计直接对应 EvoPaw 的权限门控需求：

- `beforeToolCall`：参数校验之后、执行之前，可阻断。
- `afterToolCall`：执行之后、发出最终 tool result 之前，可改写 result、标记 error、终止后续 LLM follow-up。

EvoPaw 已有 `hooks.py` 做 Claude SDK verbose，但缺少 provider-neutral permission hook。可以借鉴 Pi：

```python
before_tool_call(tool_call, args, context) -> Allow | Block | Ask
after_tool_call(tool_call, result, context) -> ToolResultOverride
```

在飞书场景，`Ask` 还需要转成确认卡片和一次性授权 token；这属于 EvoPaw 的 gateway/approval 层，不是 Pi 已经内建的。

### 3.6 Steering 与 follow-up

Pi 区分两类排队消息：

- steering：当前 assistant turn 和工具调用完成后注入，用来纠偏。
- follow-up：agent 完全结束后再执行。

EvoPaw 当前是 per-routing_key 队列，同一个 session 串行；用户在 agent 工作时再次发消息，会排在下一轮。Pi 的 steering/follow-up 可以作为未来增强：

- 飞书用户追加“等等，改成这样”时，可以作为 steering。
- “做完后顺便总结”可以作为 follow-up。

短期不必实现，但这是一个比简单 FIFO 更细的交互模型。

---

## 4. `@mariozechner/pi-coding-agent`：完整 Coding Agent 产品

### 4.1 运行模式

`pi-coding-agent` 是 Pi 的主产品。它支持：

- interactive TUI
- print 模式
- JSONL event stream
- RPC mode
- SDK embedding

对 EvoPaw 来说，最有价值的是 JSON/RPC/SDK 思路。EvoPaw 的 TestAPI 和未来 gateway 可以参考 Pi：把 agent 事件标准化成 JSONL/RPC 协议，让非 Python 进程也能接入。

### 4.2 Provider & Model 管理

Pi 维护 tool-capable 模型列表，并支持：

- `/login` OAuth
- `/model` 切换模型
- `models.json` 自定义 provider/model
- extension 动态 `registerProvider()`
- provider-level 和 model-level `compat`
- `modelOverrides`
- 凭证解析优先级

这比 EvoPaw 当前配置强很多。EvoPaw 当前缺少：

- provider block
- model metadata
- role routing
- auth source priority
- custom provider
- provider-specific policy

Pi 的 `models.json` 和 `registerProvider()` 对 EvoPaw 的 `provider_runtime` 很有参考价值。

### 4.3 Sessions：树形 JSONL

Pi session 存为 JSONL，每条 entry 有 `id / parentId`，形成树结构。它支持：

- resume
- fork
- clone
- `/tree` 跳转到历史节点
- branch summary
- model change entry
- thinking level change entry
- compaction entry
- custom entry

EvoPaw 当前 session 结构更简单：`data/sessions/{sid}.jsonl` 是消息对，另有 `ctx.json/raw.jsonl`。对飞书助手来说，树形 branching 不是 P0，但 Pi 的两个点很值得学：

1. **所有 agent 事件都用 append-only JSONL 记录。**
2. **session 中保留 provider/model/usage/tool result/compaction 等结构化信息。**

EvoPaw 当前 `MessageEntry` 只保存 role/content，后续多 provider 后应扩展成结构化 transcript。

### 4.4 Compaction

Pi compaction 机制比 EvoPaw 当前 `maybe_compress()` 更完整：

- 根据 `contextWindow - reserveTokens` 触发。
- 保留最近 token budget。
- 不在 tool result 处切断。
- 支持 split turn。
- summary 结构包含 goal、constraints、progress、decisions、next steps、critical context、read files、modified files。
- branch summary 与 compaction 共用结构。

EvoPaw 当前 `context_mgmt.py` 有硬编码 `_MODEL_CTX_LIMIT = 32000` 的问题。Pi 的做法说明：

- context limit 应来自 `model.contextWindow`。
- summary 不是随意短文本，应有固定结构。
- compaction entry 应记录 `tokensBefore`、`firstKeptEntryId`、文件引用等恢复信息。

### 4.5 Skills、Prompt Templates、Extensions、Pi Packages

Pi 的扩展面分四类：

- Prompt Templates：Markdown prompt 模板。
- Skills：Agent Skills 标准，`SKILL.md`。
- Extensions：TypeScript 模块，可注册工具、命令、快捷键、UI、事件 hook、provider。
- Pi Packages：把 extensions、skills、prompts、themes 打包成 npm/git 资源。

这和 EvoPaw 的 Skills 很像，但 Pi 更开放，也更危险。Pi README 明确提示：Pi packages 运行时有完整系统权限，extensions 可执行任意代码，skills 可指示模型执行任意动作。

EvoPaw 不应该照搬 Pi packages 的信任模型。飞书工作助手面对真实组织数据，应保持：

- 默认禁用第三方代码执行。
- Skill 安装需要审计/白名单。
- 凭证不进入 LLM。
- task Skill 运行在容器或隔离 cwd。

### 4.6 哲学取舍：No MCP、No sub-agents、No permission popups

Pi 的哲学很鲜明：

- 不内建 MCP。
- 不内建 sub-agents。
- 不内建 permission popups。
- 不内建 plan mode。
- 不内建 todos。
- 不内建 background bash。

这些能力都交给 extensions、skills、tmux、容器或用户自己实现。

这和 EvoPaw 不同。EvoPaw 的核心价值正是：

- 飞书入口。
- Skills 渐进式披露。
- task Skill sub-agent。
- 容器隔离。
- 工作助手能力。

所以 EvoPaw 不能整体采用 Pi 的产品哲学。我们可以学习 Pi 的底层抽象，但保留 EvoPaw 的产品约束。

---

## 5. `@mariozechner/pi-mom`：Slack Bot 与 EvoPaw 最像的应用层

`pi-mom` 是 Slack bot，把 Slack 消息委派给 Pi coding agent。它和 EvoPaw 的相似点很强：

| 维度 | Pi Mom | EvoPaw |
|---|---|---|
| 消息入口 | Slack Socket Mode | Feishu WebSocket |
| 会话维度 | 每个 channel/DM 独立目录 | `routing_key`：p2p/group/thread |
| 历史 | `log.jsonl` 源事实 + `context.jsonl` LLM 上下文 | `data/sessions/*.jsonl` + `ctx/raw` |
| 附件 | channel attachments 目录 | session uploads 目录 |
| 记忆 | global/channel `MEMORY.md` | Bootstrap memory + ctx + pgvector |
| 工具 | bash/read/write/edit/attach | skill_loader + sub-agent tools |
| 隔离 | 推荐 Docker sandbox | Docker/Claude built-in workspace |

Pi Mom 的几个设计对 EvoPaw 很有价值：

1. **log 与 context 分离。**  
   `log.jsonl` 是完整频道历史，`context.jsonl` 是发给模型的上下文。EvoPaw 当前也有 `raw.jsonl` 和 `ctx.json`，但 session history/message pair 还不够结构化。

2. **每个 channel 有独立目录。**  
   这与 EvoPaw `routing_key -> session_id -> workspace/sessions/{sid}` 一致。

3. **旧历史通过 grep 搜索。**  
   EvoPaw 已有 `history_reader` 和 pgvector，可以比 Pi Mom 更强。

4. **工作目录中沉淀 skill/tool。**  
   Pi Mom 允许 agent 自己创建 CLI skills。EvoPaw 不应默认允许自我安装工具，但可以在受控 skill-creator 流程中借鉴这种“脚本 + SKILL.md”模式。

Pi Mom 的风险也明显：它强调 self-managing 和 full bash access。EvoPaw 如果服务组织知识库、飞书、定时任务和凭证，不应该默认给 agent 这种自由度。

---

## 6. `@mariozechner/pi-tui`：终端 UI 框架

`pi-tui` 是一个差分渲染的 terminal UI framework。能力包括：

- differential rendering
- synchronized output
- bracketed paste
- component interface
- editor/input/select/markdown/image 等组件
- IME 支持

它对 EvoPaw 主线价值不高，因为 EvoPaw 是飞书机器人，不是终端应用。可能的边缘用途：

- 未来做本地调试 CLI。
- 做独立运维工具或 replay viewer。

短期无需引入。

---

## 7. `@mariozechner/pi-web-ui`：Web AI Chat 组件

`pi-web-ui` 提供：

- Chat UI
- streaming message
- tool execution rendering
- attachments：PDF/DOCX/XLSX/PPTX/images
- artifacts：HTML/SVG/Markdown sandbox
- IndexedDB storage
- provider key store
- settings/session store
- Ollama/LM Studio/vLLM/OpenAI-compatible custom providers

对 EvoPaw 的价值主要在两个方向：

1. **TestAPI/调试 UI。**  
   EvoPaw 当前 TestAPI 是 HTTP 调试服务，没有完整 Web UI。Pi web-ui 的组件结构可以启发一个后台调试界面。

2. **Artifacts。**  
   飞书卡片适合展示短答复和文件链接，不适合复杂 HTML artifact。Pi web-ui 的 artifacts panel 可作为未来“结果查看页”的参考。

但它是前端组件库，不应进入 P0 多 provider 改造。

---

## 8. `@mariozechner/pi-pods` / `@mariozechner/pi`：自托管 vLLM 模型部署层

`pi-pods` 的定位是管理 GPU pods 上的 vLLM 部署。README 强调它可以：

- 在 Ubuntu GPU pod 上安装配置 vLLM。
- 为 Qwen、GPT-OSS、GLM 等 agentic 模型配置 tool calling。
- 多模型共享 GPU，自动分配显存。
- 为每个模型提供 OpenAI-compatible API endpoint。
- 附带 agent CLI 测试模型工具调用能力。

这对 EvoPaw 很实际。EvoPaw 多 provider 改造中，`openai_compatible` 是最重要的 runtime family。如果我们要跑：

- Qwen Coder
- GLM-4.5/GLM-Air
- DeepSeek
- GPT-OSS
- 本地 vLLM
- 组织内 GPU pod

那么 `pi-pods` 可以作为“模型供应基础设施”。EvoPaw 不需要知道 GPU 管理细节，只要接：

```yaml
providers:
  local_qwen:
    runtime_family: openai_compatible
    base_url: http://pod-ip:8001/v1
    api_key_env: PI_API_KEY
    model: Qwen/Qwen2.5-Coder-32B-Instruct
```

`pi-pods` 真正帮 EvoPaw 解决的是“怎样把本地/远端开源模型稳定暴露成 OpenAI-compatible endpoint”，不是 agent runtime。

---

## 9. Pi 与 Hermes、Nanobot、go-tiny-claw 的关系

我们之前已经分析过 Hermes、Nanobot、go-tiny-claw。加入 Pi 后，可以重新定位：

| 项目 | 核心方法 | 优点 | 短板 | 对 EvoPaw 的角色 |
|---|---|---|---|---|
| go-tiny-claw | 极简 `LLMProvider + ToolRegistry + ReAct loop` | 最容易理解，适合教学 | 无 registry、无 resolver、无生产策略 | 理解最小 agent harness |
| Nanobot | `LLMProvider` + provider registry + 少数 backend | 抽象清晰，扩展成本低 | 高级 runtime 策略较少 | provider registry 参考 |
| Hermes | runtime resolver + api_mode + role routing | 角色化路由强，兼容复杂 | 组合态多，provider bug 风险高 | role routing / resolver 参考 |
| Pi | API registry + model metadata + compat policy + agent-core loop + product CLI | 工程完整，覆盖 provider/model/tool/event/session | TypeScript 生态；权限/沙箱靠扩展/容器；产品哲学不同 | 最完整的实现样板，但不宜直接替代 EvoPaw |

Pi 可以看作：

- Nanobot 的 registry 思路更工程化版本。
- Hermes 的 `api_mode` 思路在 TypeScript 里的落地版本。
- go-tiny-claw 的 `LLMProvider + ToolRegistry + Loop` 的生产级扩展版。

但 Pi 缺 Hermes 那种明确的 role routing。Pi 的 model switching 很强，但它主要服务一个 coding agent 的交互场景；EvoPaw 需要的是：

- `main`
- `subagent`
- `compression`
- `embedding`
- `vision`
- `fallback`
- `eval`
- `session_search`

这些 role 应该留在 EvoPaw 自己的配置和 resolver 中。

---

## 10. Pi 与 EvoPaw 的具体关系

### 10.1 EvoPaw 当前核心耦合

EvoPaw 当前架构：

```text
Feishu WebSocket
  -> Runner
  -> Main Agent (Claude Agent SDK query)
  -> skill_loader MCP
  -> task Skill Sub-Agent (Claude Agent SDK query)
  -> FeishuSender
```

关键耦合点：

- `main_agent.py` 直接导入 Claude SDK 的 `query / ResultMessage / ToolUseBlock`。
- `skill_loader.py` 直接使用 Claude SDK 的 `create_sdk_mcp_server / @tool`。
- task Skill 通过 `run_skill_agent()` 再次启动 Claude SDK sub-agent。
- `claude_client.py` 返回 `ClaudeAgentOptions`，且默认 `permission_mode="bypassPermissions"`。
- 多模态图片构造是 Claude 原生 image block。
- 配置只有 `planner_model / sub_agent_model`，没有 provider/model/runtime family。

Pi 的底层分层正好对应这些问题：

| EvoPaw 当前问题 | Pi 中对应能力 | 借鉴方式 |
|---|---|---|
| 模型只是字符串 | `Model` metadata | 引入 `ModelProfile` |
| provider 不可切 | `Provider + Api + Model` | 引入 `runtime_family/api` |
| Claude SDK message 直通 | `Message/Content/ToolCall` IR | 定义 EvoPaw 内部 IR |
| hooks 只绑定 Claude | `AgentEvent` stream | 定义 provider-neutral events |
| 工具暴露绑定 MCP | `Tool` schema + agent-core tool execution | 抽 `skills_runtime` 和 adapters |
| 上下文纯文本拼接 | structured Context | 保留结构化 tool/history |
| 图片只支持 Claude block | `ImageContent` | content builders |
| 无 usage/cost | `Usage` | metrics + budget |
| 无 prompt cache policy | `cacheRetention/sessionId` | provider runtime 传 cache hint |

### 10.2 是否可以把 EvoPaw 底层基于 Pi 系列？

可以有三种理解：

#### 方案 A：完全基于 Pi 重构 EvoPaw runtime

即把 EvoPaw 的主 Agent runtime 替换成 Node/TypeScript 的 `pi-agent-core + pi-ai`，Python 只保留 Feishu gateway。

我不建议短期做。

原因：

- 跨语言边界会变成核心路径：Feishu Python 进程必须和 Node agent sidecar 通过 RPC 交互。
- EvoPaw 的 Skills、凭证、memory、cron、TestAPI、session manager 都在 Python。
- Pi 不内建 MCP，也不内建 sub-agent；EvoPaw 现有 `skill_loader + task Skill` 要重做 adapter。
- Pi 默认权限哲学更开放，安全确认和 path protection 要用 extension 实现。
- Pi 迭代很快，作为核心依赖会引入升级风险。

#### 方案 B：把 Pi 作为可选 Node sidecar backend

可以做原型，但不应作为 P0 主路线。

形式：

```text
EvoPaw Python
  -> AgentBackend interface
    -> ClaudeSDKCompatBackend
    -> OpenAIChatBackend(Python)
    -> PiSidecarBackend(JSON/RPC)
```

优点：

- 快速复用 Pi 的多 provider 能力。
- 可以用 Pi 的 RPC/JSONL event stream。
- 不必立即在 Python 重写所有 provider adapter。

缺点：

- 部署复杂：Python + Node 两套 runtime。
- 凭证同步复杂。
- tool/skill 执行边界复杂。
- 事件协议和错误恢复需要自己定义。
- 安全审计更难。

适合做实验，不适合直接成为生产主路径。

#### 方案 C：学习 Pi，在 Python 内实现 EvoPaw 自己的 provider runtime

这是我推荐的路线。

保留 EvoPaw 的：

- Feishu gateway
- Runner
- SessionManager
- Skills
- memory
- cron
- observability
- container/workspace isolation

新增：

- `provider_runtime`
- provider/model registry
- canonical message/content/tool schema
- `AgentBackend`
- `ToolBridge`
- role routing
- OpenAI-compatible backend
- Anthropic Messages backend

Pi 作为设计参考，而不是核心依赖。

#### 方案 D：使用 Pi-pods 提供本地模型 endpoint

这是最实用、风险最低的直接采用方式。

EvoPaw 不嵌入 Pi agent，只用 `pi-pods` 管理 vLLM，得到 OpenAI-compatible endpoint。EvoPaw 的 `OpenAIChatBackend` 直接调用这些 endpoint。

这个方案推荐作为中期落地点。

### 10.3 是否可以把 EvoPaw 的底层缓存放到 Pi？

如果“缓存”指 prompt cache/provider cache，那么可以借鉴 Pi 的字段和语义：

- `sessionId`
- `cacheRetention`
- `cacheRead/cacheWrite`
- provider session affinity headers

如果“缓存”指长期上下文、历史和记忆，那么不建议。

原因：

- Pi 的 session/compaction 是 agent transcript 管理，不是长期记忆数据库。
- EvoPaw 已有 `ctx.json + raw.jsonl + pgvector`，更适合飞书长期助手。
- Pi 没有替代 pgvector hybrid search 的内建能力。
- EvoPaw 的记忆需要和飞书 routing、用户、群、话题、权限治理结合，不能外包给 Pi 的 agent session。

推荐：

- provider 层学习 Pi 的 prompt cache hints。
- session 层学习 Pi 的结构化 JSONL 和 compaction entry。
- memory 层保留 EvoPaw 自己的架构。

---

## 11. 对 EvoPaw 多 Provider 改造的具体建议

### 11.1 新增内部 IR

建议新增：

```text
evopaw/provider_runtime/
├── messages.py
├── models.py
├── registry.py
├── api_registry.py
├── policy.py
├── resolve.py
└── backends/
    ├── claude_sdk.py
    ├── openai_chat.py
    └── anthropic_messages.py
```

内部结构参考 Pi，但用 Python/Pydantic 或 dataclass：

```python
class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    data: str
    mime_type: str

class ThinkingContent(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str

class ToolCallContent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any]

class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
```

### 11.2 ProviderSpec 与 ModelProfile

参考 Pi 的 `Model`：

```python
class ProviderSpec(BaseModel):
    provider_id: str
    display_name: str
    api: Literal[
        "claude_sdk",
        "openai_chat",
        "openai_responses",
        "anthropic_messages",
        "google_generative_ai",
    ]
    base_url: str | None = None
    api_key_env: str | None = None
    is_gateway: bool = False
    is_local: bool = False

class ModelProfile(BaseModel):
    provider_id: str
    model_id: str
    display_name: str
    api: str
    context_window: int
    max_output_tokens: int
    supports_tool_calls: bool
    supports_vision: bool
    supports_reasoning: bool
    cost_input_per_million: float = 0.0
    cost_output_per_million: float = 0.0
    compat: dict[str, Any] = {}
```

### 11.3 CompatPolicy

Pi 的 `compat` 应成为 EvoPaw provider runtime 的重点参考：

```python
class OpenAICompatPolicy(BaseModel):
    supports_developer_role: bool = True
    supports_reasoning_effort: bool = True
    supports_usage_in_streaming: bool = True
    supports_strict_tools: bool = True
    max_tokens_field: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    requires_tool_result_name: bool = False
    thinking_format: Literal["openai", "deepseek", "zai", "qwen", "qwen_chat_template"] = "openai"
    cache_control_format: Literal["none", "anthropic"] = "none"
    openrouter_routing: dict[str, Any] | None = None
    vercel_gateway_routing: dict[str, Any] | None = None
```

这个层的目标是防止 Hermes 曾经暴露的问题：某个 gateway 的特殊字段泄漏到其他 provider 请求体里。

### 11.4 AgentEvent

参考 Pi 的 event protocol，EvoPaw 可以定义：

```python
class AgentEvent(BaseModel):
    type: Literal[
        "agent_start",
        "turn_start",
        "message_delta",
        "thinking_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
        "tool_result",
        "turn_end",
        "agent_end",
        "error",
    ]
    session_id: str
    routing_key: str
    payload: dict[str, Any]
```

Feishu verbose、TestAPI capture、metrics、audit 都消费这个事件流。

### 11.5 SkillLoader 拆分

现有 `skill_loader.py` 需要拆成 provider-neutral 核心：

```text
evopaw/skills_runtime/
├── registry.py
├── instructions.py
├── dispatcher.py
├── tool_schema.py
└── adapters/
    ├── claude_mcp.py
    ├── openai_tools.py
    └── anthropic_tools.py
```

Pi 的 `Tool`/`AgentTool` 说明工具层应该有：

- name
- description
- schema
- execute
- optional render metadata
- execution mode
- pre/post hooks

EvoPaw 不需要 render metadata，但需要：

- permission category
- allowed tools
- cwd policy
- credential policy
- audit policy

### 11.6 Role routing

Pi 缺少完整 role routing；这里应继续沿用我们从 Hermes 得到的启发：

```yaml
providers:
  claude_sdk:
    api: claude_sdk
    model: claude-sonnet-4-6

  dashscope:
    api: openai_chat
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key_env: QWEN_API_KEY

  local_qwen:
    api: openai_chat
    base_url: http://127.0.0.1:8001/v1
    api_key_env: PI_API_KEY

roles:
  main:
    provider: claude_sdk
    model: claude-sonnet-4-6
    cache_retention: short
  subagent:
    provider: claude_sdk
    model: claude-haiku-4-5
  compression:
    provider: dashscope
    model: qwen-plus
  embedding:
    provider: dashscope
    model: text-embedding-v4
  fallback:
    provider: openrouter
    model: anthropic/claude-sonnet-4
```

### 11.7 Pi-pods 接入方式

如果要用 Pi 系列的实际代码，优先考虑 `pi-pods`：

1. 用 `pi-pods` 在 GPU pod 上启动 Qwen/GLM/GPT-OSS。
2. 得到 OpenAI-compatible endpoint。
3. EvoPaw 通过 `OpenAIChatBackend` 调用。
4. usage/cost 由 EvoPaw 自己记录，模型成本可设为 0 或内部成本。

这样收益高、耦合低。

---

## 12. 推荐实施路线

### Phase 0：只借鉴，不引入依赖

目标：

- 把 Pi 的 `Message/Content/ToolCall/Usage/Model/Compat/Event` 思路整理成 EvoPaw 内部设计。
- 不引入 Node sidecar。
- 不改生产 runtime。

产出：

- `provider_runtime` 数据结构。
- 单元测试覆盖 schema 和 compat policy。

### Phase 1：Provider Registry + Role Resolver

目标：

- EvoPaw 配置从 `planner_model/sub_agent_model` 升级到 provider blocks + roles。
- 仍然使用 Claude SDK 主路径。

收益：

- 记忆压缩、embedding、main、subagent 的模型配置统一。
- 后续新增 provider 有落点。

### Phase 2：OpenAI-compatible Backend

目标：

- 实现 `OpenAIChatBackend`。
- 优先支持 DashScope、OpenRouter、Moonshot、本地 vLLM。
- 借鉴 Pi 的 compat policy，先实现最小字段：
  - `max_tokens_field`
  - `supports_developer_role`
  - `supports_reasoning_effort`
  - `supports_usage_in_streaming`
  - `thinking_format`

收益：

- EvoPaw 主 Agent 能真正使用非 Claude provider。

### Phase 3：Skills Runtime Adapter

目标：

- `skill_loader` 核心 provider-neutral。
- Claude SDK 仍用 MCP adapter。
- OpenAI backend 用 tools schema adapter。
- task Skill 暂时仍走 Claude SDK sub-agent。

收益：

- 主 Agent provider 可切换，task Skill 兼容保留。

### Phase 4：Structured Transcript + Compaction

目标：

- 会话历史从 role/content 升级为结构化 transcript。
- 保存 provider/model/usage/tool calls/tool results。
- compaction 使用 model context window，而不是硬编码。
- 参考 Pi 的 summary 格式。

收益：

- 支持跨 provider handoff。
- 支持 usage/cost 统计。
- verbose/debug/replay 更完整。

### Phase 5：可选 Pi Sidecar 实验

目标：

- 用 `pi --mode rpc` 或自建 Node wrapper，验证能否作为 `PiSidecarBackend`。
- 只在实验配置启用。

收益：

- 快速验证 Pi 的 provider 覆盖面。

限制：

- 不作为默认生产路径。

### Phase 6：Pi-pods / vLLM 自托管模型

目标：

- 用 `pi-pods` 管理一个测试 GPU pod。
- 暴露 Qwen/GLM/GPT-OSS OpenAI-compatible endpoint。
- EvoPaw 通过 `OpenAIChatBackend` 调用。

收益：

- 让 EvoPaw 获得本地/自托管 provider 能力。

---

## 13. 风险与边界

### 13.1 Pi 迭代很快

本次源码快照已经是 `0.70.5` 包版本，而 GitHub release 面板仍显示 `v0.70.3`。这说明 Pi 生态更新非常频繁。若作为核心依赖，必须锁版本和回归测试。

### 13.2 TypeScript/Python 跨语言成本

EvoPaw 当前是 Python 项目，核心异步流、Feishu SDK、memory、cron、TestAPI 都在 Python。引入 Pi 作为核心 runtime 会产生长期维护成本。

### 13.3 权限模型不匹配

Pi 的哲学是不内建 permission popups，推荐用容器或 extensions 自建确认流。EvoPaw 在飞书工作助手场景中必须 fail-closed，不能照搬 Pi 的默认开放工具模型。

### 13.4 Pi packages 安全风险

Pi packages 可以通过 npm/git 安装 extensions、skills、prompts、themes。它们有完整系统权限。EvoPaw 如果未来支持第三方 Skill，也必须有更严格的签名、白名单、审计和沙箱。

### 13.5 Prompt cache 不是长期记忆

Pi 的 `cacheRetention/sessionId` 很有价值，但它只优化 provider 请求，不能替代 EvoPaw 的记忆系统。

### 13.6 模型 metadata 会过期

Pi 维护模型列表是优势，但模型市场变化快。EvoPaw 不应盲目信任外部生成的模型列表，尤其涉及价格、上下文、tool calling 能力时，应允许本地 override。

---

## 14. 最终建议

我建议 EvoPaw 对 Pi 采取“四层态度”：

1. **强学习：`pi-ai` 的 API/Model/Compat/Event 设计。**  
   这是 EvoPaw 多 provider 改造最值得直接借鉴的部分。

2. **中度学习：`pi-agent-core` 的 AgentMessage/LLM Message 分离、工具执行 hooks、事件流、steering/follow-up。**  
   这些适合进入 EvoPaw 长期 runtime 设计，但要按飞书场景裁剪。

3. **选择性学习：`pi-coding-agent` 的 session tree、compaction、extensions、RPC/SDK。**  
   这些对 EvoPaw 有启发，但不是 P0。

4. **可直接使用：`pi-pods` 作为自托管模型部署工具。**  
   它可以帮 EvoPaw 获得 OpenAI-compatible 本地模型 endpoint，耦合最低。

不建议：

- 不建议短期用 Pi 替换 EvoPaw 的 Python agent runtime。
- 不建议把 EvoPaw 记忆/缓存层迁到 Pi。
- 不建议照搬 Pi packages 的安全模型。
- 不建议立即实现 Pi 那样完整的 TUI/extension/package 生态。

推荐的架构方向：

```text
EvoPaw
├── Feishu Gateway（保留）
├── Runner / Session / Cron / Memory（保留）
├── Skills Runtime（拆出 provider-neutral core）
├── Provider Runtime（新建，学习 pi-ai）
│   ├── ClaudeSDKCompatBackend
│   ├── OpenAIChatBackend
│   ├── AnthropicMessagesBackend
│   └── optional PiSidecarBackend
└── Self-hosted Models（可用 pi-pods 暴露 OpenAI-compatible endpoint）
```

最终判断：

> Pi 系列是目前最值得 EvoPaw 参考的 agent runtime 工程样板之一。它比 go-tiny-claw 完整，比 Nanobot 更工程化，比 Hermes 更模块化清晰。但由于语言栈、权限模型和产品哲学不同，EvoPaw 应“吸收 Pi 的抽象，保留自己的运行时”，而不是直接把底层改成 Pi。

---

## 参考资料

### Pi Monorepo

- Repository: https://github.com/badlogic/pi-mono
- Root README: https://github.com/badlogic/pi-mono/blob/main/README.md
- Root `package.json`: https://github.com/badlogic/pi-mono/blob/main/package.json
- `AGENTS.md`: https://github.com/badlogic/pi-mono/blob/main/AGENTS.md
- `CONTRIBUTING.md`: https://github.com/badlogic/pi-mono/blob/main/CONTRIBUTING.md

### Pi packages

- `@mariozechner/pi-ai`: https://github.com/badlogic/pi-mono/tree/main/packages/ai
- `pi-ai` README: https://github.com/badlogic/pi-mono/blob/main/packages/ai/README.md
- `pi-ai` types: https://github.com/badlogic/pi-mono/blob/main/packages/ai/src/types.ts
- `pi-ai` API registry: https://github.com/badlogic/pi-mono/blob/main/packages/ai/src/api-registry.ts
- `pi-ai` model registry: https://github.com/badlogic/pi-mono/blob/main/packages/ai/src/models.ts
- `pi-ai` built-in provider registration: https://github.com/badlogic/pi-mono/blob/main/packages/ai/src/providers/register-builtins.ts
- `pi-ai` generated models: https://github.com/badlogic/pi-mono/blob/main/packages/ai/src/models.generated.ts
- `pi-ai` env API keys: https://github.com/badlogic/pi-mono/blob/main/packages/ai/src/env-api-keys.ts

- `@mariozechner/pi-agent-core`: https://github.com/badlogic/pi-mono/tree/main/packages/agent
- `pi-agent-core` README: https://github.com/badlogic/pi-mono/blob/main/packages/agent/README.md
- `pi-agent-core` types: https://github.com/badlogic/pi-mono/blob/main/packages/agent/src/types.ts
- `pi-agent-core` loop: https://github.com/badlogic/pi-mono/blob/main/packages/agent/src/agent-loop.ts
- `pi-agent-core` Agent class: https://github.com/badlogic/pi-mono/blob/main/packages/agent/src/agent.ts

- `@mariozechner/pi-coding-agent`: https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent
- `pi-coding-agent` README: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md
- Providers docs: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/providers.md
- Models docs: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/models.md
- Custom provider docs: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/custom-provider.md
- Session docs: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/session.md
- Compaction docs: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/compaction.md
- SDK docs: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/sdk.md
- Extensions docs: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md
- Skills docs: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/skills.md
- Packages docs: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/packages.md

- `@mariozechner/pi-mom`: https://github.com/badlogic/pi-mono/tree/main/packages/mom
- `pi-mom` README: https://github.com/badlogic/pi-mono/blob/main/packages/mom/README.md

- `@mariozechner/pi-tui`: https://github.com/badlogic/pi-mono/tree/main/packages/tui
- `pi-tui` README: https://github.com/badlogic/pi-mono/blob/main/packages/tui/README.md

- `@mariozechner/pi-web-ui`: https://github.com/badlogic/pi-mono/tree/main/packages/web-ui
- `pi-web-ui` README: https://github.com/badlogic/pi-mono/blob/main/packages/web-ui/README.md

- `@mariozechner/pi-pods` / `@mariozechner/pi`: https://github.com/badlogic/pi-mono/tree/main/packages/pods
- `pi-pods` README: https://github.com/badlogic/pi-mono/blob/main/packages/pods/README.md

### EvoPaw 本地参考

- `CLAUDE.md`
- `docs/hermes-vs-nanobot-multi-provider-analysis-2026-04-22.md`
- `docs/improved_agent/evopaw-multi-model-design-2026-04-23.md`
- `docs/improved_agent/hermes-agent-improvement-plan.md`
- `evopaw/agents/main_agent.py`
- `evopaw/tools/skill_loader.py`
- `evopaw/llm/claude_client.py`
- `evopaw/memory/context_mgmt.py`
- `evopaw/memory/indexer.py`
