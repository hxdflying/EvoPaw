# `yoyo-evolve` 项目分析，以及对 `evopaw` 的改进启发

分析日期：2026-04-23  
结论先行：`yoyo-evolve` 最值得 `evopaw` 借鉴的，不是“自我进化”这个表层叙事，而是它背后的三层拆分：

1. 产品层：CLI、GitHub 交互、journal、community loop。
2. agent/runtime 层：`yoagent::Agent`、工具、skills、上下文压缩、sub-agent。
3. provider 层：Anthropic / OpenAI / Google / Ollama / OpenRouter / Bedrock / custom endpoint 等统一接入。

`evopaw` 当前最大的问题不是“只支持 Claude 模型”，而是“主运行时直接绑在 Claude Agent SDK / Claude Code CLI 上”。这意味着你绑定的是一整套 Claude Code runtime 语义，而不仅是某个模型 API。  
`yoyo-evolve` 给 `evopaw` 的最大启发，是先把“飞书产品壳”和“模型/agent runtime”解耦，再在 runtime 下面做 provider registry 与 fallback，而不是在现有代码里到处插 `if provider == ...`。

## 1. `yoyo-evolve` 到底是什么

`yoyo-evolve` 不是一个普通的聊天机器人项目，而是一个“公开演化中的 coding agent”。它的 README 对自己的定义非常明确：

- 它是一个运行在终端里的 coding agent。
- 它会定期读取自己的代码、查看 GitHub issues、计划改进、实现改动、跑测试、提交代码。
- 它还有一个独立的 social loop，会周期性读取 GitHub Discussions、回复讨论、并把“对人的理解”写入另一套 memory。

仓库首页和 README 的定位非常清楚：

- “A Coding Agent That Evolves Itself”  
  来源：<https://github.com/yologdev/yoyo-evolve>
- 仓库结构里同时存在 `src/`、`scripts/`、`skills/`、`memory/`、`journals/`、`.github/workflows/`，说明它不是单个 CLI，而是“可运行 agent + 自动化流水线 + 记忆层 + 社区互动层”的组合体。  
  来源：<https://github.com/yologdev/yoyo-evolve>

从 `Cargo.toml` 可以看到它的核心依赖非常少，最关键的是：

- 它底层依赖的是 `yoagent = { version = "0.7", features = ["openapi"] }`
- 也就是说，`yoyo-evolve` 的 agent 能力不是自己从零写的，而是建立在 `yoagent` 这个更底层的 agent runtime 上。  
  来源：<https://raw.githubusercontent.com/yologdev/yoyo-evolve/main/Cargo.toml>

这一点对你最重要：  
`yoyo-evolve` 之所以能支持很多模型，不是因为它在产品层写得很花，而是因为它从 Day 0 起就没有把产品层直接绑死到某一家厂商的 agent SDK。

## 2. `yoyo-evolve` 的核心架构

### 2.1 底层不是厂商 SDK，而是通用 agent runtime

`yoyo-evolve` 的 `src/main.rs` 直接使用的是：

- `yoagent::agent::Agent`
- `yoagent::context::{ContextConfig, ExecutionLimits}`
- `yoagent::provider::{AnthropicProvider, GoogleProvider, BedrockProvider, OpenAiCompatProvider, ModelConfig}`

这说明它的核心抽象是：

- 一个统一的 `Agent`
- 一个统一的上下文/预算配置
- 多个 provider adapter
- 不同 provider 之间只在 `ModelConfig` 和 provider 类型上分叉，其余 agent 配置尽量复用  
  来源：<https://github.com/yologdev/yoyo-evolve/blob/main/src/main.rs>

尤其关键的是 `AgentConfig` 的设计。`main.rs` 里把几乎所有运行参数先收拢到一个 `AgentConfig` 结构里，然后：

- `configure_agent()` 负责把共同配置统一灌进 agent
- `build_agent()` 才只在 provider 选择这一步分叉

这意味着它避免了最常见的坏味道：  
“每加一个 provider，就复制一遍整个 agent 构建流程。”

这也是 `evopaw` 当前最缺的一层。

### 2.2 provider 抽象非常明确

`src/providers.rs` 里直接维护了一套 provider registry 风格的常量和工具函数：

- `KNOWN_PROVIDERS`
- `provider_api_key_env(provider)`
- `known_models_for_provider(provider)`
- `default_model_for_provider(provider)`

