# EvoPaw Agent Harness 改进方案

> 基于 [Agentic Harness Patterns](https://github.com/keli-wen/agentic-harness-patterns-skill) 的六层模式，并结合 EvoPaw 当前代码结构，重新校准改进路线图。
>
> 本版重点不是增加更多能力清单，而是把上游项目真正强调的运行时不变量落到 EvoPaw：有界上下文、单一权限门控、Skill 元数据单一真相源、topic-first 记忆写入、可审计生命周期。

## 0. 本次优化结论

上一版方向基本正确，但几个实现顺序需要调整：

1. **P0 先做运行时防线，而不是先堆功能。** 当前 `Main Agent` 和 `Sub-Agent` 都使用 `permission_mode="bypassPermissions"`，且 `Sub-Agent` 的 `cwd` 是 `/workspace`。这比 `memory-save` 缺脚本更急。
2. **`memory-save` 不应先写扁平 `memory.md`。** 上游模式明确要求 topic-first、index-second。EvoPaw 已经把 `memory.md` 设计成索引，所以 P0 版本也要直接按索引写入，不能先做一个未来必删的扁平实现。
3. **`ASK` 不能简化为 allow + audit。** 在飞书这种 headless 场景里，`ASK` 要么转成一次性确认流程，要么在不能确认时转成 `DENY`。放行再审计违反 fail-closed。
4. **Skill 元数据必须回到 `SKILL.md` frontmatter。** `load_skills.yaml` 只保留来源、启停和迁移期兼容信息；`name/description/type/triggers/allowed_tools/execution_mode` 由 `SKILL.md` 做单一真相源。
5. **多 Agent 短期保持 Coordinator，不急着加 Fork。** EvoPaw 当前 task Skill 本质上是短生命周期 worker。先补“结果信封 + Main Agent 综合 + 工具过滤”，再考虑 Fork/Swarm。
6. **Hook 扩展前先做单一分发点和信任门。** 否则新增 Hook 只会把安全和生命周期副作用分散到更多调用点。

参考的上游关键文件：

- [agentic-harness-patterns-zh/SKILL.md](https://github.com/keli-wen/agentic-harness-patterns-skill/blob/master/skills/agentic-harness-patterns-zh/SKILL.md)
- [memory-persistence-pattern.md](https://github.com/keli-wen/agentic-harness-patterns-skill/blob/master/skills/agentic-harness-patterns-zh/references/memory-persistence-pattern.md)
- [skill-runtime-pattern.md](https://github.com/keli-wen/agentic-harness-patterns-skill/blob/master/skills/agentic-harness-patterns-zh/references/skill-runtime-pattern.md)
- [tool-registry-pattern.md](https://github.com/keli-wen/agentic-harness-patterns-skill/blob/master/skills/agentic-harness-patterns-zh/references/tool-registry-pattern.md)
- [permission-gate-pattern.md](https://github.com/keli-wen/agentic-harness-patterns-skill/blob/master/skills/agentic-harness-patterns-zh/references/permission-gate-pattern.md)
- [agent-orchestration-pattern.md](https://github.com/keli-wen/agentic-harness-patterns-skill/blob/master/skills/agentic-harness-patterns-zh/references/agent-orchestration-pattern.md)
- [hook-lifecycle-pattern.md](https://github.com/keli-wen/agentic-harness-patterns-skill/blob/master/skills/agentic-harness-patterns-zh/references/hook-lifecycle-pattern.md)

---

## 1. Harness 六层重新对齐

| Harness 层 | 上游不变量 | EvoPaw 现状 | 本版调整 |
|---|---|---|---|
| Memory | instruction / auto / session extraction 分离；auto memory topic-first | `soul/user/agent/memory.md` + `ctx.json` + pgvector，但 auto memory 写入缺闭环 | `memory.md` 保持索引，新增 `memory/topics/*.md`；`ctx.json` 明确只做会话快照 |
| Skills | 元数据与正文共置；发现列表有预算；默认内联，隔离显式声明 | `load_skills.yaml` 承载 type；`SKILL.md` 只读 description；task 全部 fork | `SKILL.md` frontmatter 成为单一真相源；`execution_mode` 显式化；发现预算和降级 |
| Tools & Safety | 单一权限门控；默认 fail-closed；安全分类按调用输入评估 | `bypassPermissions`，Sub-Agent 固定全工具，`cwd=/workspace` | 引入 `PermissionGate`、工具过滤、受保护路径、确认流程；缩小 Sub-Agent cwd |
| Context | select/write/compress/isolate；每个变长块有硬上限和恢复指针 | 有压缩工具，但输出无上限；截断提示不够可操作；模型窗口硬编码 | `ContextEngine`、输出上限、快照 schema、精确恢复指针、配置化窗口 |
| Multi-agent | Coordinator 必须综合；模式互斥；worker 工具最小化 | Main -> Sub-Agent 零继承，但结果常被原样转发 | `SkillResult` 结果信封、综合提示、可选最小上下文注入、暂缓 Fork |
| Lifecycle | Hook 单一分发；信任全有或全无；后台任务有状态机 | 只有 verbose hooks；后台任务多为 `create_task` | `HookDispatcher`、`BackgroundTaskStore`、Session/Cron 一等任务 |

---

## 2. 需要先纠偏的现状问题

### 2.1 Skill 注册表存在元数据双源和 path 失效

`evopaw/tools/skill_loader.py` 当前从 `load_skills.yaml` 读取 `name/type/enabled`，再读取 `SKILL.md` 的 description。这违反了上游“元数据必须和 Skill 正文共置”的原则。更具体地说，`load_skills.yaml` 里已有部分条目写了 `path`，但 `_build_skill_registry()` 实际只用 `name` 拼路径，`path` 字段没有生效。

调整方向：

- `SKILL.md` frontmatter 负责能力语义和运行时声明。
- `load_skills.yaml` 只负责启停、source 根路径和迁移期 override。
- 注册时必须使用 `resolve()`/realpath 去重，并显式支持 `path`。

### 2.2 Sub-Agent 当前隔离边界太宽

`skill_loader.py` 调用 `run_skill_agent(..., session_path="/workspace")`，而不是 `/workspace/sessions/{session_id}`。虽然 prompt 里说明了输出目录，但实际 `cwd` 覆盖整个 workspace，配合 `Bash/Read/Write/Edit/Grep/Glob` 和 `bypassPermissions`，权限面过大。

P0 必须先改为：

- Sub-Agent `cwd=/workspace/sessions/{session_id}`。
- Skill 资源通过只读挂载或固定路径传入。
- `allowed_tools` 按 Skill frontmatter 收窄，缺省不给 `Bash`。
- 受保护路径如 `.config/`、凭证、`memory/`、`data/cron/`、`workspace-init/` 必须进入权限门控。

### 2.3 `memory-save` 缺执行脚本，但不能用临时扁平方案补

上游记忆模式强调：

- index 是常驻上下文，有硬上限。
- topic 文件是按需加载的详情。
- 每次写入先写 topic，再更新 index。

因此 `memory-save` 的 P0 目标不是“向 `memory.md` 追加一行长文本”，而是实现最小可用的 topic-first writer。

### 2.4 `ASK` 语义不能在飞书里被吞掉

上一版把 `PermissionDecision.ASK` 简化成 “allow + audit log”。这会把最危险的分支变成静默放行。

在 EvoPaw 中应定义两种运行模式：

- `interactive_feishu`：发送确认卡片，用户确认后持久化一次性授权 token，再继续执行。
- `headless` / `cron`：`ASK` 转为 `DENY`，返回可解释原因。

### 2.5 后台 session 提取不应只挂在 worker idle timeout

把 session extraction 放在 `Runner._worker()` 空闲退出分支会带来两个问题：

- 用户回复频繁时长期不触发。
- 进程关闭时可能没机会执行。

更稳妥的触发点是 Agent 产出最终回复并持久化历史之后，进入一个可合并的后台队列，并在 `shutdown()` 时 drain。

---

## 3. 改进一：记忆系统升级

### 3.1 目标架构

将 EvoPaw 的记忆明确拆成四类，不再让 `ctx.json`、`memory.md` 和 pgvector 混用：

| 层 | 文件/存储 | 角色 | 是否进 system prompt |
|---|---|---|---|
| Instruction Memory | `soul.md` / `user.md` / `agent.md` | 人类维护的稳定规则和身份 | 是，完整注入 |
| Auto Memory Index | `memory.md` | Agent 学到的长期记忆索引，200 行/字节上限 | 是，仅索引 |
| Auto Memory Topics | `memory/topics/*.md` | 记忆详情，带 frontmatter | 否，按需读取 |
| Session Memory | `data/ctx/*.json` / `data/sessions/*.jsonl` / pgvector | 会话恢复、审计、检索索引 | 只注入摘要或搜索结果 |

### 3.2 P0：实现 topic-first 的 `memory-save`

新建 `evopaw/skills/memory-save/scripts/save.py`，但接口不应只接收一段自由文本，而应接收结构化输入：

```json
{
  "type": "user|feedback|project|reference",
  "title": "不超过 40 字的标题",
  "summary": "不超过 150 字的一行索引摘要",
  "body": "完整记忆内容",
  "source_session_id": "s-..."
}
```

写入不变量：

1. 校验 `type/title/summary/body`，拒绝空内容和明显可从代码库推导的信息。
2. 生成稳定 slug，准备 `memory/topics/{type}-{slug}.md`。
3. 在 topic 文件写入 YAML frontmatter：`title/type/created_at/updated_at/source_session_id`。
4. 获取 `memory.md` 写锁。
5. 检查 index 行数和字节上限；超限时返回治理建议，不静默截断。
6. 先原子写 topic 文件，再追加 index 行。
7. 如果 index 更新失败，允许 orphan topic 存在，由 `memory-governance` 后续清理。

`memory.md` 索引格式：

```markdown
## user
- [偏好中文沟通](memory/topics/user-prefer-zh.md) - 用户偏好中文解释和执行摘要。

## feedback
- [不要保存可推导架构](memory/topics/feedback-avoid-derivable.md) - 代码可推导的信息不进入长期记忆。
```

### 3.3 P1：记忆治理和上限透明化

`memory-governance` 应先做三个实际可用的维护动作：

- `scan`: 查找死链、orphan topic、超长 index 行、重复摘要。
- `compact`: 将多个相近 topic 合并为一个候选补丁。
- `archive`: 把低价值 topic 移到 `memory/archive/`，并从 index 删除。

治理输出只给建议和 diff，不自动修改共享层，除非用户明确确认。

### 3.4 P2：后台 session extraction

新增 `MemoryExtractionScheduler`，由 `Runner` 在每轮最终回复完成后调用：

```python
await memory_extraction.enqueue(
    session_id=session.id,
    routing_key=key,
    up_to_message_count=session.message_count + 2,
)
```

约束：

- 同一 session 同时只允许一个 extractor 运行；新请求合并到尾随运行。
- 如果本轮主 Agent 已调用 `memory-save`，extractor 跳过该轮并推进游标。
- extractor 只读 `raw.jsonl`，只写 `memory/topics/` 和 `memory.md`，禁止 `Bash`。
- extractor 轮次上限小于普通 Skill，例如 5。
- `Runner.shutdown()` drain 正在运行的提取任务，超时后记录未完成状态。

### 3.5 P3：`/remember` 晋升审计

`/remember` 不应自动写入 `user.md` 或 `agent.md`。它只生成结构化建议：

- 晋升到 `user.md`
- 晋升到 `agent.md`
- 保留在 auto memory
- 合并/删除
- 暂不处理

只有用户确认后才应用 patch。跨层写入必须是人类审批边界。

---

## 4. 改进二：Skills 系统优化

### 4.1 frontmatter 成为单一真相源

目标 frontmatter：

```yaml
---
name: feishu_ops
description: 飞书平台操作工具集
when_to_use: >
  当用户要求发送飞书消息、读写飞书云文档、创建日程、读取表格或操作多维表格时使用。
type: task
execution_mode: isolated
trust_level: bundled
allowed_tools:
  - Read
  - Bash
needs_context: false
output_budget_chars: 12000
safety:
  max_level: execute
  protected_paths: [".config", "memory", "data/cron"]
version: "1.0"
---
```

字段语义：

- `description`：短标签，进入发现列表。
- `when_to_use`：触发提示，和 description 拼接后进入发现列表，长度受限。
- `type`：迁移期保留，兼容 `reference/task`。
- `execution_mode`：`inline|isolated`，最终替代 `type` 的执行语义。
- `allowed_tools`：Sub-Agent 的最小工具集。
- `needs_context`：是否允许注入最小对话摘要。
- `output_budget_chars`：单 Skill 输出上限。
- `safety`：权限门控输入，不直接等同于自动授权。

### 4.2 `load_skills.yaml` 降级为 source manifest

建议格式：

```yaml
sources:
  - id: bundled
    path: evopaw/skills
    trust_level: bundled
    priority: 100
  - id: project
    path: ./skills
    trust_level: project
    priority: 50

skills:
  - name: web_browse
    enabled: true
  - name: experimental-foo
    enabled: false
```

迁移规则：

- `SKILL.md` 不存在则跳过。
- `load_skills.yaml` 中的 `type/path` 仅迁移期兼容，启动时打印 deprecation warning。
- 注册时使用 realpath 去重。相同物理文件只注册一次。
- 同名不同文件冲突时，高 `priority` 胜出，并记录 warning。

### 4.3 发现列表预算和优雅降级

上游建议发现列表控制在上下文窗口约 1%，单条约 250 字符。EvoPaw 应按字符预算实现，而不是只按粗略 token 估算：

- 先拼接 `description + " | " + when_to_use`。
- 每条硬截断到 `skill_entry_max_chars`，默认 250。
- 总预算来自 `context.skill_discovery_budget_chars`，默认 `min(model_ctx_limit * 0.01 * 2, 4000)`。
- 内置 bundled 技能保留完整 entry，但仍受单条上限约束。
- 非内置技能按顺序降级：去掉 `when_to_use`，只保留 `description`，最后只保留 `name`。

### 4.4 输出上限和结果信封

`skill_loader` 返回给 Main Agent 的文本必须有结构化边界：

```xml
<skill_result skill="feishu_ops" status="ok" truncated="false">
  <summary>已创建飞书文档并返回链接。</summary>
  <artifacts>
    <artifact path="/workspace/sessions/s-xxx/outputs/report.md" />
  </artifacts>
  <output>...</output>
</skill_result>
```

超限时：

- 原始完整结果写入 `outputs/{skill_name}-{timestamp}.txt`。
- 返回摘要、文件路径和截断说明。
- Main Agent 收到 `<synthesis_hint>`，必须综合后回复用户，不直接转发长输出。

### 4.5 缺失 Skill 脚本优先级

| Skill | 现状 | 优先级 | 调整 |
|---|---|---|---|
| `memory-save` | 有规范无脚本 | P0 | topic-first writer |
| `memory-governance` | 有规范无脚本 | P1 | scan/compact/archive |
| `web_browse` | 声明为 task | P2 | 若只依赖 Agent 浏览能力，可改 inline/reference；若保留 task，要补脚本和网络权限声明 |
| `daily-summary` | 无脚本 | P2 | 基于 session/raw + 飞书发送生成日报 |
| `investment-review` | 无脚本 | P3 | 依赖投资数据源治理后再做 |

---

## 5. 改进三：工具安全与权限

### 5.1 P0：停止生产默认 bypass

`evopaw/llm/claude_client.py` 应支持配置：

```yaml
security:
  permission_mode: "ask"
  allow_bypass_in_debug: false
  protected_paths:
    - ".config"
    - "memory"
    - "workspace-init"
    - "data/cron"
```

规则：

- 生产默认不使用 `bypassPermissions`。
- debug/test 可以显式开启 bypass，但启动日志必须标红式告警。
- `Sub-Agent` 默认不继承 Main Agent 的工具集。

### 5.2 PermissionGate 三行为

新增 `evopaw/tools/permission_gate.py`：

```python
class PermissionAction(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
```

评估顺序：

1. 显式 deny：禁用 Skill、封禁用户、受保护路径写入。
2. 显式 ask：破坏性命令、群聊高风险操作、凭证/定时任务/记忆写入。
3. 工具特定检查：Bash 命令前缀、路径 ACL、输出位置。
4. 不可绕过安全检查：`.config/`、凭证、Git/配置目录。
5. 模式级 allow：debug、内部白名单、一次性授权 token。
6. 默认：ASK；headless/cron 下 ASK 转 DENY。

需要把 `InboundMessage.sender_id` 传到 `agent_fn` 和 `skill_loader`，否则无法做 actor 级授权和审计。

### 5.3 Sub-Agent 工具最小化

`build_sub_agent_options()` 接收 `allowed_tools`，来自 Skill frontmatter：

```python
def build_sub_agent_options(..., allowed_tools: list[str]):
    return ClaudeAgentOptions(
        allowed_tools=allowed_tools,
        permission_mode=config.security.permission_mode,
        cwd=session_dir,
        ...
    )
```

默认策略：

- `reference/inline`：不创建 Sub-Agent。
- 普通数据处理 task：`Read`, `Write`，必要时加 `Bash`。
- 搜索类 task：只给对应脚本运行能力，不给任意文件写入。
- 记忆提取 task：`Read`, `Write`，不允许 `Bash`。

### 5.4 Bash 不能只按工具类型分类

上游强调并发和安全分类是 per-call 的。对 EvoPaw 而言，`Bash` 的分类必须看命令内容：

| 命令模式 | 决策 |
|---|---|
| `python scripts/read_only.py ...` | 可按 Skill 白名单 ALLOW |
| `python scripts/create_doc.py ...` | 需要外部服务权限和审计 |
| `rm`, `mv`, `chmod`, `git reset`, `curl | sh` | DENY 或 ASK |
| 写 `.config/`、`memory/`、`data/cron/` | ASK；headless 下 DENY |

第一版可以先做保守前缀白名单，宁可多问/多拒绝，不静默放行。

### 5.5 审计日志

每次 Skill 调用和权限决策写入 `data/audit/permission.jsonl`：

```json
{
  "ts": "2026-04-24T12:00:00Z",
  "session_id": "s-...",
  "routing_key": "group:...",
  "sender_id": "ou_...",
  "skill": "feishu_ops",
  "tool": "Bash",
  "action": "ask",
  "reason": "group_write_to_feishu",
  "persisted": false
}
```

---

## 6. 改进四：上下文工程

### 6.1 ContextEngine 抽象

新增 `evopaw/context/engine.py`，把现在散落在 `main_agent.py` 和 `memory/context_mgmt.py` 的拼接逻辑收拢：

```python
class ContextEngine:
    def build_system_prompt(...): ...
    def build_user_prompt(...): ...
    def save_turn(...): ...
    def recover_pointer(...): ...
```

边界：

- `bootstrap.py` 只负责稳定层加载。
- `ContextEngine` 负责选择哪些运行时上下文进入本轮。
- `context_mgmt.py` 保留底层 prune/chunk/compress/save/load 工具。

### 6.2 P0：截断必须带精确恢复指针

`_format_history()` 的提示改为可直接调用：

```text
已省略更早的 42 条消息。
如需读取，请调用 skill_loader:
skill_name="history_reader"
task_context={"page":1,"page_size":20}
```

同时 `history_reader` 返回页码、总页数和下一页调用方式，避免模型猜。

### 6.3 P0：所有变长块加硬上限

| 变长块 | 上限 | 超限处理 |
|---|---:|---|
| Skill discovery list | 配置化，默认约窗口 1% | 降级为 description/name |
| reference Skill 正文 | 默认 12000 chars | 截断并提示按 reference 文件读取 |
| task Skill 输出 | 默认 12000-20000 chars | 完整写入 outputs，返回摘要和路径 |
| tool result / 脚本 stdout | 每工具配置 | stdout/stderr 分开截断 |
| `memory.md` index | 200 行 + 字节上限 | 拒绝写入并提示治理 |
| `ctx.json` | 配置化 | 压缩旧轮次，保留快照标记 |

### 6.4 ctx.json 快照 schema

`ctx.json` 不应再直接存 list。目标格式：

```json
{
  "schema_version": 1,
  "captured_at": "2026-04-24T12:00:00Z",
  "auto_update": false,
  "model": "claude-sonnet-4-6",
  "model_ctx_limit": 200000,
  "messages": []
}
```

`load_session_ctx()` 兼容旧 list 格式，迁移时自动包一层 schema。

### 6.5 模型窗口只从配置读取

不要在方案里硬编码“某模型一定是 200K”。模型窗口属于会随版本变化的外部事实。EvoPaw 应从配置读取，并允许未知模型落到保守默认：

```yaml
context:
  default_model_ctx_limit: 32000
  compress_threshold: 0.45
  model_ctx_limits:
    claude-sonnet-4-6: 200000
    claude-haiku-4-5: 200000
  skill_output_max_chars: 16000
```

### 6.6 Bootstrap memoization 和失效点

`build_bootstrap_prompt()` 可以缓存，但必须以文件 `mtime/size/hash` 为失效键。变更点包括：

- `soul.md`
- `user.md`
- `agent.md`
- `memory.md`
- 未来支持的 include 文件

不要做无失效的全局缓存。

---

## 7. 改进五：多 Agent 协调

### 7.1 短期定位：EvoPaw 是 Coordinator 模式

当前 Main Agent 调用 task Skill 的方式是 Coordinator worker 模式，而不是 Fork。Worker 从空白上下文开始，仅通过 `task_context` 和 Skill 指令获得信息。这个模式适合 EvoPaw，但必须补齐三件事：

- Main Agent 必须综合 worker 输出，而不是原样转发。
- worker prompt 必须自包含，不能依赖“上文提到的内容”。
- worker 的工具集必须按 Skill 收窄。

### 7.2 P1：可选上下文注入

只有 `needs_context: true` 的 Skill 才注入上下文摘要：

```xml
<conversation_context>
最近 6-10 条关键对话摘要，不含完整历史。
</conversation_context>
```

约束：

- 默认不注入。
- 不注入用户所有历史。
- 不注入凭证、内部路径以外的敏感配置。
- 如果需要完整历史，引导 Skill 调用 `history_reader`，不要把完整历史塞进 prompt。

### 7.3 P1：SkillResult 信封和综合提示

`run_skill_agent()` 返回后统一包成 `SkillResult`。Main Agent 看到的是结构化结果，而不是一段散文：

- `status`: `ok|failed|partial`
- `summary`: 给 Main Agent 的短摘要
- `artifacts`: 产物路径和类型
- `output`: 截断后的原始输出
- `diagnostics`: 错误和审计信息

超过阈值时附加：

```xml
<synthesis_hint>
请综合以上 Skill 输出的关键信息回复用户。不要直接转发全文。
</synthesis_hint>
```

### 7.4 暂缓 Fork

Fork 模式需要父级完整上下文共享、单层约束、字节级稳定共享前缀和工具调用时拒绝递归 fork。EvoPaw 当前缺少这些底座，不建议在 `skill_loader.py` 里直接加 `asyncio.gather` 版本的“并行 Skill”。

更稳妥的路线：

1. 先把当前 Coordinator worker 做稳。
2. 增加 `SkillInvocationStore`，记录每个 worker 的输入、输出、状态和产物。
3. 只有当确实需要共享父上下文的并行任务时，再新增显式 `orchestration_mode=fork`，并和 Coordinator 模式互斥。

### 7.5 验证 worker 必须新建

如果未来加入“实现 worker + 验证 worker”，验证 worker 必须从空白上下文开始，不能复用实现 worker 的状态。否则实现阶段的假设会污染验证。

---

## 8. 改进六：生命周期与扩展性

### 8.1 HookDispatcher

新增 `evopaw/agents/hook_dispatcher.py`，所有 Hook 经由一个入口：

```python
class HookDispatcher:
    async def dispatch(self, event: HookEvent) -> HookResult: ...
```

第一版支持：

- `PreSkill`
- `PostSkill`
- `SessionStart`
- `SessionEnd`
- `Error`

保留 Claude Agent SDK 的 `PreToolUse/PostToolUse` verbose hooks，但不要让 verbose 直接成为唯一 Hook 系统。

### 8.2 信任门：all-or-nothing

Hook 触发前检查 workspace trust：

- 未信任：所有 hook 跳过，包括进程内 hook。
- 已信任：按来源优先级合并。
- session-scoped hook 只在当前 session 有效，结束时清理。

EvoPaw 短期可以先用配置项：

```yaml
security:
  trusted_workspace: true
```

后续再引入工作区指纹或管理员确认。

### 8.3 BackgroundTaskStore

将 pgvector index、memory extraction、cron run、后台 hook 统一登记：

```json
{
  "task_id": "memext-s-xxx-0001",
  "type": "memory_extraction",
  "status": "running|completed|failed|killed",
  "session_id": "s-xxx",
  "created_at": "...",
  "updated_at": "...",
  "output_path": "data/background/memext-s-xxx-0001.json"
}
```

原则：

- 多数任务可直接进入 `running`，不强行 pending。
- 终态立即落盘。
- 父级确认收到通知后再清理内存状态。
- shutdown 时集中 cancel/drain。

### 8.4 Cron 升级为一等 Agent Task

当前 cron 本质是向 Runner 回灌一条消息。下一版应改为：

- 每次 cron run 使用 fresh session。
- cron task 声明 `allowed_skills` 和权限 profile。
- cron 内禁止递归创建 cron，除非管理员确认。
- 结果投递目标和执行 session 分离。
- cron run 进入 `BackgroundTaskStore`，便于审计和重放。

### 8.5 启动序列

保留现有依赖排序，并补三类快速路径：

- `--version`
- `--check-config`
- `--list-skills`

快速路径不初始化飞书、数据库、Claude CLI，也不注入凭证。

---

## 9. 重新排序后的实施路线图

### Phase 0：运行时不变量修正（3-5 天）

| 编号 | 改进项 | 文件 | 交付标准 |
|---|---|---|---|
| P0-1 | Sub-Agent cwd 缩到 session 目录 | `skill_loader.py`, `skill_agent.py` | task Skill 只能在 `/workspace/sessions/{sid}` 下工作 |
| P0-2 | Skill frontmatter 元数据解析 + `path` 生效 | `skill_loader.py`, `load_skills.yaml` | `path/type` 不再双源失真，realpath 去重 |
| P0-3 | Skill 输出和 reference 正文硬上限 | `skill_loader.py` | 超限输出写入 `outputs/` 并返回恢复路径 |
| P0-4 | 历史截断恢复指针 | `main_agent.py`, `history_reader` | 截断提示包含可直接调用的 `skill_loader` 参数 |
| P0-5 | 权限门控骨架 + audit log | `permission_gate.py`, `skill_loader.py` | `ALLOW/DENY/ASK` 生效；ASK 在 headless 下不放行 |

### Phase 1：记忆和上下文可靠性（1-2 周）

| 编号 | 改进项 | 文件 | 交付标准 |
|---|---|---|---|
| P1-1 | topic-first `memory-save` | `evopaw/skills/memory-save/scripts/save.py` | 写 topic 后写 index；超限拒绝 |
| P1-2 | `ctx.json` schema + 兼容加载 | `context_mgmt.py` | 新旧格式均可读，快照带时间和模型信息 |
| P1-3 | `memory-governance` scan | `memory-governance/scripts/` | 能发现死链、orphan topic、重复和超长条目 |
| P1-4 | SkillResult 信封 | `skill_loader.py`, `skill_agent.py` | Main Agent 收到结构化结果和综合提示 |
| P1-5 | 配置化上下文窗口 | `config.yaml.template`, `context_mgmt.py` | 不再硬编码模型窗口 |

### Phase 2：安全和 Skill 供应链（1-2 周）

| 编号 | 改进项 | 文件 | 交付标准 |
|---|---|---|---|
| P2-1 | `allowed_tools` 按 Skill 生效 | `skill_loader.py`, `claude_client.py` | Sub-Agent 不再默认全工具 |
| P2-2 | Bash 命令前缀分类 | `permission_gate.py` | 高风险命令 ASK/DENY，低风险脚本可 allow |
| P2-3 | 发现列表预算和降级 | `skill_loader.py` | 预算超限时非内置 Skill 降级 |
| P2-4 | 多 source Skill discovery | `skill_loader.py`, config | bundled/project/user source 可组合 |
| P2-5 | 缺失 Skill 脚本补齐 | 各 Skill | README 中声明的 Skill 均可执行或降级为 reference |

### Phase 3：生命周期治理（2-3 周）

| 编号 | 改进项 | 文件 | 交付标准 |
|---|---|---|---|
| P3-1 | HookDispatcher | `agents/hook_dispatcher.py` | 所有新 hook 经单一分发点 |
| P3-2 | BackgroundTaskStore | `background/` | indexing/extraction/cron run 有状态和产物 |
| P3-3 | post-turn memory extraction | `runner.py`, `memory/` | 最终回复后合并触发，shutdown drain |
| P3-4 | Cron 一等任务 | `cron/`, `runner.py` | fresh session、权限 profile、run 审计 |
| P3-5 | `/remember` 审计命令 | `runner.py`, `memory/` | 只产出建议，用户确认后应用 |

### Phase 4：高级编排和插件化（3-4 周）

| 编号 | 改进项 | 文件 | 交付标准 |
|---|---|---|---|
| P4-1 | ContextEngine 抽象 | `context/engine.py` | prompt 装配、保存、恢复集中管理 |
| P4-2 | 一次性飞书确认授权 | `permission_gate.py`, `feishu/sender.py` | ASK 可通过卡片确认恢复执行 |
| P4-3 | Skill 安装/更新/隔离区 | `skills/` | 外部 Skill 进入候选区，审计后启用 |
| P4-4 | 显式 Fork 模式试点 | `orchestration/` | 与 Coordinator 互斥，单层 fork，结果可追踪 |
| P4-5 | Swarm 评估，不直接实施 | docs | 有明确场景和状态共享设计后再立项 |

---

## 10. 禁止事项清单

这些是为了避免偏离上游 Harness 模式而加入的硬约束：

1. 不把 `memory-save` 做成简单追加 `memory.md` 的临时实现。
2. 不把 `ASK` 转成静默 `ALLOW`。
3. 不让 task Skill 默认获得 `Bash/Read/Write/Edit/Grep/Glob` 全集。
4. 不再把 `load_skills.yaml` 当作 Skill 元数据权威来源。
5. 不在 `skill_loader.py` 里直接加无状态 `asyncio.gather` 来冒充 Fork。
6. 不只靠 prompt 文本约束沙盒路径。
7. 不硬编码模型上下文窗口。
8. 不在没有信任门的情况下扩展 Hook。
9. 不把 pgvector 当成 durable memory 的 source of truth。
10. 不让 cron 继承普通对话上下文执行高权限任务。

---

## 11. 关键文件变更清单

| 文件 | Phase | 变更 |
|---|---|---|
| `evopaw/tools/skill_loader.py` | P0-P2 | frontmatter 解析、path 生效、预算控制、输出上限、权限门控、SkillResult |
| `evopaw/agents/skill_agent.py` | P0-P2 | session cwd、allowed_tools、结果信封 |
| `evopaw/llm/claude_client.py` | P0-P2 | permission_mode 配置化，Sub-Agent 工具集参数化 |
| `evopaw/tools/permission_gate.py` | P0-P2 | 新建权限门控、Bash 分类、审计 |
| `evopaw/agents/main_agent.py` | P0-P1 | 精确恢复指针、ContextEngine 迁移准备 |
| `evopaw/memory/context_mgmt.py` | P1 | ctx schema、模型窗口配置化、兼容加载 |
| `evopaw/memory/bootstrap.py` | P1-P4 | memory index 上限、未来 include 和缓存失效 |
| `evopaw/skills/memory-save/scripts/save.py` | P1 | 新建 topic-first memory writer |
| `evopaw/skills/memory-governance/scripts/` | P1 | 新建治理脚本 |
| `evopaw/runner.py` | P0-P3 | sender_id 透传、post-turn extraction、`/remember`、shutdown drain |
| `evopaw/agents/hook_dispatcher.py` | P3 | 新建 Hook 单一分发点 |
| `evopaw/background/` | P3 | 新建后台任务状态机 |
| `evopaw/cron/` | P3 | cron run 一等任务化 |
| `config.yaml.template` | P0-P3 | security/context/skill discovery 配置 |
