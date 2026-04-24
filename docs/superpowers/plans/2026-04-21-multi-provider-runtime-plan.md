# EvoPaw Multi-Provider Runtime Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏 EvoPaw 现有 Skills、Feishu 接入和三层记忆的前提下，把运行时从 Claude Agent SDK 单一路径演进为多 provider 架构，让系统既能走 Claude 官方 API，也能走 Kimi API，并支持按角色、环境或会话切换。

**Architecture:** 采用“过渡期三运行时，目标双一等公民 provider”方案。过渡期保留 `claude_sdk_compat`，先通过 Moonshot 的 Anthropic 兼容入口完成 Kimi 验证；目标架构则引入独立的 provider/runtime 层，并将 `anthropic_messages`（Claude 官方 API）与 `openai_chat`（Kimi API）作为两条正式 transport。task 型 Skill 执行先保留 Claude SDK 兼容执行器，后续再决定是否替换为通用本地工具运行时。

**Tech Stack:** `claude-agent-sdk`, `openai>=1.0`, Moonshot/Kimi API, Pydantic, aiohttp, pytest

---

## 1. 结论先行

这次改造不是“把模型名从 `claude-sonnet-4-6` 改成 `kimi-k2.5`”，也不是“把 Claude 全量替换成 Kimi”。

EvoPaw 当前深度依赖了 Claude Agent SDK 的四类能力：

1. `query()` / `ClaudeAgentOptions` 这套对话运行时。
2. `create_sdk_mcp_server()` + `@tool()` 这套 MCP 工具暴露方式。
3. Claude CLI / SDK 的异常、权限模式、模型选择与环境变量行为。
4. task 型 Skill 子代理依赖 SDK 自带的 `Bash/Read/Write/Edit/Grep/Glob` 工具集合。

因此，推荐路线不是一次性硬切，而是把“当前 Claude SDK 运行时”和“未来 Claude API / Kimi API 双 provider 运行时”区分开来：

1. **Phase A：Kimi 快速验证**
   - 继续使用 Claude Agent SDK。
   - 按 Kimi 官方文档，把 Claude Code / Anthropic 路径改指向 `https://api.moonshot.ai/anthropic`。
   - 先验证主 Agent、reference Skill、task Skill、Feishu 场景、图片场景、长上下文场景是否可用。
2. **Phase B：Provider Runtime 抽象**
   - 吸收 `nanobot` 与 `hermes-agent` 的设计，把 provider 解析、模型元数据、辅助模型路由、transport 选择独立出来。
3. **Phase C：实现双正式 transport**
   - Claude 走 Anthropic 官方 `/v1/messages`。
   - Kimi 走 Moonshot `/v1/chat/completions` + `tools`。
   - `skill_loader` 从 Claude MCP 适配器拆成“核心逻辑 + SDK/MCP 适配器 + OpenAI tool 适配器”。
4. **Phase D：决定 task Skill 子执行器的长期归宿**
   - 短期保留 Claude SDK 子执行器作为兼容桥。
   - 中长期若要去掉对 Claude SDK 的依赖，再实现本地 `bash/read/write/edit/grep/glob` 通用工具运行时。

如果目标只是“尽快试 Kimi 在 EvoPaw 上的效果”，做到 Phase A 即可。

如果目标是“Claude API 和 Kimi API 长期并存”，至少要做到 Phase C。

---

## 2. 外部参考提炼

## 2.1 来自 nanobot 的可直接借鉴点

`nanobot` 的核心经验不是“支持很多 provider”，而是把 provider 元数据集中管理。

- `nanobot/providers/registry.py` 把 provider 定义为单一注册表，统一承载：
  - provider 名称
  - 模型关键字匹配
  - API key 环境变量
  - 默认 base URL
  - backend 类型（如 `openai_compat`、`anthropic`）
  - 特殊参数覆盖（如 `model_overrides`）
- `docs/configuration.md` 明确区分：
  - “具名 provider”
  - “任意 OpenAI-compatible endpoint 的 custom provider”
  - “Responses-compatible endpoint”
- 对 Moonshot/Kimi，`nanobot` 走的是 **OpenAI-compatible provider**，而不是单独写一套非标准协议。

