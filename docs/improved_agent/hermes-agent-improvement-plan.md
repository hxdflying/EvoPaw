# EvoPaw 基于 Hermes Agent 的改进方案

> 本文基于 2026-04-24 对 `NousResearch/hermes-agent` `main` 分支的再次阅读，重新校准 EvoPaw 的改进方向。
>
> 文档定位：这是对 [harness-improvement-plan.md](./harness-improvement-plan.md) 的产品化运行时补充。前者回答“Agent Harness 应有哪些层”，本文回答“这些层如何落成一个可长期运行、可治理、可自我改进的 Agent Runtime”。

---

## 0. 本次优化结论

Hermes Agent 最值得 EvoPaw 学的不是多平台数量、工具数量或供应商数量，而是它把 Agent 运行时拆成了一组稳定契约：

- 稳定 system prompt snapshot 与每轮 ephemeral overlay 分离。
- 记忆是有界热记忆 + 会话搜索 + 可选外部 provider，而不是无限追加文件。
- Skills 是可创建、可 patch、可扫描、可回滚的 procedural memory。
- 工具不是散落的函数，而是带 toolset、availability、result budget、approval gate 的 registry。
- session store 是检索、恢复、统计、成本、压缩 lineage、学习闭环的数据根。
- cron/delegation/subagent 都是 fresh execution context，不能偷用普通对话的隐式上下文。

对 EvoPaw 的改进优先级因此需要重新排序：

1. `P0` 先修运行时安全与上下文边界：停止生产默认 `bypassPermissions`，缩小 Sub-Agent cwd，补危险命令审批、路径校验、context 文件扫描、tool result 上限。
2. `P0` 同步建立 `prompt_builder`、`memory_service`、`state.db + FTS5 session_search` 三个基础件。
3. `P1` 再做 Skill 生命周期、tool registry/toolsets、ContextEngine、cron fresh task runtime。
4. `P2` 才把 `skill-creator` 的 eval 资产接入反思闭环，做候选 skill 的 quarantine/canary/promote。
5. `P3` 再考虑 provider runtime、gateway core、多平台、workflow/code execution、外部 memory provider。

一句话：**EvoPaw 不应该照搬 Hermes 的“大一体式 agent loop”，而应该吸收 Hermes 已经验证过的运行时契约，并保持 EvoPaw 当前较好的模块化边界。**

---

## 1. 对 Hermes Agent 的再理解

这次重点对照了 Hermes 的实现文件，而不只看 README/文档页。下表列出对 EvoPaw 最有迁移价值的上游事实：