支持的 provider 包括：

- `anthropic`
- `openai`
- `google`
- `openrouter`
- `ollama`
- `xai`
- `groq`
- `deepseek`
- `mistral`
- `cerebras`
- `zai`
- `minimax`
- `bedrock`
- `custom`

这套设计的意义不在于“列出很多名字”，而在于它把以下信息统一建模了：

- provider 名字
- API key env 约定
- 默认模型
- 常见模型名
- provider 对应的协议类型

来源：<https://github.com/yologdev/yoyo-evolve/blob/main/src/providers.rs>

这让它可以很自然地支持：

- CLI 启动时选 provider
- `/provider` 中途切换 provider
- fallback provider
- 配置文件持久化 provider/model/base_url
- 自定义 OpenAI-compatible endpoint

这是一种“先抽象 provider，再做产品功能”的路径。

### 2.3 多模型支持是 runtime 级别的，而不是业务层硬编码

`main.rs` 的 `create_model_config()` 和 `build_agent()` 暴露出一个很清楚的策略：

- Anthropic 走原生 `AnthropicProvider`
- Google 走 `GoogleProvider`
- Bedrock 走 `BedrockProvider`
- 其余大多数 provider 统一走 `OpenAiCompatProvider`

也就是说，`yoyo-evolve` 并没有为每家都实现一整套独立 runtime。它用了两类策略：

1. 原生适配：Anthropic / Google / Bedrock
2. OpenAI-compatible 归一化：OpenRouter / xAI / Groq / DeepSeek / Mistral / Cerebras / custom / 一部分本地服务

这点非常实用。因为现实里“多模型兼容”最容易走歪成：

- 为每个厂商写一套完全不同的执行路径
- 结果接口、流式输出、tool call、错误处理全部分裂

`yoyo-evolve` 反而采取了更克制的办法：  
先按“协议族”分组，而不是按“厂商品牌”分组。

## 3. `yoyo-evolve` 为什么能兼容很多模型

可以把它总结成一句话：

> 它兼容多模型，不是因为它“支持很多 API”，而是因为它的主产品逻辑根本不直接依赖某一个厂商的 agent runtime。

具体做法有五个关键点。

### 3.1 统一的 `AgentConfig`

`AgentConfig` 收拢了这类字段：

- `model`
- `api_key`
- `provider`
- `base_url`
- `skills`
- `system_prompt`
- `thinking`
- `max_tokens`
- `temperature`
- `max_turns`
- `context_strategy`
- `fallback_provider`
- `fallback_model`

来源：<https://github.com/yologdev/yoyo-evolve/blob/main/src/main.rs>

这让 runtime 构建不依赖 CLI、本地命令、GitHub Actions 或社交流水线。  
产品外层只要能生成一个 `AgentConfig`，底层 runtime 就能跑。

### 3.2 provider / model 解析逻辑集中管理

`src/cli.rs` 和 `src/providers.rs` 联合做了这几件事：

- CLI 参数解析
- 配置文件加载
- provider 默认模型推导
- fallback provider 推导
- API key 环境变量映射

来源：

- <https://github.com/yologdev/yoyo-evolve/blob/main/src/cli.rs>
- <https://github.com/yologdev/yoyo-evolve/blob/main/src/providers.rs>

这意味着：

- 业务逻辑不关心 API key 叫什么
- 命令解析不关心 provider 的具体 transport
- runtime 不关心参数是来自 CLI、配置文件还是环境变量

### 3.3 fallback 是一等能力

`main.rs` 里 `AgentConfig` 直接带：

- `fallback_provider`
- `fallback_model`

同时提供 `try_switch_to_fallback()`。  
README 也明确把 provider failover 写成产品能力之一。  
来源：

- <https://github.com/yologdev/yoyo-evolve/blob/main/src/main.rs>
- <https://github.com/yologdev/yoyo-evolve/blob/main/README.md>

这点比“支持多 provider”更成熟。  
很多项目只做到“可以手动切 provider”，但没做到：

- 主 provider API error
- 自动切 fallback
- 重建 agent
- 继续 prompt

对于 `evopaw` 这种服务端 bot，这一层价值非常高。

### 3.4 tool / skill / context 不是跟 provider 绑死的

从 `main.rs` 和 `CLAUDE.md` 看，`yoyo` 的 tools、skills、context management 都属于 runtime 上层能力，而不是特定 provider 的附属品：

