# EvoPaw 借鉴 Hermes-Agent / Nanobot 的可执行修改方案（审查修订版）

日期：2026-04-29

背景：本文档把 [`hermes-nanobot-subagent-skills-hook-analysis-2026-04-29.md`](./hermes-nanobot-subagent-skills-hook-analysis-2026-04-29.md) 中值得借鉴的部分整理成可执行修改计划。本版已根据严格审查结果修订：先修正文档前提，再给出可落地任务、风险边界和验收标准。

> 使用原则：不要把背景分析文档里的结论原样当实现方案。落地时以本文档的优先级和风险边界为准。

---

## 0. 审查修正摘要

### 0.1 必须修正的事实前提

| 原计划/背景说法 | 审查结论 | 落地影响 |
|---|---|---|
| EvoPaw 有 `reference / task / inline` 三类型 | 当前 `load_skills.yaml` 只有 `reference / task`；`history_reader` 是 `SkillDispatcher.dispatch()` 的名称特判，不是通用 inline 类型 | 文档和实现不要引入“inline 类型系统”，除非另立设计 |
| `{skill_base}` 指向 `/workspace/skills/<name>` | 当前代码实际使用 `/mnt/skills/<name>` | 占位符文档和迁移计划必须以 `/mnt/skills` 为准 |
| 当前内置 18 个 skills | 当前清单是 19 个，包含 `hk-investment-morning-report` | 验收标准写“全部 19 个 enabled skill” |
| EvoPaw 有 Hermes 风格 `/skill-name` slash 入口 | Runner 只支持 `/new`、`/verbose`、`/help`、`/status` | 不要把 slash skill 入口列为现有能力 |
| Hermes 已有同款 `requires.bins/env` 自动隐藏 | Nanobot 当前支持 `requires.bins/env`；Hermes 当前主线更偏 `metadata.hermes.config`、platform/toolset 条件 | `requires` 主要借鉴 Nanobot；Hermes 只作为元数据/占位符参考 |
| Hermes shell hook 可改写 `tool_input` | 当前可确认的是 pre-tool block 与 pre-LLM context 注入；不要把 `modifications.tool_input` 当已验证能力 | 可变 hook context 不能用 Hermes 作为强依据 |

### 0.2 当前代码锚点

| 能力 | 当前落点 |
|---|---|
| Skill registry | `evopaw/skills_runtime/registry.py` |
| Skill description / instruction placeholders | `evopaw/skills_runtime/instructions.py` |
| Skill dispatch | `evopaw/skills_runtime/dispatcher.py` |
| Sub-Agent 执行 | `evopaw/agents/skill_agent.py` |
| Sub-Agent allowed tools | `evopaw/llm/claude_client.py` |
| StreamSink Protocol | `evopaw/agent_backends/base.py` |
| Verbose sink | `evopaw/agents/hooks.py` |
| Runner 队列与 slash 命令 | `evopaw/runner.py` |
| Main Agent final text 持久化前位置 | `evopaw/agents/main_agent.py` |

---

## 1. 修订后的整体路线

### 1.1 优先级分布

| 优先级 | 任务 | 主要价值 | 风险边界 |
|---|---|---|---|
| P0 | Skill 元数据可用性、CompositeStreamSink、独立 Response Finalizer、占位符基线修正 | 低成本补齐可观测性和依赖诊断 | 不改变 `dispatch -> str`、不改变队列语义 |
| P1 | Sub-Agent task_id、取消机制设计、占位符体系扩展、HTTP iteration 事件 | 提升定位、治理和 prompt 稳定性 | cancel 需要处理 Runner 串行队列限制 |
| P2 | 显式后台 Sub-Agent、受限可变 hook context | 解决长任务阻塞和安全拦截 | 需要架构设计和集成测试 |
| P3 | 内联 shell、Hermes shell hook、clawhub、SpawnTool、抹掉类型系统 | 不建议做 | 安全/复杂度/重复能力问题 |

### 1.2 推荐推进顺序

```text
Week 1
├── P0-1  Skill 元数据：requires/platform/available reason
├── P0-2  CompositeStreamSink
├── P0-3  独立 Response Finalizer
└── P0-4  占位符基线修正与文档化

Week 2
├── P1-1  Sub-Agent task_id（保持 dispatch 返回 str）
├── P1-2  取消机制设计与 /stop 或 out-of-band cancel
├── P1-3  占位符体系扩展
└── P1-4  HTTP backend iteration 事件

Week 3+
├── P2-1  显式后台 Sub-Agent + 结果回注
└── P2-2  受限可变 hook context
```