对 EvoPaw 的启发：

1. Kimi 不应在代码里散落成一堆 `if "kimi" in model`。
2. EvoPaw 需要一个统一的 provider 配置层，至少描述：
   - `provider_id`
   - `family`（`claude_sdk_compat`, `anthropic_messages`, `openai_chat`）
   - `api_base`
   - `api_key_env`
   - `default_model`
   - `context_window`
   - `supports_tools`
   - `supports_vision`
   - `param_policy`
3. Moonshot 的“OpenAI-compatible”应作为**标准路径**，不是临时 hack。

## 2.2 来自 hermes-agent 的可直接借鉴点

`hermes-agent` 的关键点是把“provider 选择”与“具体请求构造”分开。

- 文档强调共享的 `runtime provider resolver`，统一服务于：
  - CLI
  - gateway
  - cron
  - auxiliary model calls
- `Adding Providers` 文档把 provider 接入拆成两层：
  - **runtime resolution**
  - **api_mode / adapter**
- Hermes 的判断标准很清晰：
  - 如果只是 OpenAI-compatible endpoint，用 `chat_completions` 路径即可。
  - 只有非 OpenAI 协议，才需要新 adapter / 新 `api_mode`。

对 EvoPaw 的启发：

1. 必须把“配置解析”与“发送请求”拆开。
2. 必须有明确的运行模式，例如：
   - `claude_sdk_compat`
   - `anthropic_messages`
   - `openai_chat`
3. provider 解析要统一服务于：
   - 主 Agent
   - task 型 Skill 子执行器
   - 记忆压缩模型
   - embedding / summarizer
4. API key 不能靠随手读环境变量，必须做作用域控制，避免错把某个 key 发到错误 endpoint。

## 2.3 来自 Kimi 官方文档的关键约束

Kimi 官方文档同时给了两条路：

1. **OpenAI-compatible 路**
   - `base_url = https://api.moonshot.ai/v1`
   - 兼容 `/v1/chat/completions`
   - tool use 使用 `tools` / `tool_calls`
   - 官方明确建议不要再用废弃的 `functions`
2. **Claude Code / Anthropic-compatible 路**
   - 官方直接给出了 Claude Code 的环境变量方案：
     - `ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic`
     - `ANTHROPIC_AUTH_TOKEN=<Moonshot API Key>`
     - `ANTHROPIC_MODEL=kimi-k2.5`
     - `CLAUDE_CODE_SUBAGENT_MODEL=kimi-k2.5`
     - `ENABLE_TOOL_SEARCH=false`

此外，Kimi 官方文档还说明：

- K2.5 / K2.6 提供 256K 上下文。
- 支持多步 tool invocation。
- OpenAI-compatible 多模态格式使用 `image_url` / `video_url`，不是 Claude 的 base64 image block。
- `temperature=0` 且 `n>1` 是非法组合。
- 流式响应中的 `usage` 行为与 OpenAI 有细微差异。

对 EvoPaw 的结论：

1. **快速验证**应优先走 Anthropic-compatible 路，因为这能复用现有 Claude SDK 工具链。
2. **正式架构**不应只保留 Kimi 一条路，而应同时提供：
   - `anthropic_messages` 作为 Claude 官方 API 路径
   - `openai_chat` 作为 Kimi API 路径

## 2.4 来自 Anthropic 官方文档的关键约束

Anthropic 官方 API 当前以 Messages API 为核心：

- 端点是 `/v1/messages`
- 认证通过 `x-api-key`
- 需要 `anthropic-version` 请求头
- 会话是无状态的，需要每轮发送完整历史

对 EvoPaw 的启发：

1. Claude 官方 API 不应继续等同于 Claude SDK。
2. Claude 直连路径需要成为 provider runtime 的正式 transport，而不是只靠 CLI 或 SDK 间接调用。
3. 主 Agent 若要真正支持“Claude API / Kimi API 二选一”，运行时必须同时支持：
   - `anthropic_messages`
   - `openai_chat`

---

## 3. EvoPaw 当前耦合面清单

## 3.1 与 Claude Agent SDK 的直接耦合