| Hermes 位置 | 已验证的关键设计 | 对 EvoPaw 的启发 |
|---|---|---|
| [`run_agent.py`](https://github.com/NousResearch/hermes-agent/blob/main/run_agent.py) | `AIAgent` 是主循环，负责 prompt、tool dispatch、fallback、compression、session persistence、background review | EvoPaw 不要复制 10k+ 行主循环，但要把主循环责任显式拆成模块 |
| [`agent/prompt_builder.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/prompt_builder.py) | system prompt 由 identity、memory、skills index、context files、platform hints 等分层组装；context 文件进入 prompt 前扫描注入风险 | EvoPaw 需要把 `main_agent.py` 中的 prompt 拼接移到 `prompt_builder.py` |
| [`tools/memory_tool.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/memory_tool.py) | `MEMORY.md` / `USER.md` 是 bounded entries；mid-session 写盘但不改变本轮 system prompt snapshot | EvoPaw 的热记忆应小而精，并区分“写入持久化”和“下轮才注入” |
| [`tools/session_search_tool.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/session_search_tool.py) | FTS5 搜索命中消息，按 session 去重，再用辅助模型生成 focused summary | EvoPaw 不能只依赖 pgvector；需要精确短语/关键词历史召回 |
| [`hermes_state.py`](https://github.com/NousResearch/hermes-agent/blob/main/hermes_state.py) | SQLite WAL + FTS5 + session lineage + token/cost/tool counts | `index.json + JSONL` 应降级为审计副本，状态主存储转向 SQLite |
| [`tools/skill_manager_tool.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/skill_manager_tool.py) | `skill_manage` 支持 create/edit/patch/delete/write_file/remove_file；patch 是常用修复路径 | EvoPaw 的 `skill-creator` 应扩展为 Skill 管理服务，而不是只会创建 |
| [`tools/skills_guard.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/skills_guard.py) | 外部 skill 安装前扫描，按 source/trust/verdict 决策 allow/block/ask | EvoPaw 做 external dirs 前必须先做 trust 和 scan |
| [`tools/registry.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/registry.py) | 每个工具带 schema、toolset、check_fn、max_result_size、dispatch | EvoPaw 的 Skill Runtime 需要内部 tool registry，即使 Main Agent 仍只暴露 `skill_loader` |
| [`tools/approval.py`](https://github.com/NousResearch/hermes-agent/blob/main/tools/approval.py) | 危险命令检测、session approval、permanent allowlist、smart approval、cron deny 策略集中在一个 gate | EvoPaw 需要单一 PermissionGate，不能让各 Skill 自己决定能不能执行 |
| [`agent/context_engine.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/context_engine.py) | ContextEngine 抽象负责 token state、compression、session lifecycle、可选工具 | EvoPaw 需要从工具函数式 `context_mgmt.py` 升级到策略对象 |
| [`agent/subdirectory_hints.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/subdirectory_hints.py) | 访问子目录时懒加载局部 `AGENTS.md` / `CLAUDE.md`，追加到 tool result 而不改 system prompt | EvoPaw 可做轻量局部上下文发现，且不破坏 prompt cache |
| [`agent/memory_manager.py`](https://github.com/NousResearch/hermes-agent/blob/main/agent/memory_manager.py) | 内建 memory 永远存在，外部 memory provider 最多一个，统一 prefetch/sync/tool/hook | EvoPaw 可先做 local provider，再预留远端 provider，而不是马上接外部服务 |
| [`cron/scheduler.py`](https://github.com/NousResearch/hermes-agent/blob/main/cron/scheduler.py) | cron 有独立 session、toolset、delivery、timeout、workdir、disabled toolsets | EvoPaw 的 CronService 应从“消息回灌”升级为 first-class task runtime |
| [`hermes_cli/runtime_provider.py`](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/runtime_provider.py) | provider resolution 统一处理 API mode、base_url、credential pool、fallback | EvoPaw 现在不需要 18 个 provider，但需要统一模型/辅助任务路由 |

这些事实带来的最大调整是：**Hermes 的学习闭环依赖一组前置运行时能力。没有状态库、权限门、prompt 分层、memory service 和 skill lifecycle，直接做“自进化”会把错误固化为资产。**

---

## 2. EvoPaw 当前状态复盘

### 2.1 已有优势

EvoPaw 已经具备一些很好的基础，不需要推倒重来：

- Main Agent 只通过 `skill_loader` 获取外部能力，渐进式披露方向正确。
- `reference` / `task` 两类 Skill 清晰，任务型 Skill 用短生命周期 Sub-Agent 执行。
- Feishu `p2p/group/thread` routing_key 模型已经落地。
- per-routing_key 队列保证同一会话串行、不同会话并行。
- `soul.md/user.md/agent.md/memory.md` bootstrap 和 `ctx.json/raw.jsonl/pgvector` 已经形成记忆雏形。
- `history_reader` 内联是正确优化，避免把简单分页也交给 Sub-Agent。
- `skill-creator` 目录已有 `run_eval.py`、`run_loop.py`、benchmark 聚合、grader/analyzer/comparator 等评测资产。
- CronService、CleanupService、TestAPI、Prometheus 表明系统化意识已经存在。

### 2.2 需要立即纠偏的短板

下面这些短板会阻碍 EvoPaw 变成长期运行系统，应前置处理：

1. `build_main_agent_options()` 与 `build_sub_agent_options()` 都使用 `permission_mode="bypassPermissions"`，生产长期运行风险过高。
2. `SkillLoaderTool` 提示当前 session 是 `/workspace/sessions/{sid}`，但 task 型 Skill 实际传给 Sub-Agent 的 `session_path` 是 `/workspace`，cwd 边界过宽。
3. `build_sub_agent_options()` 默认开放 `Bash/Read/Write/Edit/Grep/Glob`，没有按 Skill 元数据裁剪。
4. `load_skills.yaml` 中部分 `path` 字段被注册表忽略，Skill 元数据存在双源漂移。
5. `SKILL.md` frontmatter 当前主要只读 `description`，缺少 source/trust/status/allowed_tools/output_budget 等运行时字段。
6. `ctx.json` 是裸 list，缺少 schema、captured_at、model、model_ctx_limit、compression_count、recovery pointer。
7. `context_mgmt.py` 硬编码 `_MODEL_CTX_LIMIT = 32000`，与多模型/多 provider 演化冲突。
8. `SessionManager` 使用 `index.json + JSONL`，可读但不支撑 FTS5、lineage、token/cost/tool analytics。
9. `CronService` 只是构造 `InboundMessage` 回灌 `Runner`，默认继承普通消息路径，缺少 fresh session、tool policy、递归保护。
10. Feishu 层已有 `sender_id`，但 agent/skill/runtime 没有充分利用用户身份做审批、审计和权限隔离。

---

## 3. 迁移原则

### 3.1 学运行时契约，不学文件体量

Hermes 的 `run_agent.py` 很强，但也很大。EvoPaw 当前模块边界更清晰，应该保留：

- `agents/main_agent.py` 只做 Agent turn 编排。
- `tools/skill_loader.py` 只做 Skill discovery / invocation。
- `memory/`、`session/`、`cron/`、`llm/` 各自独立演进。

优化方向不是把逻辑塞进一个“超级主循环”，而是把 Hermes 主循环里的职责拆回 EvoPaw 的模块：

| 职责 | EvoPaw 目标归属 |
|---|---|
| prompt assembly | `evopaw/agent_runtime/prompt_builder.py` |
| memory writes and hot snapshot | `evopaw/memory/service.py` |
| session persistence/search | `evopaw/session_store/` 或 `evopaw/session/db.py` |
| tool/skill policy | `evopaw/runtime/tool_registry.py` + `evopaw/security/permission_gate.py` |
| context compression | `evopaw/context/engine.py` |
| cron isolated execution | `evopaw/cron/agent_runner.py` |
| learning loop | `evopaw/learning/` |

### 3.2 Main Agent 仍保持窄工具面

Hermes 直接给 Agent 暴露很多 tool schemas。EvoPaw 当前“Main Agent 只看见 `skill_loader`”是一个更强的治理边界，不应轻易放弃。

建议保留：

- Main Agent 只暴露 `skill_loader`。
- Skill Runtime 内部再引入 tool registry/toolsets。
- Sub-Agent 的工具面由 Skill frontmatter + PermissionGate 决定。

也就是说，Hermes 的 tool registry 对 EvoPaw 的迁移目标不是“把所有工具暴露给主模型”，而是“给 Skill 执行层建立统一工具治理”。

### 3.3 热记忆与 topic-first 冷记忆并不冲突

上一份 Harness 方案强调 topic-first memory。Hermes 强调 bounded hot memory。两者可以组合：

- `user.md` / `memory.md`：小而精的热记忆，始终注入或按稳定 prompt snapshot 注入。
- `memory/topics/*.md`：冷记忆/长文档/项目事实，按需检索或打开。
- `state.db + FTS5`：会话历史精确搜索。
- `pgvector`：跨会话语义召回和 learning sample mining。

关键是不要让 `memory.md` 变成无限增长日志，也不要让所有 durable facts 都沉到 topic 文件导致每轮都需要检索。

### 3.4 cron、delegation、subagent 默认 fresh context

Hermes 的一个核心纪律是：子任务默认不知道父上下文。

EvoPaw 应明确：

- Cron 任务默认 fresh session。
- Sub-Agent 默认只知道 `skill_instructions + task_context + declared inputs`。
- Delegation 如果未来引入，必须显式传入背景，不继承完整对话。
- 任何自动任务不能创建新的自动任务，除非显式授权。

---

## 4. P0：先修运行时不变量

P0 不是“功能期”，而是把长期运行的底线补齐。建议 1-2 周内优先做完。

### 4.1 收紧 Sub-Agent 执行边界

当前问题：

- `skill_loader.py` 中 task 型 Skill 调用 `run_skill_agent(..., session_path="/workspace")`。
- `claude_client.py` 中 Sub-Agent 默认有广泛文件/命令工具。

目标：

- Sub-Agent cwd 必须是 `/workspace/sessions/{session_id}`。
- Skill 资源通过只读挂载路径 `/mnt/skills/{skill_name}` 访问。
- 输出只允许写到 session `outputs/`、`tmp/`，需要发布到 Feishu 的文件走 Sender/Delivery 层。
- 默认工具集从 `Bash/Read/Write/Edit/Grep/Glob` 改为按 Skill 声明裁剪。

建议变更：

- 修改 `evopaw/tools/skill_loader.py`：
  - `_workspace_root = "/workspace"` 改为 `_session_dir = f"/workspace/sessions/{session_id}"`。
  - 传入 `session_path=_session_dir`。
  - 对 `task_context` 增加最大长度限制和结构化解析错误返回。
- 修改 `evopaw/llm/claude_client.py`：
  - `build_sub_agent_options(..., allowed_tools=None, permission_mode=None)`。
  - 默认 allowed_tools 来自 Skill metadata。
  - 生产默认 permission_mode 不再是 `bypassPermissions`。

验收标准：

- 任意 task Skill 的 cwd 都不能读写其他 session。
- `../`、绝对路径逃逸、symlink 逃逸都会被拒绝。
- 没有 `allowed_tools` 的 Skill 不默认获得 Bash。

### 4.2 建立 PermissionGate

Hermes 的危险命令系统集中在 `tools/approval.py`。EvoPaw 应实现一个更小但同样集中的版本：

```text
PermissionGate.check(
    actor="main_agent" | "sub_agent" | "cron" | "skill_script",
    session_id=...,
    routing_key=...,
    sender_id=...,
    skill_name=...,
    tool_name=...,
    args=...,
) -> PermissionDecision
```

决策结果：

| action | 含义 |
|---|---|
| `allow` | 直接执行 |
| `deny` | 返回明确 BLOCKED，不重试原危险动作 |
| `ask` | 需要用户审批；Feishu 场景发审批卡片并阻塞/挂起 |

P0 只需覆盖：

- Bash 命令危险模式。
- 文件写入/删除路径边界。
- Feishu 发送、群发、上传等外部副作用。
- Cron 无人值守场景：默认 deny，除非 job policy 明确允许。

审计日志建议：

```json
{
  "ts": "2026-04-24T00:00:00Z",
  "session_id": "s-...",
  "routing_key": "p2p:...",
  "sender_id": "ou_...",
  "actor": "sub_agent",
  "skill_name": "scheduler_mgr",
  "tool_name": "Bash",
  "action": "ask",
  "reason": "recursive delete",
  "approved_by": null
}
```

### 4.3 上下文文件扫描

Hermes 在 `prompt_builder.py`、`memory_tool.py`、`skills_guard.py` 中都对会进入 prompt 的内容做注入扫描。EvoPaw 需要把扫描前置到所有 prompt 输入：

- `soul.md`
- `user.md`
- `agent.md`
- `memory.md`
- `AGENTS.md`
- `CLAUDE.md`
- Skill `SKILL.md`
- session snapshot
- retrieved memory/search snippets

P0 扫描规则不必复杂，先覆盖：

- “ignore previous instructions” 类覆盖指令。
- hidden HTML/comment 指令。
- 零宽字符、bidi override。
- 读取/外发 `.env`、token、secret 的指令。
- `curl | sh`、`wget | sh`、远端脚本执行。

发现高危内容时，应注入安全占位而不是原文。

### 4.4 Tool Result Budget

Hermes 的 registry 支持 `max_result_size_chars`，session_search 也限制 top-N、max chars、并发。EvoPaw 当前 `skill_loader` 返回 Sub-Agent 输出没有统一预算。

P0 要求：

- 每个 Skill 增加 `output_budget_chars`，默认 12000。
- 超限结果写入 `outputs/skill-result-*.md`，返回摘要和文件路径。
- `history_reader`、`search_memory`、未来 `session_search` 都必须分页/限额。
- 错误返回必须是结构化 JSON 或固定 XML envelope，不能把 traceback 无限塞回主模型。

建议统一结果格式：

```xml
<skill_result name="pdf" status="ok" truncated="false">
  <summary>...</summary>
  <artifacts>
    <file>/workspace/sessions/s-xxx/outputs/result.md</file>
  </artifacts>
</skill_result>
```

### 4.5 `ctx.json` 改为有 schema 的 session snapshot

当前 `ctx.json` 是 list，语义容易滑向“长期记忆”。P0 应先改结构，不一定马上迁移所有存量。

目标结构：

```json
{
  "schema_version": 1,
  "session_id": "s-xxx",
  "captured_at": "2026-04-24T00:00:00Z",
  "model": "claude-sonnet-4-6",
  "model_ctx_limit": 200000,
  "compression_count": 1,
  "source_range": {
    "from_raw_offset": 0,
    "to_raw_offset": 128
  },
  "messages": [
    {"role": "system", "content": "<context_summary>...</context_summary>"}
  ]
}
```

原则：

- snapshot 只解决 session continuity。
- durable preference/fact 写入 `memory_service`。
- `raw.jsonl` 或后续 SQLite 是可恢复原始历史的来源。
- model context limit 从配置/模型元数据来，不再硬编码 32000。

---

## 5. P0：Memory 与 Session Search

### 5.1 新增 `memory_service`

Hermes 的 built-in memory 有几个关键点值得直接借鉴：

- 两个目标：`memory` 和 `user`。
- 操作是 `add/replace/remove/read`，不是让模型自由编辑文件。
- entries 有字符预算。
- 写入会立即落盘，但本轮 system prompt snapshot 不变。
- 写入前做 prompt injection/exfiltration 扫描。

EvoPaw 建议新增：

```text
evopaw/memory/
├── service.py
├── scanner.py
├── quota.py
├── store.py
└── providers/
    ├── local.py
    └── base.py
```

`memory-save` 的角色调整为策略 Skill：

- 判断该不该记。
- 生成 compact declarative facts。
- 调用 `memory_service` 写入。

它不应继续成为“随意写文件”的主通道。

### 5.2 热记忆写入规则

| 信息类型 | 写入位置 | 示例 |
|---|---|---|
| 用户偏好/纠正 | `user.md` | “用户偏好中文回复，代码标识符保持英文。” |
| 稳定环境事实 | `memory.md` | “EvoPaw 使用 Claude Agent SDK，主工具入口是 skill_loader。” |
| 任务进度 | 不写 hot memory | 用 session_search 回忆 |
| 可复用流程 | Skill | “如何生成港股晨报” |
| 大段资料 | topic/cold file | 报告、会议纪要、项目背景长文 |

写入必须是 declarative fact，不是 imperative instruction。
例如：

- 推荐：“用户偏好最终答复先给结论再给验证命令。”
- 避免：“以后所有回答都必须先给结论。”

### 5.3 新增 `session_search`

Hermes 的 `session_search` 路径是：FTS5 搜索消息 -> 按 session 聚合 -> 截取命中附近上下文 -> 辅助模型摘要。

EvoPaw 建议新增：

```text
evopaw/session_store/
├── db.py
├── writer.py
├── search.py
└── summarize.py

evopaw/skills/session_search/
└── SKILL.md
```

SQLite schema 先做 MVP：

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  routing_key TEXT NOT NULL,
  source TEXT NOT NULL,
  parent_session_id TEXT,
  model TEXT,
  started_at REAL NOT NULL,
  ended_at REAL,
  message_count INTEGER DEFAULT 0,
  tool_call_count INTEGER DEFAULT 0
);

CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT,
  ts REAL NOT NULL,
  tool_name TEXT,
  metadata TEXT
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
  content,
  content=messages,
  content_rowid=id
);
```

保留现有 JSONL 作为 append-only audit，短期采用 dual-write，稳定后再让 SQLite 成为主读路径。

### 5.4 pgvector 的职责收窄

pgvector 不应删除，但职责要明确：

- 语义相似召回。
- 相似任务聚类。
- learning loop 样本挖掘。
- 技能候选发现。

精确历史问题优先走 FTS5：

- “上次那个错误码是什么？”
- “之前提到的文件路径？”
- “上周那个命令怎么写？”

语义模糊问题再走 pgvector：

- “我们之前做过类似的 Feishu 附件处理吗？”
- “有没有类似的投资报告工作流？”

---

## 6. P0/P1：Prompt Builder 与 ContextEngine

### 6.1 抽出 Prompt Builder

当前 `main_agent.py` 同时负责 bootstrap prompt、tool constraints、ctx summary、history、image block、SkillLoader server、Claude SDK options 和 persistence。建议新增：

```text
evopaw/agent_runtime/
├── prompt_builder.py
├── turn_overlay.py
└── platform_hints.py
```

接口：

```python
def build_stable_system_prompt(
    *,
    workspace_dir: Path,
    skills_index: str,
    platform: str,
    model_family: str,
) -> str: ...

def build_turn_overlay(
    *,
    session_snapshot: dict | None,
    recent_history: list[MessageEntry],
    retrieved_context: list[dict],
    user_message: str,
) -> str: ...
```

分层建议：

1. Identity：`soul.md`
2. Agent rules：`agent.md`
3. Hot memory snapshot：`user.md` + `memory.md`
4. Skills index：只包含可见 Skill 的紧凑 metadata
5. Project context：`AGENTS.md` / `CLAUDE.md`
6. Platform hint：Feishu/TestAPI/Cron
7. Tool-use constraints
8. Turn overlay：session snapshot、recent history、retrieved context、本轮输入

### 6.2 稳定层与临时层分离

Hermes 的重要经验是：system prompt 尽量稳定，memory mid-session 写入不改变当前 snapshot。这有两个收益：

- 降低 prompt cache 失效。
- 避免“刚写入的记忆立即变成更高优先级指令”。

EvoPaw 可先这样落地：

- 每个 Runner worker/session 启动时生成 `stable_system_prompt`。
- 本轮 `ctx_summary/recent_history/retrieved_context/user_message` 放入 user prompt overlay。
- `memory_service.add()` 的结果只在工具响应中可见，下一轮再进 stable snapshot。

如果 Claude Agent SDK 的调用模型不利于跨轮缓存，也仍应保持概念分离，方便未来多 provider。

### 6.3 ContextEngine 抽象

`context_mgmt.py` 当前有剪枝、分块、压缩三类函数。建议升级为：

```text
evopaw/context/
├── engine.py
├── compressor.py
├── budget.py
├── retrieval.py
└── subdirectory_hints.py
```

接口：

```python
class ContextEngine:
    def update_from_response(self, usage: dict) -> None: ...
    def should_compress(self, messages: list[dict]) -> bool: ...
    def compress(self, messages: list[dict], focus_topic: str | None = None) -> list[dict]: ...
    def retrieve(self, query: str, session_id: str) -> list[dict]: ...
```

P1 才需要完整 token-aware；P0 可以先把硬编码参数移到 config：

- `context.model_ctx_limit`
- `context.compress_threshold`
- `context.protect_first_n`
- `context.protect_last_n`
- `context.chunk_tokens`

### 6.4 子目录上下文发现

Hermes 的 `SubdirectoryHintTracker` 不改 system prompt，而是在工具结果中追加局部上下文。EvoPaw 可做轻量版本：

- 当 Main/Sub-Agent 访问某路径，检查该路径及最多 5 级父目录。
- 识别 `AGENTS.md`、`CLAUDE.md`、`.cursorrules`。
- 经过 context scanner。
- 限制每文件 8000 字符。
- 只注入一次，缓存已加载目录。

这对 EvoPaw 处理用户上传项目、复杂仓库文档、Skill 脚本调试都有价值。

---

## 7. P1：Skill 供应链与 Tool Registry

### 7.1 Skill metadata 收敛到 SKILL.md frontmatter

`load_skills.yaml` 应只承担 enable/order/source manifest，不再是 Skill 类型和路径的唯一来源。

建议 frontmatter：

```yaml
---
name: scheduler_mgr
description: 管理 EvoPaw 定时任务，支持 at/every/cron。
type: task
version: "1.1.0"
category: productivity
execution_mode: isolated
source:
  kind: bundled
  uri: ""
trust: internal
status: enabled
allowed_tools:
  - Read
  - Write
  - Bash
allowed_paths:
  read:
    - /workspace/sessions/{session_id}
    - /mnt/skills/scheduler_mgr
  write:
    - /workspace/sessions/{session_id}/outputs
    - /workspace/sessions/{session_id}/tmp
output_budget_chars: 12000
needs_context: false
safety:
  side_effects: true
  requires_approval_for:
    - write_cron
    - delete_cron
---
```

Registry 构建规则：

- `SKILL.md` frontmatter 是 metadata source of truth。
- `load_skills.yaml` 只决定哪些 bundled/local skill 启用和排序。
- manifest 中的 `path` 必须被尊重；若缺失则默认为 `skills/{name}`。
- 同名 Skill 冲突时：local/generated > bundled > external，但必须审计并记录 shadow。

### 7.2 `skill_manage` 服务化

EvoPaw 的 `skill-creator` 已有创建和评测资产，但缺少运行时管理服务。建议新增：

```text
evopaw/skills_runtime/
├── registry.py
├── manager.py
├── guard.py
├── lifecycle.py
├── audit.py
└── sources.py
```

支持操作：

- `create`
- `patch`
- `edit`
- `delete`
- `write_file`
- `remove_file`
- `promote`
- `rollback`

Hermes 的经验是：`patch` 应是默认修复路径，`edit` 只用于大改。EvoPaw 也应避免每次修 Skill 都全量重写 `SKILL.md`。

### 7.3 候选区与生命周期

目录结构：

```text
data/skills/
├── active/
├── quarantine/
├── canary/
├── archived/
└── audit.jsonl
```

生命周期：

| 状态 | 可见性 | 进入条件 |
|---|---|---|
| `draft` | 不可见 | 反思/用户请求生成 |
| `quarantine` | 不可见 | 已落盘，等待扫描/评测 |
| `canary` | 部分 routing_key 可见 | 扫描通过，评测通过 |
| `enabled` | 全局可见 | 人工或策略批准 |
| `deprecated` | 可见但不优先 | 有替代方案 |
| `retired` | 不可见 | 归档 |

### 7.4 Skill Guard

在支持 external skill dirs、团队共享目录或自动生成 Skill 前，必须先做 Skill Guard：

- 扫描 prompt injection。
- 扫描环境变量/secret 外发。
- 扫描远端下载执行。
- 扫描 path traversal、symlink escape。
- 扫描二进制/大文件/过多文件。
- 按 source/trust/verdict 决策。

没有 Guard 之前，不建议做公开 Skill Hub。

### 7.5 内部 Tool Registry

即使 Main Agent 仍只暴露 `skill_loader`，Skill Runtime 内部也应借鉴 Hermes 的 registry：

```text
evopaw/runtime/
├── tool_registry.py
├── toolsets.py
├── result_budget.py
└── dispatch.py
```

每个工具条目包含：

- name
- toolset
- schema
- handler
- check_fn
- required_secrets
- max_result_size_chars
- side_effect_level

这能解决：

- Skill 脚本能力难以审计。
- Sub-Agent allowed_tools 过粗。
- Feishu、文件、搜索、记忆、cron 等副作用没有统一入口。

---

## 8. P1：Cron 任务运行时

### 8.1 从消息回灌升级为 Agent Task

当前 CronService：

```text
cron tick -> InboundMessage(is_cron=True) -> Runner.dispatch()
```

目标：

```text
cron tick
  -> create fresh task session
  -> load job prompt + attached skills + tool policy
  -> run isolated agent turn
  -> persist result and audit
  -> deliver to target
```

建议新增：

```text
evopaw/cron/
├── agent_runner.py
├── policy.py
├── delivery.py
└── store.py
```

### 8.2 任务定义升级

建议 `tasks.json` 升级为：

```json
{
  "id": "job-001",
  "name": "daily-summary",
  "enabled": true,
  "schedule": {
    "kind": "cron",
    "expr": "0 9 * * 1-5",
    "tz": "Asia/Shanghai"
  },
  "task": {
    "prompt": "请生成昨天工作摘要并发给我",
    "fresh_session": true,
    "skills": ["daily-summary", "session_search"],
    "tool_policy": "cron_restricted",
    "timeout_seconds": 600,
    "no_recursive_schedule": true
  },
  "delivery": {
    "kind": "origin",
    "routing_key": "p2p:ou_xxx"
  },
  "state": {
    "next_run_at_ms": 0,
    "last_run_at_ms": null,
    "last_status": null,
    "last_error": null
  }
}
```

### 8.3 cron 默认安全策略

Hermes cron 对无人值守审批有明确处理。EvoPaw 应默认：

- 无用户在线审批时，危险命令 deny。
- cron session 禁用 `scheduler_mgr` 的创建/修改 cron 能力，防止递归调度。
- cron tool policy 默认只允许必要 Skills。
- cron 输出始终落审计日志，即使选择不发送。
- 空响应不标记为成功。

---

## 9. P1/P2：Learning Loop

### 9.1 不要先做全自动自进化

Hermes 的闭环学习建立在 memory、session_search、skill_manage、skills_guard、state.db 之上。EvoPaw 应按下面顺序接入：

1. 反思日志：记录“可能值得记忆/技能化”的候选，不自动改资产。
2. Candidate Store：候选 memory、candidate skill、skill patch 分开保存。
3. 离线评测：复用 `skill-creator` 的 `run_eval.py` / `run_loop.py`。
4. Quarantine：评测通过后仍不直接启用。
5. Canary：只对指定 routing_key 或 test profile 可见。
6. Promote：人工确认或稳定策略后启用。

### 9.2 Learning 子系统

```text
evopaw/learning/
├── reflection.py
├── triggers.py
├── candidate_store.py
├── evaluator.py
├── promotion.py
├── telemetry.py
└── reports.py
```

触发条件建议：

- 单任务发生 5 次以上有效工具调用且最终成功。
- 用户纠正过流程。
- 7 天内重复出现同类任务。
- 某 Skill 连续失败后用户/模型找到替代路径。
- session_search 多次召回同一流程。

### 9.3 候选类型

| 候选 | 来源 | 处理 |
|---|---|---|
| memory candidate | 用户偏好、稳定事实 | 进入 memory review，可自动建议，不直接写入 |
| skill candidate | 成功复杂流程 | 进入 quarantine，跑 eval |
| skill patch | 使用中发现缺口 | patch diff，跑 targeted eval |
| prompt rule candidate | 重复系统性错误 | 只进入人工 review，不自动改 `agent.md` |
| test/benchmark candidate | 失败样本 | 加入 skill-creator benchmark |

### 9.4 必须记录 telemetry

没有 telemetry，就无法判断 Skill 是否真的变好。

建议记录：

- skill_invocation_id
- skill_name/version
- status
- elapsed_ms
- tool_count
- output_truncated
- user_correction_after_use
- retry_count
- produced_artifacts
- approval_events

---

## 10. P2/P3：Provider Runtime、Gateway、Delegation

### 10.1 Provider Runtime

EvoPaw 当前模型配置分散：

- 主 Agent：Claude Sonnet。
- Sub-Agent：Claude Haiku。
- 压缩/embedding：Qwen/OpenAI 兼容。

建议新增：

```text
evopaw/provider_runtime/
├── resolve.py
├── models.py
├── auxiliary.py
├── fallback.py
└── credentials.py
```

短期只支持：

- `anthropic`
- `openai_compatible`
- `dashscope`
- `fallback`

不要追 Hermes 的大量 provider，先把 main/subagent/compression/session_search/eval 的模型路由统一。

### 10.2 Gateway Core

EvoPaw 不需要马上做 Telegram/Discord/Slack，但需要降低 Feishu 耦合：

```text
evopaw/gateway/
├── events.py
├── core.py
├── delivery.py
├── session_keys.py
├── approvals.py
└── adapters/
    ├── feishu.py
    └── test_api.py
```

这样可以把 Feishu listener/sender、TestAPI、cron delivery、approval cards、verbose/progress events 统一到 gateway event/delivery 抽象。

### 10.3 Delegation 与 Workflow Runtime

Hermes 的 delegation/code execution 很强，但 EvoPaw 不应在 P0/P1 做通用并行代理。

先满足：

- task Skill 的 Sub-Agent 有明确输入输出和权限。
- session_search/memory/tool registry 已稳定。
- SkillResult envelope 已稳定。

之后再考虑：

```text
delegate_task(goal, context, tool_policy, max_parallel=3)
```

以及低上下文成本 workflow runtime：

```text
evopaw/workflow_runtime/
├── executor.py
├── rpc_bridge.py
├── tools.py
└── policies.py
```

适用场景：

- 搜索 -> 抓取 -> 过滤 -> 汇总。
- 表格/PDF 批处理。
- 投研报告流水线。
- cron 批任务。

---

## 11. 重新排序后的实施路线图

### Phase 0：运行时底线

目标：让系统具备长期运行的最低安全和恢复能力。

- 修正 Sub-Agent cwd 为 session 目录。
- Skill metadata 增加 `allowed_tools/output_budget/status/trust/source`。
- 移除生产默认 `bypassPermissions`，引入 PermissionGate。
- 增加危险命令、路径、context 文件扫描。
- 建立 `memory_service`，让 `memory-save` 走服务写入。
- 抽出 `prompt_builder.py`，分离 stable prompt 与 turn overlay。
- `ctx.json` 改为 schema 化 session snapshot。
- 建立 `state.db + FTS5 session_search` MVP。
- Cron 改为 fresh session，并禁止递归创建 cron。

### Phase 1：运行时平台化

目标：把能力从“散落实现”变成可治理运行时。

- Skill registry 尊重 frontmatter 与 manifest，消除双源漂移。
- 建立 Skill lifecycle/quarantine/audit。
- 增加内部 ToolRegistry/toolsets/result budget。
- ContextEngine 替代硬编码 `context_mgmt.py` 策略。
- 子目录上下文发现。
- CronAgentRunner 支持 attached skills/tool policy/delivery。
- SQLite 记录 tool_calls、skill_events、memory_events、approval_events。

### Phase 2：学习闭环

目标：让 EvoPaw 能在受控流程中沉淀经验。

- 建立 reflection triggers。
- CandidateStore 保存 memory/skill/patch/prompt-rule 候选。
- 对接 `skill-creator` eval/benchmark。
- canary/promote/rollback。
- `/insights` 或管理报表展示 Skill 成功率、用户纠正、成本和延迟。

### Phase 3：高级扩展

目标：按需求扩展，而不是提前复杂化。

- ProviderRuntime + fallback/auxiliary model routing。
- Gateway Core + Feishu/TestAPI adapters。
- profile/workspace isolation。
- external skill dirs/team skill repo。
- generic delegation。
- workflow/code execution runtime。
- remote memory provider。

---

## 12. 关键文件变更清单

### 12.1 P0 必改

| 文件 | 动作 |
|---|---|
| `evopaw/tools/skill_loader.py` | 修正 Sub-Agent cwd；读取更多 frontmatter；加 output budget；尊重 `path` |
| `evopaw/llm/claude_client.py` | 参数化 permission_mode/allowed_tools；去掉生产默认 bypass |
| `evopaw/agents/main_agent.py` | 抽离 prompt/session/context 逻辑，调用 `prompt_builder` |
| `evopaw/memory/bootstrap.py` | 接入 context scanner；热记忆 snapshot 化 |
| `evopaw/memory/context_mgmt.py` | 改为 schema snapshot；移除硬编码 model ctx limit |
| `evopaw/cron/service.py` | fresh session、no-recursive-schedule、cron policy |
| `evopaw/skills/load_skills.yaml` | 降级为 manifest，保留 enable/order/source |

### 12.2 P0/P1 新增

| 文件/目录 | 责任 |
|---|---|
| `evopaw/security/permission_gate.py` | ALLOW/DENY/ASK 决策 |
| `evopaw/security/scanner.py` | context/memory/skill 注入与 exfil 扫描 |
| `evopaw/memory/service.py` | bounded memory add/replace/remove/read |
| `evopaw/session_store/db.py` | SQLite/WAL/FTS5 |
| `evopaw/session_store/search.py` | session_search |
| `evopaw/agent_runtime/prompt_builder.py` | stable prompt assembly |
| `evopaw/context/engine.py` | ContextEngine 抽象 |
| `evopaw/skills_runtime/registry.py` | Skill metadata/source/lifecycle |
| `evopaw/runtime/tool_registry.py` | Skill 内部工具治理 |
| `evopaw/cron/agent_runner.py` | first-class cron task runner |

### 12.3 P2/P3 新增

| 文件/目录 | 责任 |
|---|---|
| `evopaw/learning/` | reflection/candidates/eval/promotion |
| `evopaw/provider_runtime/` | provider/fallback/auxiliary model routing |
| `evopaw/gateway/` | adapter/delivery/approval/progress abstraction |
| `evopaw/workflow_runtime/` | 低上下文成本工作流执行 |

---

## 13. 不建议照搬的 Hermes 能力

### 13.1 不复制大一体式主循环

Hermes 的主循环很成熟，但文件体量和责任集中度不适合 EvoPaw 当前架构。EvoPaw 应学习它的分工，不复制它的组织形式。

### 13.2 不急着做全量多平台

EvoPaw 当前价值在 Feishu 工作助手。先把 Feishu 场景里的权限、记忆、会话搜索、cron、Skill 生命周期做好，再抽象 gateway。

### 13.3 不急着接远端用户建模

Honcho 类 provider 很有启发，但会带来隐私、合规、网络稳定性和数据治理问题。先做好 local provider interface。

### 13.4 不急着做公开 Skill Hub

没有 Skill Guard、trust、quarantine、audit、rollback 前，公开安装第三方 Skill 风险大于收益。

### 13.5 不把 pgvector 当唯一记忆

embedding search 不是 session search。精确历史召回、命令、路径、错误码必须有 FTS5/SQLite 支撑。

---

## 14. 更新后的十个优先动作

1. 修正 task Skill 的 Sub-Agent cwd，不再把 `/workspace` 作为默认执行目录。
2. 为 Sub-Agent 引入 `allowed_tools` metadata，移除默认全量 Bash/Read/Write/Edit/Grep/Glob。
3. 增加 PermissionGate，覆盖 Bash、文件写入、Feishu 副作用、cron 无人审批。
4. 抽出 `prompt_builder.py`，把 stable system prompt 与 turn overlay 分离。
5. 建立 bounded `memory_service`，让 `memory-save` 只做策略判断。
6. 将 `ctx.json` schema 化，明确它是 session snapshot，不是 durable memory。
7. 建立 SQLite/WAL/FTS5 `session_search`，JSONL 保留为审计副本。
8. Skill frontmatter 增加 source/trust/status/allowed_tools/output_budget，并让 registry 尊重 `path`。
9. Cron 改为 fresh task session，禁用递归调度，绑定 tool policy。
10. 把 `skill-creator` 评测资产接入 CandidateStore，但候选 Skill 默认进 quarantine。

---

## 15. 参考资料

Hermes Agent 上游：

- [README.md](https://github.com/NousResearch/hermes-agent/blob/main/README.md)
- [run_agent.py](https://github.com/NousResearch/hermes-agent/blob/main/run_agent.py)
- [agent/prompt_builder.py](https://github.com/NousResearch/hermes-agent/blob/main/agent/prompt_builder.py)
- [agent/context_engine.py](https://github.com/NousResearch/hermes-agent/blob/main/agent/context_engine.py)
- [agent/subdirectory_hints.py](https://github.com/NousResearch/hermes-agent/blob/main/agent/subdirectory_hints.py)
- [agent/memory_manager.py](https://github.com/NousResearch/hermes-agent/blob/main/agent/memory_manager.py)
- [agent/memory_provider.py](https://github.com/NousResearch/hermes-agent/blob/main/agent/memory_provider.py)
- [tools/memory_tool.py](https://github.com/NousResearch/hermes-agent/blob/main/tools/memory_tool.py)
- [tools/session_search_tool.py](https://github.com/NousResearch/hermes-agent/blob/main/tools/session_search_tool.py)
- [tools/skill_manager_tool.py](https://github.com/NousResearch/hermes-agent/blob/main/tools/skill_manager_tool.py)
- [tools/skills_guard.py](https://github.com/NousResearch/hermes-agent/blob/main/tools/skills_guard.py)
- [tools/registry.py](https://github.com/NousResearch/hermes-agent/blob/main/tools/registry.py)
- [tools/approval.py](https://github.com/NousResearch/hermes-agent/blob/main/tools/approval.py)
- [tools/path_security.py](https://github.com/NousResearch/hermes-agent/blob/main/tools/path_security.py)
- [hermes_state.py](https://github.com/NousResearch/hermes-agent/blob/main/hermes_state.py)
- [cron/scheduler.py](https://github.com/NousResearch/hermes-agent/blob/main/cron/scheduler.py)
- [hermes_cli/runtime_provider.py](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/runtime_provider.py)
- [website/docs/developer-guide/agent-loop.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/agent-loop.md)

EvoPaw 对照文件：

- [harness-improvement-plan.md](./harness-improvement-plan.md)
- `evopaw/agents/main_agent.py`
- `evopaw/tools/skill_loader.py`
- `evopaw/llm/claude_client.py`
- `evopaw/memory/bootstrap.py`
- `evopaw/memory/context_mgmt.py`
- `evopaw/memory/indexer.py`
- `evopaw/session/manager.py`
- `evopaw/cron/service.py`
- `evopaw/skills/load_skills.yaml`
- `evopaw/skills/skill-creator/`

---

## 16. 最终判断

Hermes Agent 对 EvoPaw 的真正启发是：Agent Runtime 的长期能力来自运行时纪律，而不是功能数量。

EvoPaw 当前已经有不错的 Feishu 接入、SkillLoader、Sub-Agent、记忆雏形和评测资产。下一步最重要的不是继续堆 Skill，而是先把下面三组基础设施做实：

1. **安全边界**：PermissionGate、路径隔离、context/skill 扫描、cron 无人审批策略。
2. **记忆与上下文**：bounded hot memory、schema session snapshot、FTS5 session_search、ContextEngine。
3. **Skill 资产治理**：frontmatter source of truth、lifecycle/quarantine、tool policy、eval/canary/promote。

这些落地后，EvoPaw 才具备安全地接入学习闭环、provider runtime、gateway core 和更强 delegation 的工程基础。