### 1.3 不变量

以下不变量不得破坏：

1. `SkillDispatcher.dispatch(...) -> str` 保持不变，三个 backend 都依赖这个契约。
2. `reference / task` 二分保留；`history_reader` 继续作为 dispatcher 特例，除非另立通用 inline 类型设计。
3. `<available_skills>` XML → 完整 SKILL.md → task Sub-Agent 的渐进披露保留。
4. task skill 的 Sub-Agent `cwd=/workspace` 保留，除非先审计所有 task SKILL.md 相对路径。
5. `session_id` 不作为裸值进入普通用户对话，只以 workspace 路径形式注入 skill 指令。
6. thread 场景和 Sub-Agent 默认不推 verbose hook，避免刷屏。
7. 凭证仍写入 workspace 配置文件，不进入 LLM 文本上下文。

---

## 2. P0 任务

### P0-1：Skill 元数据加 `requires` / `platforms` / `available reason`

**主要借鉴**：Nanobot `requires.bins/env`；Hermes 的 skill 元数据与平台/条件过滤思路。

#### 现状

- `_build_skill_registry()` 只读取 `load_skills.yaml` 中的 `name/type/enabled`，再检查 `SKILL.md` 是否存在。
- 缺少依赖前置检查。缺少 `pandoc`、`soffice`、Tavily key、飞书凭证文件等问题会在 Sub-Agent 运行后才暴露。
- 当前 `load_skills.yaml` 里有 19 个 enabled skill，计划和测试都要按 19 个计算。

#### 目标

启动或构建 registry 时解析 skill 依赖，把 skill 标记为：

```python
{
    "type": "task",
    "path": Path(...),
    "available": True | False,
    "unavailable_reason": "",
    "requires": {...},
    "platforms": [...],
}
```

不可用 skill 仍出现在 `<available_skills>`，但标明 `available=false` 和原因。这样 LLM 能看到能力存在，但知道当前不能调用；dispatcher 也要做硬拦截。

#### Frontmatter 规约

建议支持以下最小集合：

```yaml
---
name: tavily_search
description: 使用 Tavily API 搜索互联网
type: task
version: "1.0"
platforms: ["linux"]
requires:
  bins: ["python3"]
  env: ["TAVILY_API_KEY"]
  files:
    - "/workspace/.config/tavily.json"
---
```

说明：

- `requires.bins`: 使用 `shutil.which()` 检查。
- `requires.env`: 检查 `os.environ` 非空。
- `requires.files`: 检查路径存在。用于 `feishu_ops` 这类凭证文件场景。
- `platforms`: 使用 `sys.platform` 或 `platform.system()` 归一化后检查。
- 缺省 `requires` 视为可用，向后兼容所有现有 SKILL.md。

#### 改动范围

| 文件 | 改动 |
|---|---|
| `evopaw/skills_runtime/registry.py` | 解析 SKILL.md frontmatter；新增 `_check_requirements()`、`_check_platforms()` |
| `evopaw/skills_runtime/instructions.py` | `<available_skills>` XML 加 `<available>` 和 `<unavailable_reason>` |
| `evopaw/skills_runtime/dispatcher.py` | dispatch 时不可用 skill 直接返回友好错误 |
| `evopaw/skills/*/SKILL.md` | 只给 2-3 个高价值 skill 试点，不一次性改完 |
| `tests/unit/test_skills_runtime_dispatcher.py` | 加不可用 skill 分发测试 |
| `tests/unit/test_skills_runtime_registry.py` | 新建 registry 元数据测试 |

#### 实施步骤

1. 在 `registry.py` 增加 frontmatter 解析函数，返回 dict；YAML 解析失败时只跳过新字段，不影响旧注册。
2. 实现 `_check_requirements(requires, workspace_root="/workspace") -> tuple[bool, str]`。
3. `_build_skill_registry()` 同时读取 `load_skills.yaml` 和 SKILL.md frontmatter；`type` 仍以 `load_skills.yaml` 为准，避免历史配置突然变更。
4. `_build_description_xml()` 输出：

   ```xml
   <skill>
     <name>tavily_search</name>
     <type>task</type>
     <available>false</available>
     <unavailable_reason>缺少环境变量 TAVILY_API_KEY</unavailable_reason>
     <description>...</description>
   </skill>
   ```

