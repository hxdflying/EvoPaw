# Hermes-Agent 与 Nanobot 在 Subagent / Skills / Hook 三个维度上的实现对比，以及对 EvoPaw 的借鉴建议

日期：2026-04-29
目标：从 **subagent / skills / hook** 三个具体维度切入，详细分析 `NousResearch/hermes-agent` 与 `HKUDS/nanobot` 的实现，并判断哪些部分值得 EvoPaw 借鉴，给出可落地的优先级清单。

> 本文与 `docs/improved_agent/hermes-vs-nanobot-multi-provider-analysis-2026-04-22.md` 互补：那一篇聚焦"多 provider 兼容"，这一篇聚焦"Subagent / Skills / Hook"。

---

## 0. 结论先行（TL;DR）

| 维度 | Hermes 做对的 | Nanobot 做对的 | EvoPaw 该不该抄 |
|---|---|---|---|
| **Subagent** | 没有真正的 subagent，把"边界 LLM 任务"统一在一个 auxiliary_client router 里 | `SubagentManager.spawn` 异步后台 + MessageBus 回注 + session_key 取消语义 | 借鉴 **Nanobot 的 task_id + cancel + 结果回注** 三件套 |
| **Skills** | `SKILL.md` 模板变量 (`${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}`) + 可选内联 shell + slash 命令双入口 | `build_skills_summary()` / `load_skills_for_context()` 两段式渐进披露 + `always=true` 自动加载 + `requires.bins` / `requires.env` 校验 | **强烈建议**：偷 Hermes 的占位符规约 + Nanobot 的 `always` / `requires` 元数据 |
| **Hook** | shell 命令为载体（`pre_tool_call` / `post_tool_call` / `pre_llm_call`）+ 白名单 + 用户同意 + 正则匹配 | Python `AgentHook` 类 + 6 个事件 + `CompositeHook` + `finalize_content` 可改写返回 | 借鉴 Nanobot 的 **`finalize_content` 改写语义** 与 **`CompositeHook` 组合器**；Hermes shell-hook 暂不需要 |

最重要的三句话：

1. **Subagent**：Nanobot 的 spawn / cancel / 异步回注比 EvoPaw 当前"同步 await `run_skill_agent`"更适合 task 型 skill 长耗时场景。
2. **Skills**：EvoPaw 的"reference / task 二分法"在表达力上**优于**两家，但在元数据丰富度上不如 Nanobot；占位符方案比 Hermes 简陋。
3. **Hook**：EvoPaw 的 `StreamSink` 抽象其实已经很接近 Nanobot 的 `AgentHook`，差的只是"事件粒度"和"可改写返回"。

---

## 1. 三家在 Subagent 上的设计

### 1.1 Hermes：根本没有 Subagent，只有 `auxiliary_client`

很多人误以为 Hermes 有 subagent，但**实际上 `agent/` 目录里没有任何 subagent / sub_agent / delegation 模块**。

它真正做的是：把"边界型 LLM 任务"（context 压缩、网页提取、视觉分析、会话搜索）统一在一个 LLM client router 里。

核心入口（`agent/auxiliary_client.py`）：

```python
def resolve_provider_client(
    provider: str,
    model: str = None,
    async_mode: bool = False,
    explicit_base_url: str = None,
    explicit_api_key: str = None,
    api_mode: str = None,
    is_vision: bool = False,
) -> Tuple[Optional[Any], Optional[str]]
```

设计点：

- 给定 `provider` + `model`，返回一个**已配置好认证 / base_url / 协议族**的客户端
- 支持 `provider="auto"` 时按优先级链路自动探测：用户主 provider → OpenRouter → Nous → 自定义端点 → Codex → API key 检测
- HTTP 402 / 429 自动 fallback 到下一个 provider（`_is_payment_error` / `_try_payment_fallback`）
- 配置里通过 `auxiliary.vision.provider` 这种 namespace 按任务覆盖

**Hermes 的"subagent"哲学**：
> 不需要"独立 agent"，只需要"独立 LLM 调用"。把它视为可插拔的 client，和主 agent 共用同一套 prompt 工具链。

这种设计的好处：实现成本极低（不需要单独的事件循环 / 工具集 / 取消逻辑）；坏处：表达力有限，没法像 Nanobot 那样跑长任务。

### 1.2 Nanobot：真正的 SubagentManager.spawn

Nanobot 在 `nanobot/agent/subagent.py` 里实现了**真正意义上的 subagent**。