- tools 由 `build_tools()` 统一构建
- skills 由 `SkillSet` 加载
- sub-agent 由 `with_sub_agent(...)` 接入
- context 由 `ContextConfig` 控制

来源：

- <https://github.com/yologdev/yoyo-evolve/blob/main/src/main.rs>
- <https://github.com/yologdev/yoyo-evolve/blob/main/CLAUDE.md>

这意味着切 provider 时，不需要重写整套 skill 体系。  
这一点对 `evopaw` 非常关键，因为你的业务价值大部分其实在：

- session 管理
- Feishu 路由
- attachment 流程
- `skill_loader` + skills

而不是在 Claude 本身。

### 3.5 把“多模型”设计成配置问题，而不是分支问题

`.yoyo.toml` 非常简单：

```toml
provider = "anthropic"
model = "claude-opus-4-6"
```

来源：<https://github.com/yologdev/yoyo-evolve/blob/main/.yoyo.toml>

这背后是一种重要设计取向：

- 让 provider/model 成为配置层切换
- 不让上层产品代码感知太多 vendor 细节

这也是 `evopaw` 最应该学的一点。

## 4. `yoyo-evolve` 除了多模型之外，还有哪些值得看

### 4.1 它不是“agent 自主写代码”那么简单，而是有完整的自动化闭环

`evolve.yml` 和 `scripts/evolve.sh` 组成了一条非常完整的演化流水线：

1. 检查构建与测试
2. 获取 GitHub issues
3. 规划任务
4. 实现任务
5. 验证
6. 修复失败
7. 回应 issue
8. push / tag / wrap-up

来源：

- <https://github.com/yologdev/yoyo-evolve/blob/main/.github/workflows/evolve.yml>
- <https://github.com/yologdev/yoyo-evolve/blob/main/scripts/evolve.sh>
- <https://github.com/yologdev/yoyo-evolve/blob/main/CLAUDE.md>

这里真正值得借鉴的不是“自动改自己代码”，而是这些工程思想：

- 任务前先做 starting state 验证
- 每个 task 单独验证
- 验证失败先修复，修不动再回滚
- 回复 issue 时只说自己真正完成的事情
- 用 workflow 把长流程拆成确定的阶段

### 4.2 它有双层记忆系统

`yoyo` 的 memory 不是“把所有历史一股脑塞 prompt”。它分成：

- append-only archive：`memory/learnings.jsonl`、`memory/social_learnings.jsonl`
- active memory：`memory/active_learnings.md`、`memory/active_social_learnings.md`

然后每天用 `synthesize.yml` 做 time-weighted compression：

- 最近内容保留细节
- 中期内容压缩
- 远期内容按主题归纳

来源：

- <https://github.com/yologdev/yoyo-evolve/blob/main/.github/workflows/synthesize.yml>
- <https://github.com/yologdev/yoyo-evolve/blob/main/memory/active_learnings.md>
- <https://github.com/yologdev/yoyo-evolve/blob/main/scripts/yoyo_context.sh>
- <https://github.com/yologdev/yoyo-evolve/blob/main/CLAUDE.md>

这个模式对 `evopaw` 非常有参考价值，因为你现在也已经有：

- session history
- `ctx.json`
- pgvector / 记忆相关组件

你完全可以借鉴它的“archive -> synthesized active context”思想，把当前的历史、长上下文、故障经验、用户偏好做得更稳定。

### 4.3 它把“身份、人格、记忆、经济约束”都做成显式 prompt 资产

`scripts/yoyo_context.sh` 会统一拼接：

- `IDENTITY.md`
- `PERSONALITY.md`
- `ECONOMICS.md`
- `memory/active_learnings.md`
- `memory/active_social_learnings.md`
- sponsor 信息

来源：

- <https://github.com/yologdev/yoyo-evolve/blob/main/scripts/yoyo_context.sh>
- <https://github.com/yologdev/yoyo-evolve/blob/main/IDENTITY.md>

这不是噱头，而是一种 prompt asset 管理方式。  
`evopaw` 也有类似雏形，比如 bootstrap / workspace-init / memory / skill 指令，但当前更分散、工程化程度不如 `yoyo` 清晰。

### 4.4 它对不可信输入的边界感很强

无论在 `social.sh` 还是 `social` skill 里，都明确把 GitHub Discussions 内容标为：