- `evopaw/llm/claude_client.py`
  - 构造 `ClaudeAgentOptions`
  - 检测 Claude CLI
- `evopaw/agents/main_agent.py`
  - `query()`
  - `ResultMessage`
  - `CLINotFoundError` / `CLIConnectionError`
  - Claude 图片 block
  - Claude MCP server 注入
- `evopaw/agents/skill_agent.py`
  - task 型 Skill 的 Claude 子代理执行
- `evopaw/tools/skill_loader.py`
  - `create_sdk_mcp_server`
  - `@tool` 装饰器

## 3.2 与 Claude 协议的间接耦合

- `evopaw/tools/add_image_tool_local.py`
  - 当前只会生成 Claude 原生 image content block
- `evopaw/main.py`
  - 启动时强依赖 Claude CLI
- `tests/integration/conftest.py`
  - LLM 测试只认 `ANTHROPIC_API_KEY`
- `README.md` / `CLAUDE.md`
  - 文档默认假设整个系统只能跑在 Claude Agent SDK 上

## 3.3 当前设计里的一个现成问题

`evopaw/main.py` 虽然读了：

- `agent.sub_agent_model`
- `agent.sub_agent_max_turns`

但当前并没有把它们真正传到 `run_skill_agent()` 路径里。

这个问题在 Kimi 改造前就该修掉，否则：

- 你以为自己在切换 Sub-Agent 模型
- 实际系统仍然在使用硬编码默认值

---

## 4. 推荐目标架构

## 4.1 总体原则

不要把“是否使用 Kimi”写成“是否使用某个 SDK”。

EvoPaw 后续应拆成四层：

1. **Provider Config Layer**
   - 配置、env、默认值、base URL、模型元数据
2. **Runtime Resolution Layer**
   - 根据请求场景解析出本次调用实际要用的 provider/model/endpoint
3. **Transport Layer**
   - `claude_sdk_compat`
   - `anthropic_messages`
   - `openai_chat`
4. **Execution Layer**
   - 主 Agent loop
   - task Skill executor
   - auxiliary model calls

## 4.2 推荐的过渡架构

短期不要追求“一次性把所有执行面都迁到 OpenAI SDK”，也不要把 Claude 官方 API 和 Claude SDK 混为一谈。

推荐的过渡结构是：

- **Main Agent**
  - Phase A: 仍用 `ClaudeSDKCompatBackend`
  - Phase C: 可选 `AnthropicMessagesBackend(Claude API)` 或 `OpenAIChatBackend(Kimi API)`
- **SkillLoader**
  - 立即拆成“核心逻辑 + 适配器”
- **task 型 Skill executor**
  - 先保留 `ClaudeSDKSkillExecutor`
  - 后续再决定是否实现 `GenericLocalToolExecutor`
- **Compression / embedding**
  - 纳入统一 provider runtime，但暂时可继续保留当前 Qwen / DashScope 路径

这样做的原因：

1. Main Agent 当前只有一个外部工具：`skill_loader`。
2. 这意味着主 Agent 迁到 `anthropic_messages` 或 `openai_chat` 的难度，比“全面替换全部工具系统”低得多。
3. 难点真正集中在 task 型 Skill 的本地工具运行时，而不是主 Agent 的对话 loop。

---

## 5. 目标文件结构

建议新增以下结构：

```text
evopaw/
├── provider_runtime/
│   ├── __init__.py
│   ├── models.py          # ProviderConfig / ResolvedRuntime / ModelProfile
│   ├── resolve.py         # 配置 + env + 默认值解析
│   ├── metadata.py        # context window / vision / tools / param policy
│   └── policy.py          # main/subagent/compression/embedding 路由策略
├── llm/
│   ├── __init__.py
│   ├── backends/
│   │   ├── base.py        # AgentBackend 抽象
│   │   ├── claude_sdk.py  # 现有 Claude SDK 兼容层
│   │   ├── anthropic_messages.py
│   │   └── openai_chat.py # Kimi/OpenAI-compatible tool loop
│   ├── multimodal.py      # Claude/OpenAI 两套 content builder
│   └── usage.py           # usage / finish_reason 归一化
└── tools/
    ├── skill_loader.py    # 只保留核心 registry / dispatch
    ├── skill_loader_mcp.py
    └── skill_loader_openai.py
```