5. `dispatch()` 在 unknown skill 判断后、`history_reader` 特判前做 unavailable 守卫。
6. 试点建议：
   - `tavily_search`: `requires.env` 或 `requires.files`
   - `feishu_ops`: `requires.files: ["/workspace/.config/feishu.json"]`
   - `docx` 或 `pptx`: `requires.bins: ["soffice"]`

#### 测试要求

- `test_registry_requires_bins_missing`
- `test_registry_requires_env_missing`
- `test_registry_requires_files_missing`
- `test_registry_platform_mismatch`
- `test_description_xml_marks_unavailable`
- `test_dispatch_unavailable_skill_returns_error`

#### 验收标准

- 没有声明 `requires` 的现有 skill 仍可用。
- 缺少依赖时 skill 出现在 XML 中，但 `available=false`。
- 模型即使调用不可用 skill，dispatcher 也返回明确错误，不启动 Sub-Agent。
- `python3 -m pytest tests/unit/test_skills_runtime_dispatcher.py -v` 通过。

#### 风险

| 风险 | 处理 |
|---|---|
| 依赖检查过严导致可用 skill 被隐藏 | 先用 `available=false` 而不是从 XML 移除，便于观察 |
| env 和文件凭证重复表达 | 对文件凭证优先用 `requires.files`，不要把 secret 传入 LLM |
| 平台名不一致 | 先支持 `linux/darwin/win32` 三个归一化值 |

---

### P0-2：`CompositeStreamSink` 组合器

**主要借鉴**：Nanobot `CompositeHook` 的 fan-out 和错误隔离。

#### 现状

当前 `StreamSink` 只有 `on_tool_use/on_tool_result` 两个事件，verbose 模式只能挂一个 `FeishuStreamSink`。

#### 目标

新增 `CompositeStreamSink`，只组合当前已有工具事件，不引入 finalizer 或可变 context。

#### 改动范围

| 文件 | 改动 |
|---|---|
| `evopaw/agents/hooks.py` | 新增 `CompositeStreamSink` |
| `tests/unit/test_hooks.py` | 新增组合器测试 |

#### 实施步骤

```python
class CompositeStreamSink:
    def __init__(self, sinks: list[StreamSink]) -> None:
        self._sinks = list(sinks)

    async def on_tool_use(self, name: str, input_data: dict) -> None:
        for sink in self._sinks:
            try:
                await sink.on_tool_use(name, input_data)
            except Exception:
                logger.warning("sink %s on_tool_use failed", type(sink).__name__, exc_info=True)

    async def on_tool_result(self, name: str, output: Any) -> None:
        for sink in self._sinks:
            try:
                await sink.on_tool_result(name, output)
            except Exception:
                logger.warning("sink %s on_tool_result failed", type(sink).__name__, exc_info=True)
```

#### 验收标准

- 多个 sink 都收到 tool 事件。
- 其中一个 sink 抛异常，不影响后续 sink。
- `build_stream_sink_hooks(CompositeStreamSink([...]))` 在 Claude SDK 路径仍可用。

---

### P0-3：独立 Response Finalizer，而不是塞进 `StreamSink`

**主要借鉴**：Nanobot `finalize_content(ctx, content)` 的“最终回复改写”语义。

#### 审查结论

原计划把 `finalize_content` 加进 `StreamSink`，这不合适：

- `StreamSink` 当前只在 verbose 模式下创建，不适合承载安全 redact、格式转换这类应始终可用的最终响应处理。
- `StreamSink` 是工具事件通知端，把响应改写放进去会把 verbose 和安全/格式责任混在一起。
- finalizer 应在 `main_agent.py` 拿到 `final_text` 后、保存 ctx/raw 前执行。

#### 目标

新增独立的 response finalizer pipeline：

```python
class ResponseFinalizer(Protocol):
    async def finalize(self, text: str, context: ResponseFinalizeContext) -> str: ...
```

用于 redact、飞书富文本转换前置清理、签名、格式规范化等。

#### 改动范围

| 文件 | 改动 |
|---|---|
| `evopaw/agents/response_finalizer.py`（新） | `ResponseFinalizeContext`、`ResponseFinalizer`、`CompositeResponseFinalizer` |
| `evopaw/agents/main_agent.py` | `backend.run_turn()` 后、`if not final_text` 前后接入 finalizer |
| `evopaw/main.py` | 构建默认 finalizer，可先为空组合器 |
| `tests/unit/test_main_agent.py` | 验证 finalizer 调用位置和异常降级 |

#### 建议类型