核心 API：

```python
class SubagentManager:
    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:  # 返回 8 字符 task_id

    async def _announce_result(
        self, task_id: str, label: str, task: str,
        result: str, origin: dict[str, str], status: str
    ) -> None:  # 通过 MessageBus 把结果回注主 agent

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        # subagent 与主 agent 共享同一个 LLMProvider，但允许动态换 model

    async def cancel_by_session(self, session_key: str) -> int:
        # 按 session_key 批量取消挂在该会话上的所有 subagent
```

关键设计：

| 特性 | Nanobot 怎么做 |
|---|---|
| **触发方式** | LLM 通过 `SpawnTool` 工具调用，跟普通工具一样进 `tools.register(SpawnTool(manager=self.subagents))` |
| **执行模型** | 后台 `asyncio.create_task`，**不阻塞主 agent**，主 agent 可以继续聊天 |
| **结果回注** | 完成后通过 `MessageBus` 注入一条 `system` 频道的 `InboundMessage`，回到原 session |
| **生命周期** | `AgentRunner.run` 最大 15 轮迭代，`SubagentStatus` 跟踪阶段 / 工具事件 / 错误 |
| **session 隔离** | 每个 subagent 绑定 `session_key`，主 session 退出时 `cancel_by_session` 一锅端 |
| **provider 共享** | 默认与主 agent 共用同一个 `LLMProvider`；通过 `set_provider` 可换 |

调用伪代码（从 `loop.py` 推断）：

```python
# 主 agent 一次工具调用
tool_call = SpawnTool(task="搜索 X 并写报告", label="report-x")
task_id = await tool_call.run()  # 返回 "8 字符 ID"，主 agent 立刻继续

# ...几分钟后，bus 收到一条 InboundMessage
{
    "channel": "system",
    "content": f"[subagent:{task_id}] result: <报告内容>",
    ...
}
# 主 agent 把它当成普通用户消息处理，可以接着回应
```

### 1.3 EvoPaw 现状：同步 await + 6 工具的 Sub-Agent

EvoPaw 的 sub-agent 实现在 `evopaw/agents/skill_agent.py` 中：

```python
async def run_skill_agent(
    skill_name: str,
    skill_instructions: str,
    task_context: str,
    session_path: str,
    model: str,
    max_turns: int,
) -> str:
    options = build_sub_agent_options(...)  # allowed_tools=["Bash","Read","Write","Edit","Grep","Glob"]
    async for message in claude_agent_sdk.query(prompt=..., options=options):
        ...
    return result_text
```

特征：

- **同步 await**：`SkillDispatcher.dispatch(...)` 一直 await 到 sub-agent 出 final text
- **每次新建独立 query session**：状态不污染
- **固定 6 个工具**：Bash / Read / Write / Edit / Grep / Glob，硬编码在 `claude_client.py:65-86`
- **cwd 固定 `/workspace`**：跨 session 共享凭证 / cron / .config
- **没有 task_id**：单次调用是匿名的
- **没有 cancel**：用户没法主动 kill 一个慢 skill
- **结果是函数返回值**：通过 `dispatch` 链路一路 return 回主 agent 的工具结果消息

### 1.4 Subagent 维度对比

| 维度 | Hermes | Nanobot | EvoPaw |
|---|---|---|---|
| 是否真正"独立 agent" | ❌（router） | ✅ 后台任务 | ⚠️ 同步 await |
| 任务标识 | 无 | `task_id`（8 字符 UUID） | 无 |
| 执行模型 | 同步 LLM call | 异步后台任务 | 同步 await |
| 取消语义 | N/A | `cancel_by_session` | ❌ |
| 结果回注 | 直接 return | MessageBus + system 频道 | 直接 return |
| Provider 切换 | 每次 `resolve` 时按 role 选 | `set_provider` 动态改 | 配置时确定（claude-haiku） |
| 工具集 | 共享主 agent 工具链 | 独立 ToolRegistry | 独立 6 工具 |

### 1.5 EvoPaw 该借鉴什么