---

## 6. 分阶段实施计划

### Task 1: 先做 Kimi 兼容性 POC，不动主架构

**Files:**
- Modify: `evopaw/main.py`
- Modify: `evopaw/llm/claude_client.py`
- Modify: `evopaw/agents/skill_agent.py`
- Modify: `README.md`
- Test: `tests/unit/test_skill_agent.py`
- Test: `tests/integration/conftest.py`

- [ ] 增加一个显式运行模式配置，例如 `agent.runtime_mode: claude_sdk_compat`
- [ ] 把 `sub_agent_model`、`sub_agent_max_turns` 真正传进 task Skill 执行路径
- [ ] 在启动阶段新增 provider preflight：
  - `runtime_mode=claude_sdk_compat` 时仍检查 `claude` CLI
  - 若目标 provider 为 `moonshot_claude_compat`，则检查：
    - `ANTHROPIC_BASE_URL`
    - `ANTHROPIC_AUTH_TOKEN`
    - 主/子模型名
- [ ] 新增一份明确的 POC 环境变量说明：

```bash
export ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic
export ANTHROPIC_AUTH_TOKEN=$MOONSHOT_API_KEY
export ANTHROPIC_MODEL=kimi-k2.5
export ANTHROPIC_DEFAULT_OPUS_MODEL=kimi-k2.5
export ANTHROPIC_DEFAULT_SONNET_MODEL=kimi-k2.5
export ANTHROPIC_DEFAULT_HAIKU_MODEL=kimi-k2.5
export CLAUDE_CODE_SUBAGENT_MODEL=kimi-k2.5
export ENABLE_TOOL_SEARCH=false
```

- [ ] 先跑文本与工具链 smoke test：

```bash
python3 -m pytest tests/unit/test_main_agent.py tests/unit/test_skill_agent.py -v
```

- [ ] 再做 TestAPI 实测：
  - 普通问答
  - `history_reader`
  - 一个 reference Skill
  - 一个 task Skill
  - 一个图片输入场景

**验收标准：**
- 主 Agent 能在 Kimi 上返回稳定文本回复
- `skill_loader` 仍可调用
- task 型 Skill 仍可执行 Bash/Read/Edit 路径
- 不需要改业务 Skills 本体

### Task 2: 引入 Provider Runtime 抽象，停止把模型配置散落在各处

**Files:**
- Create: `evopaw/provider_runtime/__init__.py`
- Create: `evopaw/provider_runtime/models.py`
- Create: `evopaw/provider_runtime/resolve.py`
- Create: `evopaw/provider_runtime/metadata.py`
- Create: `evopaw/provider_runtime/policy.py`
- Modify: `evopaw/main.py`
- Modify: `evopaw/llm/__init__.py`
- Test: `tests/unit/test_provider_runtime.py`

- [ ] 定义 `ProviderConfig`、`ResolvedRuntime`、`ModelProfile`
- [ ] 统一解析以下信息：
  - `provider_id`
  - `family`
  - `api_base`
  - `api_key`
  - `model`
  - `context_window`
  - `supports_tools`
  - `supports_vision`
- [ ] 明确解析优先级，借鉴 Hermes：
  1. 显式调用参数
  2. `config.yaml`
  3. 环境变量
  4. 默认值
- [ ] 第一批只支持四类 runtime family：
  - `claude_sdk_compat`
  - `anthropic_messages`
  - `openai_chat`
  - `fallback`
- [ ] 将以下调用面都改为读取 runtime resolver，而不是各自读环境变量：
  - 主 Agent
  - task Skill executor
  - compression / summarizer
  - embedding

**验收标准：**
- Kimi / Claude / Qwen 的模型路由不再散落在 `main.py`、`claude_client.py`、各类 helper 中
- 任意执行面都能拿到统一的 resolved runtime 结构

### Task 3: 把 Claude 专属多模态格式抽成双协议 builder