```python
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class ResponseFinalizeContext:
    session_id: str
    routing_key: str
    root_id: str
    skills_called: list[str]
    role: str = "main"

class ResponseFinalizer(Protocol):
    async def finalize(self, text: str, context: ResponseFinalizeContext) -> str: ...
```

`CompositeResponseFinalizer` 串行 pipe，单个 finalizer 抛错时记录 warning 并沿用上一步文本。

#### 接入位置

在 `evopaw/agents/main_agent.py`：

1. `result = await backend.run_turn(req)`
2. `final_text = result.text`
3. `skills_called = list(result.skills_called)`
4. 调用 response finalizer
5. 再做空文本判断、`record_skills`、ctx/raw 持久化和 pgvector 索引

注意：Runner 的语音消息还会对 agent reply 做二次包装。finalizer 只处理 agent 原始回复；如果以后要处理语音包装后的最终文本，应在 Runner 另设 finalizer，不要混用。

#### 验收标准

- finalizer 能替换文本，并且替换后的文本进入 ctx/raw 和用户回复。
- finalizer 抛异常时主流程不崩，使用原始文本。
- verbose 关闭时 finalizer 仍然执行。

---

### P0-4：占位符基线修正与文档化

#### 现状

当前 `_SKILLS_MOUNT = "/mnt/skills"`，旧文档把 `{skill_base}` 写成 `/workspace/skills/<name>` 是错误前提。

当前占位符只有：

- `{skill_base}` -> `/mnt/skills/<name>`
- `{_skill_base}` -> `/mnt/skills/<name>`
- `{session_id}`
- `{session_dir}` -> `/workspace/sessions/<sid>`

#### 目标

先不大规模引入 `${TODAY}` 等新能力，只做基线修正：

1. 新增 `docs/skills-placeholders.md`，记录当前真实占位符。
2. 在行动计划和背景文档中停止使用 `/workspace/skills`。
3. 后续 P1-3 再做新占位符体系。

#### 改动范围

| 文件 | 改动 |
|---|---|
| `docs/skills-placeholders.md` | 新增当前占位符规约 |
| `evopaw/skills_runtime/instructions.py` | 可选：把注释写得更明确 |
| `tests/unit/test_skills_runtime_dispatcher.py` 或新测试 | 确认 `{skill_base}` 替换为 `/mnt/skills/<name>` |

#### 验收标准

- 文档中无 `/workspace/skills/<name>` 作为 skill base 描述。
- 测试明确保护 `/mnt/skills/<name>` 这一当前行为。
- 所有 19 个 enabled skill 的指令替换仍能构建。

---

## 3. P1 任务

### P1-1：Sub-Agent 加 `task_id`，但保持 `dispatch -> str`

**主要借鉴**：Nanobot 8 字符 task id。

#### 审查结论

原计划提出 `dispatch` 返回 `(task_id, result)`，这会破坏当前三个 backend 的工具结果契约。必须保持 `SkillDispatcher.dispatch(...) -> str`。

#### 目标

每次 task skill 调用生成 8 字符 task id，用于日志、错误文本、verbose 文本和未来 cancel/async 关联。

#### 改动范围

| 文件 | 改动 |
|---|---|
| `evopaw/agents/skill_agent.py` | `run_skill_agent(..., task_id: str | None = None, routing_key: str = "")` |
| `evopaw/skills_runtime/dispatcher.py` | task 分支生成 task_id 并透传 |
| `tests/unit/test_skill_agent.py` | task_id 生成和错误文本测试 |
| `tests/unit/test_skills_runtime_dispatcher.py` | dispatch 返回仍为 str |

#### 实施步骤

1. 在 dispatcher task 分支生成 `uuid.uuid4().hex[:8]`。
2. `run_skill_agent()` 接收可选 `task_id`，默认自动生成，便于单测。
3. 日志前缀统一为 `[subagent#abc12345]`。
4. 错误返回中包含 `task#abc12345`。
5. 正常返回不要强行包一层 JSON，避免污染主 Agent 工具结果。

#### 验收标准

- `dispatch("pdf", ...)` 返回类型仍是 `str`。
- 日志包含 `[subagent#...]`。
- 错误文本包含 task id。

---

### P1-2：取消机制重新设计，不能只在 `/new` 里加 `cancel_by_session`

**主要借鉴**：Nanobot `cancel_by_session`。

#### 审查结论

原计划低估了 Runner 的串行队列限制。当前同一 routing_key 的消息由一个 worker 串行处理；如果慢 task 正在 `await agent_fn()`，用户发的 `/new` 也会排队，无法及时进入 `_handle_slash()`。因此“在 `/new` 里调用 cancel”不能解决慢任务阻塞时的取消问题。