| 借鉴项 | 来源 | 价值 | 改造成本 |
|---|---|---|---|
| **task_id 标识** | Nanobot | 让飞书侧能查"我刚才那个 PDF 处理到哪了" | 低（在 `dispatch` 里加一个 UUID） |
| **后台异步 + MessageBus 回注** | Nanobot | 长 skill（投研报告 / 网页爬取）不阻塞主对话 | **中-高**（需要改 `runner.py` 的队列语义；要决定主 agent 是否能"先回话再被 sub-agent 打断" ）|
| **cancel_by_session** | Nanobot | `/new` 切 session 时干掉挂着的旧 sub-agent | 中（要在 `SessionManager` 上挂 task 引用） |
| **status 跟踪** | Nanobot | verbose 模式可以显示 "subagent#abc12345 进行到第 3 轮" | 低 |
| **auxiliary 角色路由** | Hermes | memory 压缩 / vision 用便宜模型，已经在做但没抽象 | 低（已有 `provider_runtime`） |

**不建议借鉴**：
- Hermes 的 "auxiliary client = 没有 subagent" 路线 —— EvoPaw 的 task skill 已经必须独立运行（带文件副作用），降级会丢能力。
- Nanobot 的 `SpawnTool` 当成 LLM 工具暴露 —— EvoPaw 已经有 `skill_loader` 渐进披露，再加一个 spawn 是冗余。

---

## 2. 三家在 Skills 上的设计

### 2.1 Hermes：SKILL.md + 模板变量 + slash 命令双入口

Hermes 的 skill 由四个文件协作（都在 `agent/`）：

| 文件 | 职责 |
|---|---|
| `skill_utils.py` | 解析 YAML frontmatter，元数据校验，platform / requires 过滤 |
| `skill_preprocessing.py` | 模板变量替换 (`${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}`) + 可选内联 shell `` !`cmd` `` |
| `skill_commands.py` | 把 skill 注册成 slash 命令（`/skill-name`），构造 invocation message |
| `skill_preprocessing.py` 配置开关 | `template_vars`（默认 on）/ `inline_shell`（默认 off） |

Skill 形态：

```markdown
---
name: gif-search
description: ...
platforms: ["macos", "linux"]
metadata:
  hermes:
    config:
      - key: wiki.path
        description: ...
        default: ~/wiki
    fallback_for_toolsets: [...]
    requires_toolsets: [...]
---

# 内容
当前 skill 目录：${HERMES_SKILL_DIR}
当前 session：${HERMES_SESSION_ID}

获取 git 分支：!`git rev-parse --abbrev-ref HEAD`
```

**Hermes 的 skill 调用是 slash 命令**，不是 LLM 工具调用：

```
用户输入 /gif-search dogs
    → resolve_skill_command_key()      标准化命令名
    → _load_skill_payload()             读 SKILL.md
    → preprocess_skill_content()        替换变量 + 跑内联 shell
    → _build_skill_message()            注入到下一条 message
    → 主 agent 用普通对话流处理
```

**亮点**：
- `${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}` 占位符是**显式契约**，不像 EvoPaw 那样在文档里口头约定
- 内联 shell `!`cmd`` 让 SKILL.md 可以**动态拼装上下文**（默认关闭，需配置开启）
- slash 命令使非 tool-calling 模型也能用 skill（兼容老模型）
- `requires.bins` / `requires.env` 缺失时 skill 自动隐藏

**不足**：
- 没有 reference vs task 的类型区分，所有 skill 都走 prompt 注入
- 没有渐进式披露 —— 全部 skill 元数据一次性放进 prompt（hermes 用 `disabled_skills` 手动剪）

### 2.2 Nanobot：两段式渐进披露 + always-on

Nanobot 在 `nanobot/agent/skills.py` 中实现了**两段式渐进披露**：

```python
# 阶段一：摘要（注入 system prompt）
def build_skills_summary() -> str:
    """返回所有 enabled skills 的 (name, description, path, available)
       元组列表，agent 据此决定要不要展开。"""

# 阶段二：完整内容（按需加载）
def load_skills_for_context(skill_names: list[str]) -> str:
    """读取指定 skills 的完整 SKILL.md 正文（去 frontmatter）"""
```

agent 的工作流是：

```
build_system_prompt():
    base_system + build_skills_summary()  # 摘要

LLM 决定要某个 skill →
    通过 read_file 工具读 skills/<name>/SKILL.md  # 自由展开
```

**亮点**：
- 渐进式披露的"展开动作 = 普通文件读"，不需要专门的 `skill_loader` 工具
- `always=true` 标记的 skill 自动注入完整内容（绕过披露）
- `requires.bins` / `requires.env` 失败时 skill 在摘要里被标 unavailable
- 内置 + workspace 两级 skills 目录（builtin / 用户定制）
- 9 个内置 skills 涵盖日常：clawhub / cron / github / memory / skill-creator / summarize / tmux / weather / my