**Files:**
- Create: `evopaw/llm/multimodal.py`
- Modify: `evopaw/tools/add_image_tool_local.py`
- Modify: `evopaw/agents/main_agent.py`
- Test: `tests/unit/test_add_image_tool.py`
- Test: `tests/unit/test_main_agent.py`

- [ ] 将 `load_image_for_claude()` 演进为通用图片内容构建器
- [ ] 至少支持两种输出：
  - Claude SDK / Anthropic 形状
  - OpenAI-compatible `image_url` 形状
- [ ] Kimi OpenAI-compatible 路径中，图片应转成 data URL：

```python
{
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64,AAA="}
}
```

- [ ] 主 Agent 根据 runtime family 选择 content builder

**验收标准：**
- Anthropic-compatible 路径仍不回归
- Kimi OpenAI-compatible 路径可正确发送图片

### Task 4: 拆分 SkillLoader 的“核心逻辑”和“Claude MCP 适配器”

**Files:**
- Modify: `evopaw/tools/skill_loader.py`
- Create: `evopaw/tools/skill_loader_mcp.py`
- Create: `evopaw/tools/skill_loader_openai.py`
- Test: `tests/unit/test_skill_loader.py`
- Test: `tests/unit/test_skill_loader_openai.py`

- [ ] `skill_loader.py` 只保留：
  - skill registry 解析
  - frontmatter description 提取
  - reference/task/history_reader 分发逻辑
- [ ] `skill_loader_mcp.py` 负责：
  - `@tool`
  - `create_sdk_mcp_server`
- [ ] `skill_loader_openai.py` 负责：
  - 生成 OpenAI-compatible tool schema
  - 将 tool call args 转发给核心 dispatcher
- [ ] 保持 `history_reader` 的内联逻辑不依赖任何 LLM runtime

**验收标准：**
- 同一套 `skill_loader` 核心逻辑，可同时被 Claude MCP 路径和 OpenAI `tools` 路径复用

### Task 5: 为主 Agent 增加 Anthropic Messages 与 OpenAI-compatible 两条正式 backend

**Files:**
- Create: `evopaw/llm/backends/base.py`
- Create: `evopaw/llm/backends/claude_sdk.py`
- Create: `evopaw/llm/backends/anthropic_messages.py`
- Create: `evopaw/llm/backends/openai_chat.py`
- Modify: `evopaw/agents/main_agent.py`
- Modify: `evopaw/llm/__init__.py`
- Test: `tests/unit/test_openai_backend.py`
- Test: `tests/unit/test_anthropic_backend.py`
- Test: `tests/unit/test_main_agent.py`

- [ ] 定义统一接口，例如：

```python
@dataclass
class AgentTurnResult:
    text: str
    usage: dict[str, int] | None
    raw_messages: list[dict]

class AgentBackend(Protocol):
    async def run_turn(
        self,
        prompt: str,
        system_prompt: str,
        tools: list[dict],
        session_id: str,
    ) -> AgentTurnResult:
        raise NotImplementedError
```

- [ ] `ClaudeSDKCompatBackend` 包装现有 `query()` 路径
- [ ] `AnthropicMessagesBackend` 实现：
  - 构造 Messages API 请求
  - 归一化多轮历史
  - 适配 Claude 的 content blocks
  - 统一 usage / stop_reason 抽取
- [ ] `OpenAIChatBackend` 实现：
  - 构造 `messages`
  - 注入 `tools`
  - 识别 `tool_calls`
  - 执行 `skill_loader`
  - 回填 `tool` 消息
  - 继续循环直到拿到最终文本
- [ ] 这一步只迁主 Agent，不迁 task Skill executor

**关键决策：**

主 Agent 当前只有一个工具：`skill_loader`。  
这使它适合作为第一批正式 backend 迁移对象，无论目标是 Claude API 还是 Kimi API。

**验收标准：**
- `runtime_mode=anthropic_messages`
- `provider=anthropic`
- 主 Agent 可在 Claude `/v1/messages` 上完成问答
- `runtime_mode=openai_chat`
- `provider=moonshot`
- 主 Agent 可在 Kimi `/v1/chat/completions` 上完成问答 + 调用 `skill_loader`

### Task 6: 为 task 型 Skill 保留兼容桥，不要在第一轮就强行重写本地工具运行时