#### 目标

设计真正可用的取消机制，至少满足：

1. 用户能取消当前 routing_key 的进行中 agent/sub-agent。
2. cancel 不依赖排队后的普通消息被 worker 处理。
3. `/new` 可以顺便清理 session 关联任务，但不能作为唯一取消入口。

#### 推荐方案

引入显式 `/stop`，并让 Runner 在 `dispatch()` 入队前识别 `/stop`：

1. 如果 inbound 是 `/stop`，不进入同 routing_key 的普通队列。
2. Runner 维护 `_active_tasks: dict[str, asyncio.Task]`，记录当前 `_handle()` 或 agent task。
3. `/stop` 直接 cancel 当前 active task，并调用 Sub-Agent registry cancel。
4. `/new` 仍按队列正常处理；它可以在拿到执行机会后调用 `cancel_by_session` 做兜底清理。

#### 改动范围

| 文件 | 改动 |
|---|---|
| `evopaw/runner.py` | 新增 `/stop`，dispatch 快路径识别，active task 管理 |
| `evopaw/agents/sub_agent_registry.py`（新） | process-local task registry |
| `evopaw/agents/skill_agent.py` | 注册当前 Sub-Agent task，处理 `asyncio.CancelledError` |
| `tests/unit/test_runner.py` | `/stop` 不被同 session 队列阻塞 |
| `tests/unit/test_skill_agent.py` | cancel 返回和 registry 清理 |

#### 风险边界

- `claude_agent_sdk.query()` 的底层进程清理可能滞后；验收只能要求 Python task 在合理时间内收到 cancel，并记录清理状态。
- 不要在 Prometheus label 中使用 task_id；task_id 高基数，只进日志。
- process-local registry 不跨进程。如果未来多 worker 部署，需要外部协调机制。

#### 验收标准

- 慢 agent_fn 运行时发送 `/stop`，不等待慢任务结束即可返回“已取消”。
- `/stop` 不影响其他 routing_key。
- `/new` 仍创建新 session，并尝试清理该 routing_key 旧任务。

---

### P1-3：占位符体系扩展

**主要借鉴**：Hermes `${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}` 的显式契约。

#### 目标

在 P0-4 记录当前占位符后，引入新规约并保留旧 alias 一个版本周期。

#### 新规约

建议使用 `${EVOPAW_*}` 前缀，降低和 shell 环境变量混淆的概率：

```text
${EVOPAW_SKILL_NAME}
${EVOPAW_SKILL_BASE}      -> /mnt/skills/<name>
${EVOPAW_SESSION_ID}
${EVOPAW_SESSION_DIR}     -> /workspace/sessions/<sid>
${EVOPAW_ROUTING_KEY}
${EVOPAW_WORKSPACE_ROOT}  -> /workspace
${EVOPAW_TODAY}           -> YYYY-MM-DD
${EVOPAW_NOW}             -> YYYY-MM-DD HH:MM:SS TZ
```

旧 alias 保留：

```text
{skill_base}
{_skill_base}
{session_id}
{session_dir}
```

#### 改动范围

| 文件 | 改动 |
|---|---|
| `evopaw/skills_runtime/placeholders.py`（新） | 构建和替换占位符 |
| `evopaw/skills_runtime/instructions.py` | 调用新模块 |
| `docs/skills-placeholders.md` | 更新新旧规约 |
| `tests/unit/test_skills_runtime_instructions.py` | 新旧占位符替换测试 |

#### 注意事项

- `${EVOPAW_TODAY}` 的时区必须明确，默认建议读取配置，缺省用 `Asia/Shanghai` 或项目当前运行时区，不要隐式用服务器 UTC。
- 当前 `agent_fn` 没有 sender name 入参，不要在本轮加入 `${SENDER_NAME}`。
- 如果要支持转义，如 `\${EVOPAW_TODAY}`，需要单独测试；不要默认承诺。

#### 验收标准

- 新占位符可替换。
- 旧占位符仍可替换。
- `{skill_base}` 和 `${EVOPAW_SKILL_BASE}` 都指向 `/mnt/skills/<name>`。
- 所有 19 个 enabled skill 构建 instruction 不报错。

---

### P1-4：HTTP backend iteration 事件，Claude SDK 路径暂不承诺 ✅ 已完成（2026-04-29）