**不足**：
- 没有 reference vs task 区分 —— 所有 skill 都靠 prompt 推理
- 没有占位符规约（比 Hermes 弱）
- 远程 skill 通过 `clawhub` 拉取（中心化，与 EvoPaw 自托管偏好不符）

### 2.3 EvoPaw 现状：reference / task 二分 + 三阶段披露

EvoPaw 是三家中**类型系统最强**的：

```yaml
# skills/<name>/SKILL.md
---
name: pdf
description: ...
type: task              # 或 reference / inline
version: 1.0.0
---
```

调用链：

```
阶段 1：<available_skills> XML 元数据   注入 skill_loader tool description
    （listing 所有 name + description + type）

阶段 2 (reference)：<skill_instructions>SKILL.md 内容</skill_instructions>
    返回给主 agent 自行推理

阶段 2 (task)：run_skill_agent 创建 Sub-Agent，
    SKILL.md 作为 Sub-Agent 的 system_prompt

阶段 2 (inline, 即 history_reader)：直接在 dispatcher 内分页返回
```

占位符（`evopaw/skills_runtime/instructions.py`）：

```python
{skill_base}    → /mnt/skills/<name>            # 容器内 SKILL 资源挂载点
{_skill_base}   → /mnt/skills/<name>            # 同上的下划线别名
{session_id}    → 当前 session_id（裸值，仅在 SKILL.md 文本内出现）
{session_dir}   → /workspace/sessions/<sid>     # session 工作目录
```

**亮点**：
- type 区分让"长跑 task"与"轻量 reference"分开优化（Sub-Agent 仅 task 需要）
- 三阶段披露比 Hermes / Nanobot 都细
- inline 类型（`history_reader`）省掉了起 Sub-Agent 的成本
- 18 个内置 skills 覆盖飞书办公全流程

**不足**：
- 占位符只有 3 个（`skill_base` / `session_id` / `session_dir`），相比 Hermes 的可扩展占位符规约弱
- 没有 `requires.bins` / `requires.env` 校验（如果 `pandoc` 没装，pptx skill 启动后才报错）
- 没有 `always=true` 机制（投研类 skill 每次都要 LLM 主动选）
- 没有 platform 字段（Linux-only skill 在 mac 开发环境会爆掉）
- `<available_skills>` XML 是一次性全量注入，没有"按 routing_key / sender_id 筛选可见 skill" 的能力

### 2.4 Skills 维度对比

| 维度 | Hermes | Nanobot | EvoPaw |
|---|---|---|---|
| Skill 形态 | YAML + Markdown | YAML + Markdown | YAML + Markdown |
| 类型系统 | 无 | 无 | ✅ reference / task / inline |
| 渐进披露 | 全量注入 | 摘要 + read_file 展开 | XML 摘要 + 工具调用展开（最严格） |
| 占位符 | `${HERMES_SKILL_DIR}` 等 | 无 | `{skill_base}` 等（少而粗） |
| 内联 shell | ✅（可关） | ❌ | ❌ |
| `always=true` | ❌ | ✅ | ❌ |
| `requires.bins/env` | ✅ | ✅ | ❌ |
| platforms 字段 | ✅ | ❌ | ❌ |
| 远程注册表 | 无（本地 + optional-skills/） | ✅ clawhub | 无（本地） |
| Slash 命令入口 | ✅（双入口） | ❌ | ✅（在 runner 拦截） |

### 2.5 EvoPaw 该借鉴什么

| 借鉴项 | 来源 | 价值 | 改造成本 |
|---|---|---|---|
| **`requires.bins` / `requires.env` 元数据** | Hermes + Nanobot | 启动时校验 pandoc / ghostscript / 飞书凭证；缺失时 skill 自动隐藏 | 低（在 `_build_skill_registry` 里加校验） |
| **`always=true` 自动加载** | Nanobot | 让"投研日报"等强约束 skill 不依赖 LLM 主动选 | 低（在 description XML 阶段拼接） |
| **占位符体系扩展** | Hermes | `${WORKSPACE_DIR}` / `${TODAY}` / `${SENDER_NAME}` 等 | 低-中（要划清"哪些占位符暴露给 LLM 看" vs "哪些只在 SKILL.md 内部展开"） |
| **可选内联 shell** | Hermes | SKILL.md 里 `!`date`` 实时拼当前日期到指令；**默认关，需 config 开** | 中（安全风险大，必须像 Hermes 一样白名单 + timeout + cwd 限定） |
| **platform 字段** | Hermes | mac dev 跑 evopaw 时自动跳过 Linux-only skill | 低 |