- untrusted user input
- 不可当成指令执行
- 不可照着文本里的命令跑
- 只允许修改白名单文件

来源：

- <https://github.com/yologdev/yoyo-evolve/blob/main/scripts/social.sh>
- <https://github.com/yologdev/yoyo-evolve/blob/main/skills/social/SKILL.md>

这对 `evopaw` 也有直接价值。  
你现在处理的是飞书消息、附件、富文本、外部文件路径，本质上同样是 untrusted input，只是渠道不同。

## 5. `yoyo-evolve` 和 `evopaw` 的本质区别

这部分非常重要。  
我不建议把 `yoyo` 的方案原样照搬到 `evopaw`，因为两者不是同一种产品。

### 5.1 `yoyo-evolve` 是本地 CLI 产品

它的核心交互是：

- 用户在终端里直接使用 agent
- agent 本地访问文件系统、git、shell、MCP
- 自动化演化通过 GitHub Actions 完成

### 5.2 `evopaw` 是飞书驱动的服务端 agent

`evopaw` 的核心交互是：

- 飞书消息进入 `FeishuListener`
- `Runner` 负责 session 路由
- main agent / skill agent 负责处理文本与附件
- 最终通过飞书发回消息

而且当前 `evopaw` 的主链路明确写死在 Claude Agent SDK 上：

- 启动时直接检查 `claude` CLI  
  [evopaw/main.py](/home/hxd/agent_project/evopaw/evopaw/main.py:79)
- `check_claude_cli()` 只是 `shutil.which("claude")`  
  [evopaw/llm/claude_client.py](/home/hxd/agent_project/evopaw/evopaw/llm/claude_client.py:24)
- 主 Agent 用 `claude_agent_sdk.query()`  
  [evopaw/agents/main_agent.py](/home/hxd/agent_project/evopaw/evopaw/agents/main_agent.py:182)
- Skill 子 Agent 也用 `claude_agent_sdk.query()`  
  [evopaw/agents/skill_agent.py](/home/hxd/agent_project/evopaw/evopaw/agents/skill_agent.py:51)
- SDK 内部默认走 `SubprocessCLITransport`，最终拉起 `claude` 进程  
  [/home/hxd/anaconda3/lib/python3.11/site-packages/claude_agent_sdk/client.py](/home/hxd/anaconda3/lib/python3.11/site-packages/claude_agent_sdk/client.py:136)  
  [/home/hxd/anaconda3/lib/python3.11/site-packages/claude_agent_sdk/_internal/transport/subprocess_cli.py](/home/hxd/anaconda3/lib/python3.11/site-packages/claude_agent_sdk/_internal/transport/subprocess_cli.py:63)

因此：

- `yoyo` 的问题是“如何基于通用 runtime 做产品”
- `evopaw` 当前的问题是“产品已经写出来了，但 runtime 被 Claude Code 运行时反向绑定了”

这两个起点不同。

## 6. `yoyo-evolve` 对 `evopaw` 的真正帮助在哪里

### 6.1 最值得学：把 runtime 从产品壳里拆出来

`evopaw` 的飞书接入、session、attachment、skills，本质上都是产品壳。  
真正应该被替换的，是这层：

- `main_agent.py` 的 `query(...)`
- `skill_agent.py` 的 `query(...)`
- `claude_client.py` 里对 `ClaudeAgentOptions` 的直接构建

`yoyo` 给你的启发不是“换成 Rust”，而是：

- 先定义统一的 agent request / response
- 再定义 provider registry
- 最后在 runtime adapter 里对接具体厂商

### 6.2 最值得学：provider registry，而不是 scattered conditionals

你后面如果要支持：

- Claude
- OpenAI
- Gemini
- Ollama / 本地 Qwen
- OpenRouter / DeepSeek / xAI / Groq

正确顺序应该是：

1. 定义 provider spec
2. 定义默认模型、环境变量、capabilities
3. 定义 runtime adapter
4. 再接入配置与路由

而不是：

- 在 `main_agent.py` 加一个 `if provider == "openai"`
- 在 `skill_agent.py` 再加一个 `if provider == "openai"`
- 在 attachment、verbose、streaming、hooks 里再分别兼容一轮

`yoyo` 的 `providers.rs` 就是这种集中式设计的好例子。

### 6.3 最值得学：fallback 和 context strategy 要做成一等能力

`yoyo` 明确支持：