**主要借鉴**：Nanobot `before_iteration` / `after_iteration`。

#### 审查结论

OpenAI/Anthropic HTTP backend 是手写工具循环，可以较低成本插入 iteration 事件；Claude SDK backend 的轮次由 SDK 驱动，不一定有同等粒度。原计划把三条路径都当低成本是不准确的。

#### 目标

只在 `openai_chat` 和 `anthropic_messages` backend 中增加 iteration 事件；Claude SDK 路径暂不触发，文档和测试明确这一点。

#### 建议不要扩展 `StreamSink` Protocol

如果只用于 metrics，不必再次破坏 `StreamSink` Protocol。优先选择：

- 在 backend 内部直接记录 metrics；或
- 新增可选 `IterationObserver`，独立于 verbose sink。

#### 验收标准

- HTTP backend 每轮工具循环有 metrics 或 observer 事件。
- Claude SDK backend 测试明确“不触发 iteration 事件”。
- 不破坏现有 `StreamSink` runtime_checkable 兼容性。

#### 落地说明

实现选择：**metrics-only**（不引入 `IterationObserver` Protocol，API 表面更小）。

新增指标：

```text
evopaw_llm_tool_iterations_total{provider_id, runtime_family, role, outcome}
  outcome ∈ {"continue", "final"}
    - continue: 本轮命中 tool_calls / tool_use，进入下一轮请求
    - final:    本轮收到终止 finish_reason / stop_reason，函数返回 final text
```

`max_turns` 耗尽场景不会出现 final（直接抛 `ProviderMaxTurnsExceeded`），由
既有 `evopaw_llm_calls_total{outcome="max_turns_exceeded"}` 体现，不在新指标
里重复打点（测试硬保护）。

Helper：`evopaw.observability.metrics.record_llm_tool_iteration(...)`。

调用点：

- `evopaw/agent_backends/openai_chat.py`：`run_turn` 工具循环每轮一次。
- `evopaw/agent_backends/anthropic_messages.py`：`run_turn` 工具循环每轮一次。
- `evopaw/agent_backends/claude_sdk.py`：**不导入** helper（测试 source 级硬保护）。

测试覆盖（新增 14 个）：

- `tests/unit/test_openai_chat_backend.py::TestIterationMetric`（4 个）
- `tests/unit/test_anthropic_messages_backend.py::TestIterationMetric`（4 个）
- `tests/unit/test_claude_sdk_backend.py::TestNoIterationMetricForSDK`（2 个，
  含 source 级硬保护）
- `tests/unit/test_metrics.py::TestRecordLlmToolIteration`（4 个）

`StreamSink` Protocol 完全未改动，`runtime_checkable` 兼容性保留。

---

## 4. P2 任务

### P2-1：显式后台 Sub-Agent + 结果回注 ✅ 已完成（2026-04-29）

**主要借鉴**：Nanobot `SubagentManager.spawn`、后台 task、完成后回注。

#### 审查结论

这是架构级任务，不应默认改变所有 task skill。建议只对明确标记的长任务启用后台模式。

#### 推荐规约

在 skill 元数据或 dispatch task_context 中显式声明：

```yaml
execution:
  mode: foreground | background
```

默认 `foreground`，保持现有行为。只有 `background` 才立即返回：

```text
已启动后台任务 task#abc12345：investment-report。完成后我会在当前会话回复结果。
```

#### 回注策略

优先直接通过 `Sender` 发飞书消息或更新卡片，不自动把完整结果注入 main agent 上下文。原因：

- 避免长报告污染当前对话历史。
- 避免后台结果触发主 Agent 再次工具调用。
- 飞书用户侧最需要的是结果通知，不一定需要二次总结。

如确实需要二次总结，应作为 `background.followup: summarize` 的显式能力另行设计。

#### 前置依赖

- P1-1 task_id
- P1-2 `/stop` / cancel 机制
- P0-1 metadata 可用性

#### 落地说明

实现路径（registry → dispatcher → main_agent）：

1. **Registry 层**（`evopaw/skills_runtime/registry.py`）：新增
   `_parse_execution_mode(front)`，解析 SKILL.md frontmatter 的
   `execution.mode`，合法值 `foreground|background`，缺省 / 非法值降级为
   `foreground`。registry 条目新增 `execution_mode` 字段。