**不建议借鉴**：
- Nanobot 的 `clawhub` 中心化注册表 —— EvoPaw 走容器自托管路线，引入会带来分发/安全治理成本。
- Hermes 的 slash 命令注册成 skill 入口 —— EvoPaw 的 slash 命令在 Runner 层拦截，不进 LLM 上下文，与 skill 设计初衷不一样。
- 抹掉 reference / task 类型区分 —— EvoPaw 的二分法是真正的设计优势。

---

## 3. 三家在 Hook 上的设计

### 3.1 Hermes：shell 命令为载体的安全 hook

Hermes 在 `agent/shell_hooks.py` 中实现了一个**通过 shell 命令拦截事件**的 hook 系统。

核心 API：

```python
@dataclass
class ShellHookSpec:
    event: Literal["pre_tool_call", "post_tool_call", "pre_llm_call"]
    command: str         # shlex.split + shell=False
    matcher: str | None  # 正则匹配 tool name
    timeout: int = 60    # 默认 60s，最大 300s

def register_from_config():
    """从 config.yaml 注册所有 hook，第一次执行需要用户确认（白名单）"""

def _spawn(spec: ShellHookSpec, payload: dict) -> dict:
    """执行 shell 命令，stdin 传 JSON payload，stdout 解析 JSON 响应"""
```

事件类型：

| 事件 | 触发时机 | payload 关键字段 |
|---|---|---|
| `pre_tool_call` | 工具调用前 | `tool_name` / `tool_input` |
| `post_tool_call` | 工具调用后 | `tool_name` / `tool_output` |
| `pre_llm_call` | LLM 请求前 | `messages` / `model` |

**安全机制**（这是 Hermes 真正做对的地方）：

1. `shlex.split()` + `shell=False` —— 防注入
2. 第一次执行需要 TTY 弹"是否允许这个 hook 运行？"，allowlist 存在 `~/.hermes/shell-hooks-allowlist.json`
3. 默认 60s 超时，强制最大 300s
4. 正则 `matcher` 按工具名过滤，避免一个 hook 在所有工具上触发
5. 幂等注册去重

**hook 的"返回值"** 通过 stdout JSON 表达：

```json
{
    "decision": "allow" | "block",
    "reason": "...",
    "modifications": {
        "tool_input": {...}     // pre_tool_call 可改写输入
    }
}
```

Hermes 这种"hook 是外部 shell 命令"的设计很特别：
- **优点**：跨语言（hook 可以是 bash / python / go / rust）；进程隔离；用户可以用任何工具链
- **缺点**：每次启动子进程开销 50-200ms；payload 序列化开销；难做有状态 hook

### 3.2 Nanobot：Python AgentHook 类 + 6 事件 + Composite

Nanobot 的 hook 在 `nanobot/agent/hook.py`，是**纯 Python 类继承**：

```python
class AgentHook:
    async def before_iteration(self, ctx: AgentHookContext) -> None: ...
    async def on_stream(self, ctx: AgentHookContext, delta: str) -> None: ...
    async def on_stream_end(self, ctx: AgentHookContext) -> None: ...
    async def before_execute_tools(self, ctx: AgentHookContext) -> None: ...
    async def after_iteration(self, ctx: AgentHookContext) -> None: ...
    def finalize_content(self, ctx: AgentHookContext, content: str | None) -> str | None: ...

class CompositeHook(AgentHook):
    def __init__(self, hooks: list[AgentHook]): ...
    # 把每个事件分发给所有 hook，错误隔离 (_for_each_hook_safe)
```

事件粒度（注意：是 EvoPaw 的两倍）：

| 事件 | EvoPaw 现有的对应物 |
|---|---|
| `before_iteration` | ❌（EvoPaw 没有） |
| `on_stream` | ❌（EvoPaw 不消费 LLM 流式增量） |
| `on_stream_end` | ❌ |
| `before_execute_tools` | ✅ `PreToolUse` / `StreamSink.on_tool_use` |
| `after_iteration` | ❌（粒度比 PostToolUse 粗：一次迭代可能多个工具） |
| `finalize_content` | ❌ **关键缺失**（最后一刻可改写文本） |

**可改写性（mutability）** 是 Nanobot hook 系统的**杀手级特性**：