**Files:**
- Modify: `evopaw/agents/skill_agent.py`
- Create: `evopaw/agents/skill_executors/base.py`
- Create: `evopaw/agents/skill_executors/claude_sdk_executor.py`
- Test: `tests/unit/test_skill_agent.py`

- [ ] 把 `run_skill_agent()` 改造成“执行器选择器”
- [ ] 第一版只实现：
  - `ClaudeSDKSkillExecutor`
- [ ] 当主 Agent 已迁到 `anthropic_messages` 或 `openai_chat` 路径时，task Skill 仍然可以通过兼容桥执行
- [ ] 只有在确认 Kimi 主 Agent 稳定后，再决定是否实现：
  - `bash`
  - `read_file`
  - `write_file`
  - `edit_file`
  - `grep`
  - `glob`
  这套通用本地工具运行时；如果决定继续推进，则新增文件：
  - `evopaw/agents/skill_executors/generic_local_executor.py`

**不建议第一轮做的事：**
- 直接重写 18 个 Skills
- 直接把 Claude SDK 子执行器完全删掉
- 一上来就把本地工具运行时与安全审批一起重做

**验收标准：**
- 主 Agent backend 与 task Skill executor backend 可以解耦独立选择

### Task 7: 把模型配置从“主模型 / 子模型”升级为“角色路由”

**Files:**
- Modify: `evopaw/main.py`
- Modify: `README.md`
- Create: `config.yaml.template`
- Test: `tests/unit/test_provider_runtime.py`

- [ ] 将当前配置：

```yaml
agent:
  planner_model: "claude-sonnet-4-6"
  sub_agent_model: "claude-haiku-4-5"
```

升级为：

```yaml
llm:
  providers:
    moonshot:
      family: openai_chat
      api_base: https://api.moonshot.ai/v1
      api_key_env: MOONSHOT_API_KEY
    anthropic:
      family: anthropic_messages
      api_base: https://api.anthropic.com
      api_key_env: ANTHROPIC_API_KEY
    moonshot_claude_compat:
      family: claude_sdk_compat
      api_base: https://api.moonshot.ai/anthropic
      api_key_env: MOONSHOT_API_KEY
  roles:
    main:
      provider: anthropic
      model: claude-sonnet-4-20250514
    task_skill:
      provider: moonshot_claude_compat
      model: kimi-k2.5
    main_alt:
      provider: moonshot
      model: kimi-k2.5
    compression:
      provider: dashscope
      model: qwen3-turbo
    embedding:
      provider: dashscope
      model: text-embedding-v3
```

- [ ] 这样后续才能支持：
  - 主 Agent = Claude API
  - 主 Agent = Kimi API
  - task Skill = 兼容桥
  - 压缩/embedding = 其他 provider

**验收标准：**
- 配置语义与运行时语义一致
- 不再把“provider 选择”塞进若干零散环境变量里

### Task 8: 测试矩阵与灰度上线

**Files:**
- Modify: `tests/integration/conftest.py`
- Create: `tests/unit/test_provider_runtime.py`
- Create: `tests/unit/test_skill_loader_openai.py`
- Create: `tests/integration/test_kimi_poc.py`
- Create: `tests/integration/test_anthropic_main_agent.py`
- Create: `tests/integration/test_openai_main_agent.py`

- [ ] 单元测试覆盖：
  - runtime resolution
  - tool schema generation
  - Claude/OpenAI 双协议 multimodal builder
  - 主 Agent backend 选择
  - task Skill executor 选择
- [ ] 集成测试拆成三层：
  - `llm_anthropic_direct`
  - `llm_claude_sdk_compat`
  - `llm_kimi_claude_compat`
  - `llm_kimi_openai`
- [ ] 先灰度到 TestAPI
- [ ] 再灰度到飞书单聊
- [ ] 最后放开群聊 / thread / cron

建议命令：