2. **Dispatcher 层**（`evopaw/skills_runtime/dispatcher.py`）：`__init__`
   新增 `result_callback: Callable[[str, str, str], Awaitable[None]] | None`
   参数；`dispatch()` 在 task 型分发前读 `execution_mode`，`background`
   分支走 `_spawn_background_task`：
    - `asyncio.create_task` spawn `_run_and_callback`；
    - 注册到 `SubAgentRegistry`（让 `/stop` 能 cancel）；
    - 任务自然结束 → 调用 `result_callback(task_id, skill_name, result_text)`；
    - `CancelledError` → 不调用 callback，仅记录日志；
    - 任意异常都被吞，避免影响 `dispatch -> str` 不变量。
3. **Main Agent 层**（`evopaw/agents/main_agent.py`）：HTTP backend
   （`openai_chat` / `anthropic_messages`）路径下注入 `_bg_result_callback`
   闭包，捕获 `sender / routing_key / root_id`，通过
   `sender.send(routing_key, msg, root_id)` 把结果以
   `📌 后台任务 task#xxx（skill）已完成：\n\n{result_text}` 形式推送。
   闭包内部 try/except 兜底，sender 抛错不影响 dispatcher 的清理路径。
   Claude SDK MCP server 路径不接入（保持 P2 改造的「SDK 自管工具调用」边界）。

不变量保持：`SkillDispatcher.dispatch -> str` 未变；foreground 路径完全不
触碰 `SubAgentRegistry` / `result_callback`；`StreamSink` Protocol 未改。

测试覆盖（新增 17 个）：

- `tests/unit/test_skills_runtime_registry.py::TestParseExecutionMode`
  （8 个：缺省、block 非 dict、mode 缺失、mode 非 string、显式
  foreground、显式 background、大小写/空格归一化、未知值降级）
- `tests/unit/test_skills_runtime_registry.py::TestRegistryExecutionMode`
  （3 个：默认 foreground、显式 background、非法值降级）
- `tests/unit/test_skills_runtime_dispatcher.py::TestDispatchBackgroundMode`
  （7 个：立即返回 task_id 提示 + 注册到 registry、callback 在完成时
  收到正确参数 + 注销、sub-agent crash 时 callback 收到错误文本、cancel
  时不触发 callback、callback=None 仅日志、callback 抛错不破坏注销、
  foreground 不触碰 registry）
- `tests/unit/test_main_agent.py::TestRuntimeFamilyDispatch`
  （2 个：HTTP backend 注入 callback 并通过 sender.send 推送、
  sender 抛错被闭包吞掉不冒到 dispatcher）

---

### P2-2：受限可变 hook context ✅ 已完成（2026-04-29）

**主要借鉴**：Nanobot mutable `AgentHookContext`。

#### 目标

只支持工具调用拦截，不支持任意 mutate messages：

```python
class ToolDecision:
    action: Literal["allow", "block"]
    reason: str = ""
    rewritten_input: dict | None = None
```

#### 风险边界

- 仅 HTTP backend 先做；Claude SDK hook 能否等价拦截需单独验证。
- 不允许 hook 直接改 conversation messages。
- 所有 block 都要进入审计日志。

#### 落地说明

实现路径（base → backends → tests）：

1. **Base 层**（`evopaw/agent_backends/base.py`）：新增 Pydantic 严格模型
   `ToolDecision(extra="forbid")` 和 `runtime_checkable` Protocol `ToolGate`，
   只暴露 `before_tool_use(name, input_data) -> ToolDecision` 一个 hook 点。
   `TurnRequest` 新增 `tool_gate: ToolGate | None = None` 字段。
2. **HTTP backends**（`evopaw/agent_backends/openai_chat.py` /
   `anthropic_messages.py`）：在 dispatch tool_call 前调用
   `_consult_tool_gate(req, name, args)`：
    - `tool_gate=None` → `ToolDecision(action="allow")`，零开销。
    - 抛错或返回非 `ToolDecision` → 按 `allow` 兜底（fail-safe），
      避免 hook bug 影响主流程。
    - `action="block"` → 跳过 dispatch，调用 `record_error(...)` 写审计
      log，把 `reason` 文本作为 tool result 写回 messages，
      被拦截的 skill **不**进入 `skills_called`。
    - `action="allow"` 且 `rewritten_input` 非 None → 用 rewritten_input
      替换原 args 后正常 dispatch。
    - 其它 action 值 → 按 `allow` 兜底（不抛错）。
3. **Claude SDK backend**（`evopaw/agent_backends/claude_sdk.py`）：源码
   级**不**导入 `ToolGate` / `ToolDecision`，由测试 source-grep 硬保护
   （SDK 自管工具调用，不接入 P2-2）。