- fallback provider
- context compaction
- checkpoint / restart strategy

来源：

- <https://github.com/yologdev/yoyo-evolve/blob/main/src/main.rs>
- <https://github.com/yologdev/yoyo-evolve/blob/main/src/cli.rs>

对 `evopaw` 来说，这非常适合演化成：

- 主模型：高质量但贵
- 兜底模型：便宜、快、可本地
- 任务路由：文档摘要 / OCR / 代码问答 / 轻量问答分不同模型

### 6.4 最值得学：记忆分层，不要只做“历史拼接”

`yoyo` 的 archive + synthesized active memory 模式，很适合你做：

- 用户偏好记忆
- 常见任务模板记忆
- 故障处理经验
- 飞书沟通风格 / 常见组织语境
- 附件处理失败案例

尤其适合 `evopaw` 这种长期服务型 bot。

### 6.5 最值得学：安全边界和输入分类

`yoyo` 对 GitHub discussions 的 untrusted input 分类，完全可以映射到 `evopaw`：

- 飞书文本
- 富文本卡片
- 附件文件名
- 附件内容摘要
- 外部网页内容
- 历史消息回放

对 `evopaw` 最直接的落地建议是：

- 明确哪些内容只能“理解”，不能“执行”
- skill 的可写目录做白名单
- prompt 中用稳定边界标记包装用户输入和系统拼接内容

## 7. 不建议照搬的部分

### 7.1 不要直接照搬“自我修改代码”

`yoyo-evolve` 的自我演化适合它，因为：

- 它是 open-source CLI
- 代码库就是产品本体
- GitHub Actions 能作为低频自动演化环境

`evopaw` 当前更像一个服务型机器人，首先要解决的是：

- 可靠性
- 多模型兼容
- skill 稳定性
- attachment 处理
- 部署/调试体验

在这些没稳之前，引入“自动改自己代码”的收益远小于风险。

### 7.2 不要照搬 GitHub 社交循环

`yoyo` 的 social session 很有意思，但它服务于“公开成长中的 agent persona”。  
`evopaw` 的主渠道是飞书，不是 GitHub Discussions。  
对你来说，更合理的是：

- 做 issue / 文档 / FAQ / release note 辅助
- 而不是复制一套 `social.sh`

### 7.3 不要照搬 CLI-heavy 的功能设计

`yoyo` 有大量 `/provider`、`/run`、`/watch`、`/todo`、`/checkpoint` 一类命令，是终端产品的自然形态。  
`evopaw` 是消息型产品，应该优先考虑：

- slash 命令在聊天环境里的可用性
- 卡片与文本混合体验
- 文件处理与会话连续性

## 8. 针对 `evopaw` 的详细改进建议

下面这部分是我认为最可执行的路线。

### 第一阶段：先引入 runtime 抽象，不碰飞书壳

目标：不改 `Runner`、`FeishuListener`、`Sender` 主结构，只替换 LLM 接口层。

建议新增一个抽象层，例如：

```python
class AgentRuntime(Protocol):
    async def run_main(self, request: AgentRequest) -> AgentResult: ...
    async def run_skill(self, request: SkillRequest) -> SkillResult: ...
```

```python
@dataclass
class AgentRequest:
    system_prompt: str
    user_input: str | list[dict]
    cwd: str
    tools: list[str]
    mcp_servers: dict
    model: str
    max_turns: int
    metadata: dict[str, Any]
```

```python
@dataclass
class ProviderSpec:
    name: str
    api_style: Literal["claude_code", "openai_responses", "openai_compat", "gemini"]
    api_key_env: str | None
    default_model: str
    supports_tools: bool
    supports_image_input: bool
    supports_mcp_native: bool
    supports_stream_events: bool
```

第一步先只做一个实现：

- `ClaudeCodeRuntime`

它内部继续调用你现有的 `claude_agent_sdk.query()`，这样业务功能不受影响，但架构开始解耦。

### 第二阶段：把 provider registry 做出来

参考 `yoyo` 的 `providers.rs`，在 `evopaw` 里做一个集中模块，例如：

- `evopaw/llm/providers.py`
- `KNOWN_PROVIDERS`
- `provider_api_key_env()`
- `default_model_for_provider()`
- `provider_capabilities()`

第一批建议支持：

- `claude_code`
- `openai`
- `openai_compat`
- `gemini`

其中：