- `AgentHookContext` 是可变 dataclass，hook 可以直接 mutate `messages` / `tool_results`
- `finalize_content(ctx, content) -> str | None` 返回值会**替换**原始文本
- `before_execute_tools` 可以从 `ctx.tool_calls` 里删除某个工具调用（实现"工具拦截"）

实际用例（推断）：
```python
class RedactHook(AgentHook):
    def finalize_content(self, ctx, content):
        # 在最终回复发出前自动 redact 信用卡号 / 邮箱
        return re.sub(r"\d{16}", "[REDACTED]", content or "")

class TokenBudgetHook(AgentHook):
    async def before_iteration(self, ctx):
        if ctx.total_tokens > 100_000:
            ctx.tools = [t for t in ctx.tools if t.name != "web_browse"]
            # 强制简化工具集
```

### 3.3 EvoPaw 现状：StreamSink Protocol + 两条路径

EvoPaw 的 hook 体系在 `evopaw/agents/hooks.py`：

```python
class StreamSink(Protocol):
    async def on_tool_use(self, name: str, input_data: dict) -> None: ...
    async def on_tool_result(self, name: str, output: Any) -> None: ...
```

两条触发路径：

1. **Claude SDK 路径**：`build_stream_sink_hooks(sink)` 装配 SDK 的 `PreToolUse` / `PostToolUse` 回调，由 SDK 在 CLI 进程中触发
2. **HTTP 路径**（OpenAI / Anthropic）：backend 在工具循环中**显式 await** `stream_sink.on_tool_use(...)` / `on_tool_result(...)`

实现：`FeishuStreamSink` 把工具事件推到飞书 verbose 卡片（`💭 调用 search_memory` / `✅ 完成`）。

特征：

- 只有 2 个事件（pre / post tool）
- **不可改写**：sink 是单向通知，没法拦截或修改工具输入输出
- thread + sub-agent 不接 hook（防止刷屏）
- 异常被吞（保护主流程）

### 3.4 Hook 维度对比

| 维度 | Hermes | Nanobot | EvoPaw |
|---|---|---|---|
| 载体 | shell 命令 | Python 类 | Python Protocol |
| 事件数 | 3 | 6 | 2 |
| 可改写返回 | ✅（stdout JSON） | ✅（`finalize_content` / mutate ctx） | ❌（只通知） |
| 工具名过滤 | ✅（正则 matcher） | ❌（hook 自己判断） | ❌（hook 自己判断） |
| 安全机制 | 白名单 + 用户同意 + 超时 | 错误隔离 | 异常吞没 |
| 组合器 | ❌（线性 list） | ✅ `CompositeHook` | ❌（单 sink） |
| 流式增量 | ❌ | ✅ `on_stream` / `on_stream_end` | ❌ |
| 最终文本改写 | ✅ | ✅ `finalize_content` | ❌ |
| 跨语言扩展 | ✅（用户写 bash 也行） | ❌ | ❌ |

### 3.5 EvoPaw 该借鉴什么

| 借鉴项 | 来源 | 价值 | 改造成本 |
|---|---|---|---|
| **`finalize_content` 事件** | Nanobot | 主 agent 最终回复发飞书前自动 redact / 加签名 / 改格式 | 低（在 main_agent 出 final text 处加一个 hook 调用即可） |
| **`CompositeHook` 组合器** | Nanobot | verbose hook + audit hook + redact hook 同时启用 | 低（StreamSink 加一个 list 包装即可） |
| **正则 matcher 按工具过滤** | Hermes | 只想监控 Bash 工具时不用在 hook 里写 `if name == "Bash"` | 低 |
| **`before_iteration` / `after_iteration`** | Nanobot | 每轮迭代上报指标 / 检查预算 | 低 |
| **`on_stream` 流式增量** | Nanobot | 实现飞书"打字机"效果 | **中-高**（需要 backend 支持流式，目前 EvoPaw 都是非流式） |
| **可 mutate 的 context** | Nanobot | hook 可以删除危险工具调用（pre_tool 拦截） | **高**（要重新设计 StreamSink 类型，并审计所有 hook 的副作用边界） |

**不建议借鉴**：
- Hermes 的 shell 命令 hook —— 安全治理成本高（白名单 / 同意 / 沙箱），EvoPaw 是单租户飞书 bot，没那么多 hook 用户。
- Hermes 的 stdout JSON 协议 —— Python 内进程调用 + dataclass 比序列化便宜得多。

---

## 4. 推荐借鉴清单（按优先级）