不变量保持：`StreamSink` Protocol 未扩展；`TurnRequest` 仅新增可选字段，
向后兼容；`record_error` 复用既有审计通道。

测试覆盖（新增 17 个）：

- `tests/unit/test_openai_chat_backend.py::TestToolGate`（8 个）
- `tests/unit/test_anthropic_messages_backend.py::TestToolGate`（8 个）
- `tests/unit/test_claude_sdk_backend.py::TestNoToolGateForSDK`（1 个
  source 级硬保护）

---

## 5. P3：不推荐做

| 不做 | 理由 |
|---|---|
| SKILL.md 内联 shell `!`cmd`` | 安全风险高，且大多数需求可由内置占位符解决；EvoPaw 有 `skill-creator`，允许动态 shell 会放大供应链风险 |
| Hermes shell 命令 hook 系统 | EvoPaw 当前是单租户飞书 bot，不需要用户级任意 shell hook；白名单、同意、沙箱治理成本高 |
| Nanobot clawhub 远程 skill 注册表 | 与容器自托管路线冲突，会引入分发、版本和安全治理问题 |
| Nanobot `SpawnTool` 暴露给 LLM | 与 `skill_loader` 渐进披露重复；后台执行应由 dispatcher 内部根据 metadata 决策 |
| 抹掉 `reference/task` 类型系统 | 这是 EvoPaw 相对 Hermes/Nanobot 的优势 |
| 引入通用 `inline` 类型 | 当前只有 `history_reader` 名称特判；若要通用 inline，需要另立设计，不在本计划内 |

---

## 6. 工程纪律

### 6.1 每项独立 PR

每个 P 项独立成 PR。不要把 P0-1 metadata、P0-3 finalizer、P1 cancel 混在一个 PR。

PR 描述必须包含：

- 对应章节
- 改动文件
- 新增测试
- 回滚方式
- 对 `dispatch -> str`、Runner 队列、凭证不进 LLM 这些不变量的影响说明

### 6.2 测试要求

每个 PR 至少运行：

```bash
python3 -m pytest tests/unit/test_skills_runtime_dispatcher.py -v
python3 -m pytest tests/unit/test_hooks.py -v
python3 -m pytest tests/unit/test_main_agent.py -v
python3 -m pytest tests/unit/test_runner.py -v
```

涉及 backend 时追加：

```bash
python3 -m pytest tests/unit/test_openai_chat_backend.py -v
python3 -m pytest tests/unit/test_anthropic_messages_backend.py -v
python3 -m pytest tests/unit/test_claude_sdk_backend.py -v
```

涉及 integration 时追加：

```bash
python3 -m pytest tests/integration/ -m "not llm" -v
```

### 6.3 文档同步

必须同步更新：

- `CLAUDE.md`：新增机制影响开发者操作时更新。
- `docs/skills-placeholders.md`：P0-4 / P1-3 更新。
- 本文档：完成项标注 PR 号和状态。

---

## 7. 背景文档回溯参考

| 本文档章节 | 回溯背景文档 |
|---|---|
| P0-1 Skill metadata | 背景 §2 Skills 维度，但以 Nanobot `requires` 为主要依据 |
| P0-2 CompositeStreamSink | 背景 §3 Nanobot `CompositeHook` |
| P0-3 Response Finalizer | 背景 §3 Nanobot `finalize_content` |
| P1-1 / P1-2 task_id / cancel | 背景 §1 Nanobot SubagentManager |
| P1-3 占位符 | 背景 §2.1 Hermes `${HERMES_*}` |
| P1-4 iteration | 背景 §3.2 Nanobot AgentHook |
| P2-1 后台 Sub-Agent | 背景 §1.2 Nanobot spawn / MessageBus |
| P2-2 可变 hook context | 背景 §3.2 Nanobot mutable context |

### 相关文档

- [`docs/improved_agent/hermes-nanobot-subagent-skills-hook-analysis-2026-04-29.md`](./hermes-nanobot-subagent-skills-hook-analysis-2026-04-29.md)
- [`docs/improved_agent/hermes-vs-nanobot-multi-provider-analysis-2026-04-22.md`](./hermes-vs-nanobot-multi-provider-analysis-2026-04-22.md)
- [`docs/subagent-skills-hook-pipeline-2026-04-29.md`](../subagent-skills-hook-pipeline-2026-04-29.md)
- [`docs/multi-provider-final-plan-2026-04-27.md`](../multi-provider-final-plan-2026-04-27.md)