- `openai_compat` 一口气覆盖本地 Ollama、OpenRouter、DeepSeek、xAI 兼容端点
- 这样你不用为每个厂商立即单独做 adapter

### 第三阶段：定义统一的 tool / skill 能力面

这是最难但最关键的一步。

你当前的 skill 体系其实是强资产，但它依赖 Claude runtime 的地方主要有：

- SDK query
- MCP tool 接入
- Claude 风格的权限与 hooks

建议把 skill 能力面重新定义成模型无关的形式：

- 文件读取
- 文件写入
- shell 执行
- 搜索
- 用户追问
- 图像输入
- 外部工具调用

然后：

- `ClaudeCodeRuntime` 把这些映射到 Claude Code / Agent SDK 能力
- 未来的 `OpenAIRuntime` / `GeminiRuntime` 再映射到各自 tool calling 机制

重点不是一开始就“完全统一”，而是先把你自己的内部能力词汇表统一。

### 第四阶段：让主 Agent 和 Skill Agent 共享同一 runtime 抽象

现在 `main_agent.py` 和 `skill_agent.py` 都各自直接依赖 `query()`。  
建议收敛为：

- `runtime.run_main(...)`
- `runtime.run_skill(...)`

这样你换 provider 或做 fallback 时，不需要双份改造。

### 第五阶段：引入 fallback / routing policy

建议做一个显式策略对象：

```python
class RoutingPolicy(Protocol):
    def choose_main_model(self, task: TaskMeta) -> ModelChoice: ...
    def choose_skill_model(self, skill_name: str, task: SkillMeta) -> ModelChoice: ...
    def choose_fallback(self, failure: RuntimeFailure) -> ModelChoice | None: ...
```

适合你的初版规则可以很简单：

- 复杂长任务：Claude / GPT-5 类高质量模型
- 文档摘要：OpenAI-compatible 或 Gemini
- OCR/图片：支持图像的模型
- 失败重试：切 cheap fallback
- 本地模式：优先 `openai_compat` 指向 Ollama / vLLM

### 第六阶段：升级记忆体系

借鉴 `yoyo` 的 archive + synthesis 模式，建议给 `evopaw` 增加三类记忆：

1. `user_memory`
   记录某个飞书用户/会话长期偏好、文风、惯用任务。
2. `ops_memory`
   记录你在部署、附件、skill、调试中遇到的稳定经验。
3. `failure_memory`
   记录 skill 失败模式、依赖缺失、文件类型坑点、上下文溢出模式。

然后周期性合成一份活跃上下文，而不是只靠历史回放。

### 第七阶段：补运营与诊断命令

`yoyo` 很强的一点是能诊断自己。  
`evopaw` 也很需要一套类似能力，但形式应适配服务端：

- `/status`：当前 provider / model / session / memory 状态
- `/doctor`：环境变量、CLI、依赖、skills 目录、数据库、附件处理能力检查
- `/providers`：列出可用 provider 与默认模型
- `/fallback`：查看或测试 fallback 策略

这会大幅降低多模型改造后的维护成本。

## 9. 一条更现实的实施路线

如果你只想先做“支持多模型”，而不是一次性大重构，我建议顺序是：

1. 用 `AgentRuntime` 把 Claude SDK 包起来，先把直接依赖收口。
2. 把 provider registry 做出来，配置层允许 `provider/model/base_url/api_key_env`。
3. 先接入一个 `OpenAI-compatible runtime`，覆盖本地模型和一批兼容厂商。
4. 再决定要不要单独做 `OpenAI` / `Gemini` 的原生 adapter。
5. 最后再考虑 fallback、routing、memory synthesis。

这是最稳的路径。  
它的优点是每一步都能独立上线，不会因为“多模型大改”把飞书主链路一次性炸掉。

## 10. 我对 `evopaw` 的最终建议

### 最应该立刻做的

- 先把 `claude_agent_sdk.query()` 从业务代码里包起来。
- 建一个集中式 provider registry。
- 明确内部统一的 agent request / skill request / result schema。

### 最应该第二批做的

- 做 `openai_compat` adapter。
- 做 fallback provider。
- 做 `/doctor` 和 provider 诊断。

### 最应该第三批做的

- 升级 memory 为 archive + synthesized active context。
- 增强输入边界和 untrusted content 隔离。
- 再考虑更复杂的 routing policy。

### 最不应该现在做的