下面按 **改造成本 ÷ 收益** 给出推荐顺序。

### P0：立刻就该做（改造成本低 + 收益明显）

1. **Skill 元数据加 `requires.bins` / `requires.env`** —— 启动时校验，pandoc / ghostscript / 飞书 token 缺失时 skill 自动 unavailable
2. **Skill 元数据加 `always=true`** —— 投研日报 / 默认人设这种强约束 skill 直接拼到 system prompt
3. **StreamSink 加 `finalize_content`** —— 主 agent 最终回复前可 redact / 加签名 / 转飞书富文本
4. **StreamSink 加 `CompositeHook`** —— 同时挂 verbose + audit + metrics

### P1：值得做（中等成本，结构性收益）

5. **Sub-Agent 加 `task_id`** —— `dispatch` 返回 `(task_id, result)`，verbose 卡片显示 `subagent#abc12345`
6. **Sub-Agent 加 `cancel_by_session`** —— `/new` 切 session 时干掉旧 sub-agent，避免遗留进程吃 API quota
7. **占位符体系扩展** —— 加 `${TODAY}` / `${SENDER_NAME}` / `${WORKSPACE_DIR}`，参考 Hermes 的命名
8. **StreamSink 加 `before_iteration` / `after_iteration`** —— 每轮上报 prometheus metrics

### P2：值得做但要谨慎（高成本或安全风险）

9. **Sub-Agent 后台异步 + MessageBus 回注** —— 让长 skill 不阻塞主对话；要重新设计 `runner.py` 队列语义
10. **SKILL.md 内联 shell `!`cmd``** —— 默认关，需配置开 + 白名单 + timeout + cwd 限定
11. **可 mutate 的 hook context** —— 重新设计 StreamSink 类型；审计所有 hook 副作用

### P3：不推荐做

- Hermes 风格的 shell 命令 hook（治理成本高）
- Nanobot 的 clawhub 远程 skill 注册表（与自托管路线冲突）
- 抹掉 reference / task 类型区分（EvoPaw 的设计优势）
- `SpawnTool` 当成 LLM 工具暴露（与 `skill_loader` 渐进披露重复）

---

## 5. 一图看懂三家差异

```
                  ┌─────────────────────────────────────────────┐
                  │              Subagent                        │
                  │                                              │
   Hermes:        │  ❌ 不存在，用 auxiliary_client 替代          │
                  │  ✅ 多 provider 自动 fallback                 │
                  │                                              │
   Nanobot:       │  ✅ SubagentManager.spawn(task) → task_id     │
                  │  ✅ 异步后台 + MessageBus 回注                │
                  │  ✅ cancel_by_session                         │
                  │                                              │
   EvoPaw:        │  ⚠️ 同步 await run_skill_agent                │
                  │  ✅ 短生命周期 + 6 工具                        │
                  │  ❌ 无 task_id / cancel                       │
                  └─────────────────────────────────────────────┘

                  ┌─────────────────────────────────────────────┐
                  │              Skills                          │
                  │                                              │
   Hermes:        │  ✅ ${HERMES_*} 占位符 + 内联 shell           │
                  │  ✅ slash 命令双入口                          │
                  │  ✅ requires.bins/env + platforms             │
                  │  ❌ 无类型区分 / 无渐进披露                   │
                  │                                              │
   Nanobot:       │  ✅ build_skills_summary 渐进披露             │
                  │  ✅ always=true 自动加载                      │
                  │  ✅ requires.bins/env                         │
                  │  ❌ 无类型 / 无占位符                         │
                  │                                              │
   EvoPaw:        │  ✅ reference/task/inline 三类               │
                  │  ✅ 三阶段披露（XML → 完整 → Sub-Agent）       │
                  │  ⚠️ 占位符少 / 无 requires / 无 always         │
                  └─────────────────────────────────────────────┘

                  ┌─────────────────────────────────────────────┐
                  │              Hook                            │
                  │                                              │
   Hermes:        │  ✅ shell 命令载体 + 白名单同意 + 超时        │
                  │  ✅ 正则 matcher 工具过滤                     │
                  │  ⚠️ 仅 3 事件 + 启动开销                      │
                  │                                              │
   Nanobot:       │  ✅ AgentHook 类 + 6 事件 + CompositeHook     │
                  │  ✅ finalize_content 可改写 + mutate ctx      │
                  │  ✅ on_stream 流式增量                        │
                  │                                              │
   EvoPaw:        │  ✅ StreamSink Protocol（SDK + HTTP 两路）     │
                  │  ⚠️ 仅 2 事件 + 不可改写                      │
                  │  ❌ 无 finalize / 无 composite                │
                  └─────────────────────────────────────────────┘
```