```bash
python3 -m pytest tests/unit/test_provider_runtime.py tests/unit/test_main_agent.py tests/unit/test_skill_agent.py -v
python3 -m pytest tests/integration/test_api.py -v
python3 -m pytest tests/integration/test_anthropic_main_agent.py -m "llm" -v -s
python3 -m pytest tests/integration/test_kimi_poc.py -m "llm" -v -s
python3 -m pytest tests/integration/test_openai_main_agent.py -m "llm" -v -s
```

**验收标准：**
- 三种模式可被明确区分和回归测试
- 切换 provider 不再靠手工改代码

---

## 7. 风险清单

## 7.1 最大技术风险

不是 Claude API 或 Kimi API 本身，而是 **task 型 Skill 的本地工具运行时**。

当前这些能力都来自 Claude Agent SDK：

- Bash
- Read
- Write
- Edit
- Grep
- Glob

如果你想去掉对 Claude SDK 的依赖，就必须自己实现这套工具与安全边界。

## 7.2 POC 风险

使用 Moonshot 的 Anthropic-compatible 入口做 POC 时，需要重点验证：

1. Claude SDK 的 tool / MCP 行为是否被 Moonshot 兼容层完整支持。
2. 图片输入在 Anthropic-compatible 路径下是否稳定。
3. `max_turns`、长上下文、verbose hooks 是否有边角差异。
4. 成本与延迟是否满足飞书交互体验。

## 7.3 OpenAI-compatible 风险

当主 Agent 切到 Kimi 原生 OpenAI-compatible 路径后，需要重点验证：

1. `tool_calls` 参数严格性。
2. tool call arguments 的 JSON 解析健壮性。
3. 图片 content part 格式。
4. usage 统计与 token budget。
5. 当 `skill_loader` 返回超长文本时，Kimi 对多轮 tool loop 的稳定性。

---

## 8. 推荐排期

### Week 1

- 完成 Phase A
- 目标：确认 Kimi 在 EvoPaw 上可通过兼容入口跑通，同时不破坏现有 Claude 路径

### Week 2

- 完成 provider runtime 抽象
- 不迁主 Agent，只先消除配置耦合

### Week 3

- 主 Agent 新增 Claude 官方 API backend
- 主 Agent 新增 Kimi API backend
- task Skill 保留 Claude SDK 兼容桥

### Week 4+

- 决定是否继续去掉 Claude SDK 兼容执行器
- 如果做，再单开一期实现通用本地工具运行时

---

## 9. 推荐决策

**建议直接采纳的路线：**

1. 先做 **Kimi + Claude SDK compatibility POC**
2. POC 通过后，马上做 **provider runtime 抽象**
3. 然后为 **主 Agent** 同时补齐：
   - Claude 官方 API path
   - Kimi 官方 API path
4. **task 型 Skill 子执行器先保留兼容桥**

这是当前性价比最高、风险最可控的路线。

不建议的路线是：

1. 直接全面删除 Claude Agent SDK
2. 同时重写 Claude API path、Kimi API path、主 Agent loop、MCP 适配、task Skill 本地工具、安全审批
3. 还想在同一期里上线飞书生产流量

那样改造面会过大，回滚也困难。

---

## 10. 参考资料

- Kimi OpenAI 兼容迁移文档: https://platform.kimi.ai/docs/guide/migrating-from-openai-to-kimi
- Kimi Tool Use 文档: https://platform.kimi.ai/docs/api/tool-use
- Kimi K2.6 Quickstart: https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart
- Kimi 在 Claude Code / Cline / RooCode 中的使用: https://platform.kimi.ai/docs/guide/agent-support
- nanobot 配置文档: https://github.com/HKUDS/nanobot/blob/main/docs/configuration.md
- nanobot provider registry: https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/providers/registry.py
- nanobot 仓库 README / 发布节奏: https://github.com/HKUDS/nanobot
- Hermes Adding Providers: https://hermes-agent.nousresearch.com/docs/developer-guide/adding-providers/
- Hermes Provider Runtime Resolution: https://hermes-agent.nousresearch.com/docs/developer-guide/provider-runtime/
- Hermes AI Providers: https://hermes-agent.nousresearch.com/docs/integrations/providers/
- Anthropic Messages API: https://docs.anthropic.com/en/api/messages-examples
- Anthropic API Overview: https://docs.anthropic.com/en/api/getting-started