- 直接复制 `yoyo` 的自我修改代码流水线。
- 先做 GitHub 社交人格化能力。
- 在没有 runtime 抽象前就硬塞第二家厂商。

## 11. 最后的判断

`yoyo-evolve` 对 `evopaw` 的价值，主要不是功能抄作业，而是架构启发。

如果一句话总结：

> `yoyo-evolve` 证明了一个 agent 产品要想长期支持多模型、多能力、多上下文策略，必须先有“独立于厂商 SDK 的 runtime 边界”。  
> `evopaw` 现在最该做的，就是把这条边界补出来。

一旦这层边界建立起来，你后面想做的这些事情才会真的变简单：

- 接本地模型
- 接 OpenAI / Gemini / OpenRouter
- 做 provider fallback
- 做不同任务的模型路由
- 保留飞书和 skill 这一层产品资产

否则，任何“多模型支持”最后都会退化成：  
在一套 Claude Code runtime 语义里，硬塞别家的接口。

这条路后面会越来越难走。

## 参考资料

### `yoyo-evolve`

- 仓库首页：<https://github.com/yologdev/yoyo-evolve>
- README：<https://github.com/yologdev/yoyo-evolve/blob/main/README.md>
- Cargo.toml：<https://raw.githubusercontent.com/yologdev/yoyo-evolve/main/Cargo.toml>
- `src/main.rs`：<https://github.com/yologdev/yoyo-evolve/blob/main/src/main.rs>
- `src/providers.rs`：<https://github.com/yologdev/yoyo-evolve/blob/main/src/providers.rs>
- `src/cli.rs`：<https://github.com/yologdev/yoyo-evolve/blob/main/src/cli.rs>
- `CLAUDE.md`：<https://github.com/yologdev/yoyo-evolve/blob/main/CLAUDE.md>
- `IDENTITY.md`：<https://github.com/yologdev/yoyo-evolve/blob/main/IDENTITY.md>
- `scripts/evolve.sh`：<https://github.com/yologdev/yoyo-evolve/blob/main/scripts/evolve.sh>
- `scripts/social.sh`：<https://github.com/yologdev/yoyo-evolve/blob/main/scripts/social.sh>
- `scripts/yoyo_context.sh`：<https://github.com/yologdev/yoyo-evolve/blob/main/scripts/yoyo_context.sh>
- `memory/active_learnings.md`：<https://github.com/yologdev/yoyo-evolve/blob/main/memory/active_learnings.md>
- `skills/communicate/SKILL.md`：<https://github.com/yologdev/yoyo-evolve/blob/main/skills/communicate/SKILL.md>
- `skills/social/SKILL.md`：<https://github.com/yologdev/yoyo-evolve/blob/main/skills/social/SKILL.md>
- `evolve.yml`：<https://github.com/yologdev/yoyo-evolve/blob/main/.github/workflows/evolve.yml>
- `social.yml`：<https://github.com/yologdev/yoyo-evolve/blob/main/.github/workflows/social.yml>
- `synthesize.yml`：<https://github.com/yologdev/yoyo-evolve/blob/main/.github/workflows/synthesize.yml>

### `evopaw` 本地代码

- 启动入口 CLI 检查：[evopaw/main.py](/home/hxd/agent_project/evopaw/evopaw/main.py:79)
- Claude 相关 options 封装：[evopaw/llm/claude_client.py](/home/hxd/agent_project/evopaw/evopaw/llm/claude_client.py:24)
- 主 Agent 调用 SDK：[evopaw/agents/main_agent.py](/home/hxd/agent_project/evopaw/evopaw/agents/main_agent.py:182)
- Skill Agent 调用 SDK：[evopaw/agents/skill_agent.py](/home/hxd/agent_project/evopaw/evopaw/agents/skill_agent.py:51)

### `claude_agent_sdk` 本地安装代码

- 默认 transport 走 `SubprocessCLITransport`：[/home/hxd/anaconda3/lib/python3.11/site-packages/claude_agent_sdk/client.py](/home/hxd/anaconda3/lib/python3.11/site-packages/claude_agent_sdk/client.py:136)
- 查找并启动 `claude` CLI：[/home/hxd/anaconda3/lib/python3.11/site-packages/claude_agent_sdk/_internal/transport/subprocess_cli.py](/home/hxd/anaconda3/lib/python3.11/site-packages/claude_agent_sdk/_internal/transport/subprocess_cli.py:63)