---

## 6. 关键文件锚点

### Hermes-Agent

- `agent/shell_hooks.py` — Hook 系统（shell 命令载体）
- `agent/auxiliary_client.py` — 不是 subagent，是 LLM client router
- `agent/skill_utils.py` — Skill YAML 元数据解析
- `agent/skill_commands.py` — Skill 注册成 slash 命令
- `agent/skill_preprocessing.py` — 占位符 + 内联 shell
- `tools/` — 40+ 工具集
- `skills/` + `optional-skills/` — 内置 / 可选 skill 库

### Nanobot

- `nanobot/agent/subagent.py` — `SubagentManager.spawn` / `cancel_by_session`
- `nanobot/agent/hook.py` — `AgentHook` 基类 + `CompositeHook`
- `nanobot/agent/loop.py` — 主循环（bus 驱动 + AgentRunner）
- `nanobot/agent/skills.py` — 两段式渐进披露
- `nanobot/agent/runner.py` — `AgentRunner.run` 工具循环
- `nanobot/agent/tools/` — base / registry / spawn / shell / filesystem 等 17 个工具
- `nanobot/bus/events.py` — `InboundMessage` / `OutboundMessage`
- `nanobot/skills/` — 9 个内置 skill（clawhub / cron / github / memory / skill-creator / summarize / tmux / weather / my）

### EvoPaw 对应锚点

- `evopaw/agents/skill_agent.py` — `run_skill_agent`（同步 await Sub-Agent）
- `evopaw/agents/main_agent.py:124-245` — agent_fn 装配 hook + skill_loader
- `evopaw/agents/hooks.py` — `StreamSink` / `FeishuStreamSink` / `build_stream_sink_hooks`
- `evopaw/llm/claude_client.py:65-86` — Sub-Agent 6 工具硬编码
- `evopaw/skills_runtime/dispatcher.py:129-187` — 4 分支 dispatch
- `evopaw/skills_runtime/instructions.py` — 占位符替换
- `evopaw/skills_runtime/registry.py` — Skill 注册（缺 requires / always 校验）
- `evopaw/agent_backends/openai_chat.py:155-313` — HTTP 路径手写工具循环 + 显式 await sink

---

## 7. 相关文档

- `docs/improved_agent/hermes-vs-nanobot-multi-provider-analysis-2026-04-22.md` — 多 provider 兼容角度的对比
- `docs/improved_agent/pi-mono-analysis-for-evopaw-2026-04-27.md` — pi-mono 风格分析
- `docs/improved_agent/yoyo-evolve-analysis-for-evopaw-2026-04-23.md` — yoyo-evolve 分析
- `docs/improved_agent/toolized-capabilities-feasibility-2026-04-24.md` — 工具化能力可行性
- `docs/subagent-skills-hook-pipeline-2026-04-29.md` — EvoPaw 自身 subagent / skills / hook 端到端链路
- `docs/multi-provider-final-plan-2026-04-27.md` — 多 provider 终版方案

## 8. 参考资料

### Hermes-Agent

- 仓库：https://github.com/NousResearch/hermes-agent
- `agent/shell_hooks.py`：https://raw.githubusercontent.com/NousResearch/hermes-agent/main/agent/shell_hooks.py
- `agent/auxiliary_client.py`：https://raw.githubusercontent.com/NousResearch/hermes-agent/main/agent/auxiliary_client.py
- `agent/skill_utils.py`：https://raw.githubusercontent.com/NousResearch/hermes-agent/main/agent/skill_utils.py
- `agent/skill_commands.py`：https://raw.githubusercontent.com/NousResearch/hermes-agent/main/agent/skill_commands.py
- `agent/skill_preprocessing.py`：https://raw.githubusercontent.com/NousResearch/hermes-agent/main/agent/skill_preprocessing.py

### Nanobot

- 仓库：https://github.com/HKUDS/nanobot
- `nanobot/agent/subagent.py`：https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/agent/subagent.py
- `nanobot/agent/hook.py`：https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/agent/hook.py
- `nanobot/agent/loop.py`：https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/agent/loop.py
- `nanobot/agent/skills.py`：https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/agent/skills.py
- `nanobot/bus/events.py`：https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/bus/events.py
