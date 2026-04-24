# EvoPaw 基于 Hermes Agent 的改进方案

> 基于 `NousResearch/hermes-agent` 的仓库、官方文档与功能设计，对照 `EvoPaw` 当前实现，给出一份更偏“长期运行 Agent 操作系统”的升级方案。
>
> 文档定位：这是对现有 [docs/harness-improvement-plan.md](./harness-improvement-plan.md) 的补充与升级，不重复六层 Harness 模式的基础结论，而是重点吸收 Hermes 已经产品化落地的那部分能力。

---

## 1. 结论先行

Hermes Agent 最值得借鉴的，不是“它有更多工具”或“它支持更多平台”，而是它把 Agent 当成了一个**长期运行、持续学习、可治理、可扩展的运行时系统**来设计。

对 EvoPaw 来说，这意味着改进重点不应只放在“再加几个 Skill”，而应转向下面六条主线：

1. **把学习闭环做实**：从“能创建 Skill”升级为“能从任务经验中产生候选 Skill、评测、灰度、修补、淘汰”。
2. **把记忆重新分层**：从“Bootstrap 文件 + ctx.json + pgvector”升级为“热记忆快照 + 会话检索 + 冷记忆/用户建模”。
3. **把 Prompt / Context 工程独立成子系统**：让 system prompt、上下文压缩、检索恢复、子目录上下文发现成为显式模块，而不是散落在 `main_agent.py` 里。
4. **把安全从默认放行改成默认受控**：当前 `permission_mode="bypassPermissions"` 是最大风险点，需要尽快补齐审批、沙箱、路径与凭证隔离。
5. **把 Skills 从“本地目录”升级为“技能供应链”**：支持生命周期、来源、信任级别、外部目录、候选区和自演化。
6. **把 Feishu 机器人升级为平台化 Agent Runtime**：短期不必追 Hermes 那样的多平台规模，但架构上要从 Feishu 专用管道升级为 gateway-core + adapter 模式。

一句话概括：**Hermes 给 EvoPaw 的最大启发，是从“会做事的聊天机器人”走向“会长期积累、会自我优化、可被治理的 Agent 系统”。**

---

## 2. 这份方案与现有 Harness 方案的关系

`docs/harness-improvement-plan.md` 已经覆盖了六层 Harness 的基础方向，尤其是：

- 记忆三层分离
- Skill 渐进式披露
- 工具安全与权限
- 上下文预算控制
- 多 Agent 协调
- 生命周期与扩展点

Hermes 给出的新增价值，主要体现在下面这些“更产品化、更运行时化”的方面：

| 维度 | 现有 Harness 方案 | Hermes 带来的新启发 | 对 EvoPaw 的意义 |
|---|---|---|---|
| 记忆 | 偏重记忆分层与写入通道 | 强调有界热记忆、主动保存、会话搜索、用户建模插件 | 让记忆从“能存”变成“好用、可控、可扩展” |
| Skill | 偏重前端元数据和懒加载 | 强调 `skill_manage`、外部目录、技能 Hub、候选区、版本/来源治理 | 让 Skill 从静态目录升级为持续演化资产 |
| 上下文 | 偏重压缩与预算 | 强调 prompt 稳定前缀、双层压缩、上下文引擎插件、子目录上下文发现 | 让上下文工程不再散落在业务代码里 |
| 安全 | 偏重 fail-closed 原则 | 已实现危险命令审批、容器边界、上下文扫描、MCP 凭证过滤、路径校验 | 给 EvoPaw 提供一套可落地的防线模板 |
| 多 Agent | 偏重 Coordinator/Worker 思想 | 已实现 fresh-context delegation、并行子代理、代码执行 RPC | 给 EvoPaw 提供更通用的子任务执行框架 |
| 生命周期 | 偏重 hooks 思想 | 已落地 gateway hooks、session lineage、background maintenance、profiles | 让扩展性真正进入可运营阶段 |

**建议采纳方式**：

- 把 `harness-improvement-plan.md` 视为 EvoPaw 的“基础方法论版本”。
- 把本文件视为 EvoPaw 的“产品化运行时版本”。
- 后续真正拆分实施计划时，应该以本文件为主，以前一份作为底层设计原则的参考。

---

## 3. Hermes Agent 的关键设计模式

下面是我认为对 EvoPaw 最有借鉴价值的 Hermes 设计模式。

### 3.1 稳定的 Prompt 装配层

Hermes 明确区分：

- **缓存友好的稳定 system prompt 层**
- **只在当前 API 调用时生效的临时 overlay**

它把 `SOUL.md`、`MEMORY.md`、`USER.md`、skills index、context files、platform hint 等分层组装，并强调**system prompt 在会话中尽量不变**。这直接服务于上下文稳定性与 prompt caching。

对比 EvoPaw：

- `build_bootstrap_prompt()` 负责加载 `soul.md / user.md / agent.md / memory.md`
- `ctx.json` 摘要和最近历史在 `evopaw/agents/main_agent.py` 中拼接
- 这套逻辑能工作，但“稳定层”“会话层”“本次调用层”还没有明确边界

这会导致两个问题：

1. `ctx.json` 和 memory 的职责容易混淆
2. 后续如果要做缓存、fallback、多 provider、插件化上下文，很难演化

### 3.2 有界热记忆 + 会话检索 + 可选用户建模

Hermes 的记忆不是“无限追加文件”，而是：

- `MEMORY.md`：Agent 的稳定环境/项目记忆
- `USER.md`：用户画像
- 严格字符预算
- `memory` 工具支持 `add/replace/remove`
- 自动去重、容量控制、安全扫描
- `session_search` 基于 SQLite FTS5 做跨会话搜索
- 可选 Honcho memory provider 做更深层用户建模

对比 EvoPaw：

- `soul.md / user.md / agent.md / memory.md` 作为 Bootstrap 注入
- `ctx.json` / `raw.jsonl` 承担会话级恢复
- `pgvector` 提供语义搜索
- `memory-save` 有规范，但执行闭环还不完整

EvoPaw 当前最大问题不是“没有记忆”，而是**记忆层之间的角色边界不够清晰**：

- `ctx.json` 更像会话快照，不应承担 durable memory
- `memory.md` 当前更像索引文件，热记忆密度不足
- `pgvector` 适合“语义近似召回”，但不适合取代结构化 session search

### 3.3 闭环学习：Memory Nudge + Skill Manage + Search Recall

Hermes README 里强调的 closed learning loop，不是口号，而是几种能力的组合：

- 对话中学到稳定信息时主动写 memory
- 复杂任务完成后把流程沉淀为 skill
- Skill 可以被 `create/patch/edit/delete`
- 跨会话问题优先用 `session_search`
- 用户建模可以跨 session、跨平台累积

对比 EvoPaw：

- `skill-creator` 已能创建技能
- `search_memory` 已能查历史
- `memory-save` 已有规范
- 但三者之间缺少统一的“反思-提炼-评测-发布”闭环

值得强调的一点是：EvoPaw 现有 `evopaw/skills/skill-creator/` 目录里已经有一套很强的资产：

- `run_eval.py`
- `run_loop.py`
- `aggregate_benchmark.py`
- `grader.md`
- `analyzer.md`
- `comparator.md`

这意味着 EvoPaw 并不是从零开始做自进化，而是**已经有评测土壤，但还没有接上运行时闭环**。

### 3.4 Skills 不只是目录，而是供应链

Hermes 的 Skills 设计比一般 Agent 项目成熟很多，关键点包括：

- 单一 source of truth：`~/.hermes/skills/`
- Progressive disclosure：list → full skill → reference file
- agent-managed skills：可以 patch/edit，而不只是 create
- external skill directories
- skills hub / 安装 / 更新 / reset
- quarantine / audit log / bundled manifest

对比 EvoPaw：

- `evopaw/tools/skill_loader.py` 已实现渐进式披露
- `load_skills.yaml` 也已经形成注册表
- 但 Skill 生命周期仍停留在“仓库内静态目录 + 用户/模型手工创建”

Hermes 告诉我们的核心不是“也做一个技能商店”，而是：

> **Skill 必须被当成一类可治理资产，而不是普通 Markdown 文件。**

### 3.5 Tool Runtime / Sandboxing / Approval 是一等公民

Hermes 的安全文档非常明确：它把安全边界拆成七层，包括用户授权、危险命令审批、容器隔离、MCP 凭证过滤、上下文文件扫描、跨会话隔离和路径/输入校验。

对比 EvoPaw：

- 当前 `build_main_agent_options()` 与 `build_sub_agent_options()` 都使用 `permission_mode="bypassPermissions"`
- Sub-Agent 默认开放 `Bash / Read / Write / Edit / Grep / Glob`
- 这能提高实验速度，但不适合长期运行和生产环境

EvoPaw 的 README 里说“所有执行在容器内隔离”，方向是对的，但现阶段还缺少：

- 危险命令审批
- 永久 allowlist
- 凭证白名单透传
- 上下文文件注入扫描
- cron 场景下的权限收缩

### 3.6 Context Engine 应该是插件点，而不是工具函数集合

Hermes 有一个很重要但很容易被忽略的设计：`ContextEngine` 抽象。

这意味着：

- 什么时候压缩
- 怎么压缩
- 是否暴露检索工具
- 如何追踪 token 使用

这些都不是硬编码在主循环里，而是可以被替换的引擎。

对比 EvoPaw：

- `evopaw/memory/context_mgmt.py` 已经有 prune / chunk / compress 三把剪刀
- 但它还是工具函数级别，不是系统级策略对象

如果 EvoPaw 后续要支持：

- 更强的 token-aware 压缩
- lossless retrieval
- 更稳定的 tool-result compaction
- 项目上下文的按需恢复

那么 ContextEngine 抽象会非常关键。

### 3.7 Session Store 是检索、分析、回放、治理的根

Hermes 使用 SQLite + FTS5 存 session 元数据和消息历史，并支持：

- full-text session search
- lineage
- 统计分析
- 多进程 contention handling

对比 EvoPaw：

- `SessionManager` 使用 `index.json + s-*.jsonl`
- 这对当前规模够用
- 但它几乎无法支撑更高级能力：
  - 复杂检索
  - 会话 lineage
  - insights 报表
  - 技能效果回放
  - 多 worker / 多实例写入治理

### 3.8 Cron 应该是第一类 Agent Task，而不是“帮我再发一条消息”

Hermes 的 cron 不是 shell cron，而是 first-class agent task：

- fresh session
- attached skills
- normal static tool list
- delivery target
- 禁止 cron 中再递归创建 cron

对比 EvoPaw：

- `CronService` 到点后构造一条 `InboundMessage`
- 直接回灌到现有 `Runner`
- 本质上更像“自动发送一条消息”

这种方式简单，但有几个问题：

1. 会继承原对话上下文，导致定时任务被旧会话污染
2. cron 任务与普通对话共享 session，难以审计
3. 没有技能附件和权限裁剪
4. 没有限制递归调度

### 3.9 平台化 Gateway 比单平台 Listener 更适合长期演化

Hermes 的 gateway-core + adapters + hooks + delivery path + token lock 设计，非常适合长期演化。

EvoPaw 当前架构偏 Feishu 专用：

- `feishu/listener.py`
- `feishu/sender.py`
- `runner.py`
- `api/test_server.py`

短期这完全没问题，但如果未来要支持：

- 多工作区
- 多 bot persona
- 多渠道接入
- 后台任务结果回送到不同 channel

那就需要把“平台适配”和“Agent 核心运行时”剥离开。

---

## 4. EvoPaw 当前状态：优势与核心短板

### 4.1 当前优势

EvoPaw 不是一个“从零开始”的项目，它已经有不少非常好的基础：

- `SkillLoaderTool` 的渐进式披露已经成型
- per-routing_key 串行队列模型合理
- Feishu 接入体验与 thread/group/p2p 路由已经落地
- `Bootstrap + ctx.json + pgvector` 三层记忆雏形清晰
- `history_reader` 内联优化做得对
- `skill-creator` 目录里已经有 benchmark / grader / analyzer 资产
- `CronService`、`CleanupService`、`TestAPI`、Prometheus 说明系统性意识不错

### 4.2 核心短板

如果以 Hermes 为标尺，EvoPaw 当前最关键的短板有九个：

1. **学习闭环未成型**：能 create skill，但不能自动反思、patch、评测、发布。
2. **默认安全模型过弱**：`bypassPermissions` 直接跳过审批。
3. **Prompt 装配边界不清晰**：Bootstrap、ctx、history、tool constraints 混在 `main_agent.py`。
4. **缺少 session-level 结构化检索**：`pgvector` 很强，但不能替代 session_search。
5. **Skill 生命周期缺失**：没有候选区、版本、来源、信任与统计。
6. **cron 仍是“消息重放”模型**：不是 first-class task runtime。
7. **上下文工程没有独立抽象层**：未来很难替换压缩策略。
8. **平台内核与 Feishu 耦合较深**：扩展成本高。
9. **provider/runtime 过于单一**：主模型、辅助模型、fallback、缓存、不同执行面都还没统一。

---

## 5. 改进方案一：建立真正的闭环学习内核

### 5.1 目标

把 EvoPaw 从：

- “用户要求时创建 Skill”

升级为：

- “任务完成后自动反思”
- “识别是否应保存 memory”
- “识别是否应创建/修补 Skill”
- “用现有 benchmark/eval 资产验证候选改动”
- “候选 Skill 先进入隔离区，再灰度发布”

### 5.2 建议架构

新增一个 `learning/` 子系统：

```text
evopaw/
├── learning/
│   ├── reflection.py         # 每轮或每任务结束后的反思入口
│   ├── candidate_store.py    # Skill / memory / prompt 改动候选池
│   ├── skill_lifecycle.py    # draft -> quarantine -> canary -> enabled
│   ├── evaluator.py          # 复用 skill-creator 的 eval 脚本
│   ├── heuristics.py         # 触发条件：复杂任务、用户纠正、重复流程
│   └── telemetry.py          # 记录技能成功率、失败率、人工否决原因
```

### 5.3 触发条件

建议先采用**启发式触发**，不要一开始就做全自动演化：

- 同一任务中发生 `>=5` 次有效工具调用且最终成功
- 用户明确纠正过流程
- 同类工作流在 7 天内重复出现多次
- 某 Skill 连续失败但用户/模型找到替代路径
- 某 Skill 的 benchmark 长期低于阈值

### 5.4 复用现有资产

EvoPaw 不需要重新发明 skill evolution pipeline，因为 `skill-creator` 里已经有：

- 触发评测：`run_eval.py`
- 迭代优化：`run_loop.py`
- benchmark 汇总：`aggregate_benchmark.py`
- 执行结果评分：`grader.md`
- 赛后分析：`analyzer.md`

正确做法不是另起炉灶，而是：

1. 用运行时反思逻辑产出候选 Skill/Description
2. 把候选丢给这套现有评测链
3. 评测通过后再进入发布流程

### 5.5 生命周期设计

建议把 Skill 生命周期标准化：

| 状态 | 含义 | 是否对主 Agent 可见 |
|---|---|---|
| `draft` | 反思生成的初稿 | 否 |
| `quarantine` | 已落盘，等待评测/审核 | 否 |
| `canary` | 小流量/仅指定 routing_key 启用 | 部分可见 |
| `enabled` | 正式启用 | 是 |
| `deprecated` | 已不推荐，但保留 | 只读 |
| `retired` | 下线归档 | 否 |

### 5.6 优先级

- `P0`：反思日志 + candidate store
- `P1`：Skill 自动候选生成 + 离线评测接入
- `P2`：自动 patch + canary 发布

---

## 6. 改进方案二：把记忆升级为“热记忆快照 + 会话检索 + 冷记忆”

### 6.1 关键判断

相较于上一版 Harness 方案里偏“`memory.md` 做索引、详情放 topic 文件”，我建议 EvoPaw 改成更接近 Hermes 的模式：

- **热记忆**：始终注入 prompt，必须小而精
- **会话检索**：需要时搜索全部历史
- **冷记忆/长文档**：按需打开

也就是说，不能把所有 durable memory 都外包给 topic 文件，否则模型每次都要额外检索；也不能把所有信息都塞进 `memory.md`，否则会膨胀失控。

### 6.2 目标分层

建议重构为四层：

| 层级 | 作用 | 建议实现 |
|---|---|---|
| L0 Identity | 代理身份与规则 | `soul.md` + `agent.md` |
| L1 Hot Memory | 高频 durable facts | `user.md` + `memory.md`，严格限额 |
| L2 Session Snapshot | 当前/最近会话连续性 | `ctx.json` 或新 `conversation_snapshot.json` |
| L3 Long-tail Recall | 历史会话与长尾知识 | SQLite FTS5 + pgvector + topic files |

### 6.3 本地 Memory Tool 化

新增统一的 `memory_service`，不要再依赖“让 Skill 自己操作文件”作为主路径：

```text
evopaw/memory/
├── service.py          # add / replace / remove / usage
├── scanner.py          # prompt injection / invisible unicode / exfiltration scan
├── quota.py            # 容量限制和压缩策略
├── providers/
│   ├── local.py        # 本地文件 provider
│   └── remote.py       # 未来 Honcho-like provider
```

建议支持操作：

- `add(target="memory" | "user", content=...)`
- `replace(target=..., old_text=..., content=...)`
- `remove(target=..., old_text=...)`
- `usage()`

并补齐：

- 去重
- substring 匹配更新
- 容量超限报错与整理建议
- 注入扫描

### 6.4 `memory-save` 的角色调整

`memory-save` 仍然保留，但角色要变化：

- 以前：让 Agent 通过 Skill 来做文件写入
- 以后：`memory-save` 更像一层“记忆策略 Skill”，负责**判断该不该记、记什么**
- 真正的写入由 `memory_service` 完成

这样可以把“策略”和“存储”分开。

### 6.5 引入 Session Search

建议新增 `session_search`，不要再把“查历史”只押注在 pgvector 上。

原因很简单：

- `pgvector` 擅长语义近似
- FTS5 擅长短语、关键字、精确回忆
- 很多“上次你帮我查的那个命令/文件/错误码”更像 FTS 问题，不是 embedding 问题

建议做法：

1. 用 SQLite 建 session/messages/messages_fts
2. 搜索命中后取 top-N session
3. 截取命中附近上下文
4. 用轻量模型做 focused summary
5. 返回结构化结果给主 Agent

### 6.6 保留 pgvector，但把职责收窄

pgvector 不要删，但建议职责改成：

- 跨会话语义召回
- 相似问题归档
- 反思/benchmark 样本挖掘
- 技能候选发现

也就是说，pgvector 应该从“唯一历史检索入口”变成“高级语义层”。

### 6.7 未来预留：用户建模 Provider

Honcho 的真正启发不是“接第三方服务”，而是：

> 用户建模应该是一个 provider，而不是散落在主流程里的硬编码逻辑。

EvoPaw 可以先不接远端服务，但架构上应预留：

- `memory.provider = local | remote`
- `recall_mode = context | tools | hybrid`
- `write_frequency = async | turn | session`

### 6.8 优先级

- `P0`：本地 memory service + session_search MVP
- `P1`：SQLite + FTS5 替换/补充 `index.json + JSONL`
- `P2`：provider 化用户建模

---

## 7. 改进方案三：把 Prompt / Context 工程独立成一层

### 7.1 当前问题

当前 EvoPaw 的 Prompt 装配逻辑主要在 `evopaw/agents/main_agent.py`：

- Bootstrap prompt
- tool constraint
- ctx 摘要
- conversation history
- user message
- image block

这虽然能跑，但随着系统成长，会出现几个痛点：

- system prompt 结构不可见
- 不同 provider 很难共享
- 子代理/cron/测试模式的 prompt 差异难管理
- 压缩、恢复、上下文文件发现无法复用

### 7.2 目标结构

建议新增 `evopaw/agent_runtime/prompt_builder.py`：

```text
build_system_prompt(
    identity,
    agent_rules,
    hot_memory_snapshot,
    skills_index,
    project_context,
    platform_hint,
)

build_turn_overlay(
    session_snapshot,
    retrieved_session_context,
    current_user_message,
    multimodal_blocks,
)
```

### 7.3 分层原则

建议采用 Hermes 式的明确分层：

1. Identity（`soul.md`）
2. Agent Rules（`agent.md`）
3. Hot Memory（`user.md` + `memory.md`）
4. Skills Index（精简 metadata）
5. Project Context（`AGENTS.md` / `CLAUDE.md` / workspace hints）
6. Platform Hint（Feishu/TestAPI）
7. Turn Overlay（ctx/session search/当前消息）

### 7.4 引入 ContextEngine 抽象

新增：

```text
evopaw/context/
├── engine.py             # ContextEngine ABC
├── compressor.py         # 当前 maybe_compress 的系统化版本
├── session_hygiene.py    # 进入主 Agent 前的粗粒度压缩
├── retrieval.py          # session recall / vector recall
└── hints.py              # 子目录上下文发现
```

接口建议至少包括：

- `should_compress(history, usage) -> bool`
- `compress(history, usage) -> CompressedHistory`
- `retrieve(query, session_id) -> ContextPack`
- `discover(paths) -> ContextHints`

### 7.5 子目录上下文发现

Hermes 的一个非常实用的能力是：随着 agent 读到子目录文件，逐步发现并注入子目录 `AGENTS.md`。

EvoPaw 也可以做一个轻量版本：

- 当 Skill / Agent 访问某个路径
- 向上查找最近的 `AGENTS.md` / `CLAUDE.md`
- 经过安全扫描后缓存注入

这对复杂仓库里的局部约束非常重要。

### 7.6 当前 `ctx.json` 的定位修正

建议把 `ctx.json` 从“长期记忆”语义改成“会话快照”语义：

- 它解决的是 session continuity
- 不是 durable preference memory
- 也不应被无限叠加为“伪长期记忆”

必要的话可以直接重命名为 `conversation_snapshot.json`，以减少概念混乱。

### 7.7 优先级

- `P0`：抽出 `prompt_builder.py`
- `P1`：ContextEngine + dual compression
- `P2`：子目录上下文发现 + prompt caching 兼容

---

## 8. 改进方案四：把 Skills 升级为“可治理资产”

### 8.1 当前问题

EvoPaw 的 SkillLoader 很不错，但现在仍然缺少 Hermes 那种“资产化治理”能力：

- 没有 skill status/lifecycle
- 没有 source/trust/version
- 没有 candidate/quarantine
- 没有 external skill dirs
- 没有 update/reset/rollback

### 8.2 建议元数据扩展

在现有 frontmatter 上新增：

```yaml
---
name: scheduler_mgr
description: ...
type: task
version: "1.1"
category: productivity
triggers:
  - "定时"
  - "提醒"
  - "cron"
execution_mode: isolated
allowed_tools:
  - Bash
  - Read
source:
  kind: bundled        # bundled | local | generated | imported
  uri: ""
trust: internal        # internal | reviewed | generated | quarantined
status: enabled        # draft | quarantine | canary | enabled | deprecated
owner: system
last_used_at: ""
quality_score: 0.82
---
```

### 8.3 Registry 抽象

建议从 `load_skills.yaml` 过渡到“manifest + runtime registry”：

```text
evopaw/skills_registry/
├── manifest.py
├── resolver.py
├── lifecycle.py
├── audit.py
└── sources.py
```

支持多个来源：

- bundled（仓库自带）
- local（用户/工作区创建）
- generated（学习闭环产出）
- shared（团队共享目录）

### 8.4 引入 Skill Manage

新增内部服务，而不是继续只靠 `skill-creator`：

- `create`
- `patch`
- `edit`
- `delete`
- `write_file`
- `remove_file`

其中：

- `patch` 应该是首选更新方式
- `edit` 只用于大重构
- 所有 AI 生成改动默认进入 `quarantine`

### 8.5 候选区与审计

建议新增目录：

```text
data/skills/
├── active/
├── quarantine/
├── archived/
└── audit.log
```

这样可以做到：

- 候选技能不立即暴露给主 Agent
- 所有生成/更新/禁用都有审计轨迹
- 出问题时可以回滚

### 8.6 不建议现在就做完整 Skill Hub

Hermes 的 Skills Hub 很强，但 EvoPaw 不需要一开始就做线上安装市场。

更现实的路径是：

- `P1`：支持 external skill dirs
- `P2`：支持共享团队技能目录
- `P3`：才考虑远端 registry / hub

### 8.7 优先级

- `P1`：metadata 扩展 + lifecycle + quarantine
- `P2`：skill_manage + external dirs
- `P3`：远端 registry / hub

---

## 9. 改进方案五：补齐安全边界，停止默认放行

### 9.1 当前风险点

`evopaw/llm/claude_client.py` 里，主 Agent 与 Sub-Agent 都使用：

```python
permission_mode="bypassPermissions"
```

这在实验期能提高效率，但在持续运行、自动定时、会写文件、能调飞书 API 的系统里，风险非常高。

### 9.2 建议的五层安全改造

短期先做比 Hermes 简化但足够实用的版本：

#### 第一层：危险命令审批

新增 `security/approval.py`：

- 命令模式匹配
- `manual | smart | off`
- 支持一次性通过 / 本 session 通过 / 永久 allowlist

#### 第二层：执行后端分级

至少区分：

- `trusted_local`
- `restricted_container`
- `cron_restricted`

不同 Skill / 任务绑定不同后端与权限模板。

#### 第三层：上下文文件扫描

对所有会进入 prompt 的文件执行扫描：

- `soul.md`
- `user.md`
- `agent.md`
- `memory.md`
- `AGENTS.md`
- `CLAUDE.md`

拦截：

- “忽略之前指令”
- 隐藏注释注入
- 读取 `.env` / 凭证
- curl 外发
- 零宽字符 / bidi

#### 第四层：凭证白名单透传

未来不应默认让所有 Skill 看到全量环境变量。

建议：

- Skill 声明所需 secret 名称
- runtime 按 allowlist 注入
- 其余变量不透传

#### 第五层：路径与 session 边界校验

需要保证：

- cron job 不能越过自己的工作区
- session A 不能读写 session B 的数据
- `routing_key` / `session_id` / `workdir` 都经过标准化校验

### 9.3 Hermes 的经验如何取舍

Hermes 在容器后端里会弱化危险命令审批，因为容器本身是边界。

EvoPaw 不应直接照搬这一点。原因是：

- EvoPaw 有 Feishu 发送能力
- 有 cron
- 有持久化 workspace
- 还有用户上传附件

即使在容器里，误删持久卷、误发消息、误改技能资产依然是实质风险。

### 9.4 优先级

- `P0`：危险命令审批 + 上下文扫描
- `P1`：凭证白名单 + 后端分级
- `P2`：完整安全策略中心

---

## 10. 改进方案六：重构 Session / Search / Analytics 底座

### 10.1 为什么 `index.json + JSONL` 不够了

当前 `SessionManager` 的优点是简单可靠，但随着 EvoPaw 进入长期运行阶段，它会成为瓶颈：

- 无法做高质量全文检索
- 无法做 lineage
- 无法做统计分析
- 不适合多进程/多实例并发
- 难以支撑 learning loop 的数据面

### 10.2 建议目标

以 SQLite 为主存储，以 JSONL 为审计副本：

```text
data/state.db
├── sessions
├── messages
├── messages_fts
├── tool_calls
├── skill_events
├── memory_events
└── benchmarks
```

保留：

- `raw.jsonl` 或 `sessions/*.jsonl` 作为 append-only 审计日志

### 10.3 应记录的新字段

建议在消息/会话级记录：

- model/provider
- token usage
- tool calls count
- elapsed ms
- compression events
- retrieved sessions
- triggered skills
- user correction markers
- cron / normal / api / test source

这样后面才能做：

- `/insights`
- 技能成功率分析
- 高价值样本抽取
- 成本与延迟报表

### 10.4 新增 `session_search`

建议新增模块：

```text
evopaw/session_search/
├── index.py
├── search.py
├── summarize.py
└── tool.py
```

默认策略：

1. FTS5 搜索
2. top-N sessions 去重
3. 取命中上下文窗口
4. 轻量模型生成 per-session summary
5. 返回结构化结果

### 10.5 优先级

- `P0`：state.db + FTS5 + session_search
- `P1`：analytics 事件表
- `P2`：insights 报表与学习数据面

---

## 11. 改进方案七：把 Cron 从“消息回灌”升级为第一类任务运行时

### 11.1 当前问题

`CronService` 当前的核心动作是：

- 构造 `InboundMessage`
- 交给 `Runner.dispatch()`

它非常轻，但会带来上下文污染与治理问题。

### 11.2 目标模型

建议新增 `CronAgentRunner`：

```text
cron tick
  -> create fresh task session
  -> load attached skills / tool policy
  -> run isolated agent turn
  -> persist cron result
  -> deliver to target
```

### 11.3 任务对象升级

任务定义里建议新增：

- attached skills
- tool policy
- delivery target
- workspace/profile
- timeout
- retry policy
- no-recursive-schedule

例如：

```json
{
  "id": "job-001",
  "name": "daily-summary",
  "schedule": {"kind": "cron", "expr": "0 9 * * 1-5", "tz": "Asia/Shanghai"},
  "task": {
    "prompt": "请生成昨天工作摘要并发给我",
    "skills": ["daily-summary", "search_memory"],
    "tool_policy": "cron_restricted",
    "fresh_session": true
  },
  "delivery": {
    "kind": "origin"
  }
}
```

### 11.4 递归保护

Hermes 这里有一个很值得直接借鉴的点：

> cron-run sessions 不能再创建 cron job。

EvoPaw 也应该加同样约束，否则长期运行后很容易出现定时任务自繁殖。

### 11.5 优先级

- `P0`：cron fresh session + no-recursive-schedule
- `P1`：attached skills + delivery target
- `P2`：cron analytics + retry policy

---

## 12. 改进方案八：引入通用 Delegation 和低上下文成本执行

### 12.1 当前状态

EvoPaw 现在的“多 Agent”主要体现在：

- task 型 Skill -> Sub-Agent

这已经比单代理强很多，但还不是 Hermes 那种通用 delegation/runtime：

- 没有 generic `delegate_task`
- 没有并行 workstream 概念
- 没有类似 `execute_code` 的低上下文成本工具编排

### 12.2 两个方向都值得做

#### 方向 A：通用 Delegation

新增：

```text
delegate_task(
  goal,
  context,
  tool_policy,
  max_parallel=3,
)
```

适用于：

- 并行研究
- 复杂调试
- 代码审查
- 对话不想被中间过程污染的任务

#### 方向 B：Workflow/Code Execution Runtime

Hermes 的 `execute_code` 很值得借鉴：让 Agent 生成一个 Python 脚本，通过 RPC 调工具，只把最终 `print()` 返回给模型。

对 EvoPaw 的现实意义非常大，尤其适合：

- 搜索 -> 抓取 -> 过滤 -> 汇总
- 表格/PDF/文档批处理
- 投研报告流水线
- cron 批任务

### 12.3 对 EvoPaw 的建议形态

不必完全复刻 Hermes 的 Unix socket RPC，可以先做简化版：

```text
evopaw/workflow_runtime/
├── stub.py
├── executor.py
├── rpc_bridge.py
└── policies.py
```

第一期只支持：

- `read_file`
- `write_file`
- `search_memory`
- `web_browse`
- `tavily_search`
- `arxiv_search`
- `feishu_ops` 的安全子集

### 12.4 子代理上下文契约

Hermes 一个特别重要的原则是：

> Subagents know nothing.

EvoPaw 也应该把这条显式写进 delegation runtime：

- 子代理默认不继承父上下文
- 所有必要背景必须通过 `goal/context` 明确传入
- 避免“主代理以为子代理知道，子代理其实不知道”的隐性错误

### 12.5 优先级

- `P1`：generic delegation
- `P2`：workflow/code execution runtime

---

## 13. 改进方案九：从 Feishu 专用管道演进到 Gateway Core

### 13.1 不是为了支持 18 个平台，而是为了降低耦合

Hermes 支持很多平台，但 EvoPaw 不必盲目追求“渠道数量”。

真正值得学的是它的分层：

- gateway core
- platform adapters
- delivery path
- hooks
- status/locks

### 13.2 建议重构方向

```text
evopaw/
├── gateway/
│   ├── core.py
│   ├── events.py
│   ├── session_keys.py
│   ├── delivery.py
│   ├── hooks.py
│   └── adapters/
│       ├── feishu.py
│       └── test_api.py
```

然后把现有：

- `feishu/listener.py`
- `feishu/sender.py`
- `api/test_server.py`

逐步适配到这个模型里。

### 13.3 引入 Hook System

当前只有 verbose hooks。建议扩展为：

- `on_message_received`
- `on_session_loaded`
- `on_agent_started`
- `on_tool_started`
- `on_tool_finished`
- `on_memory_flushed`
- `on_cron_fired`
- `on_delivery_complete`
- `on_skill_candidate_created`

这样以后接：

- 审计
- 指标
- A/B 实验
- 学习闭环

都会容易很多。

### 13.4 Profile / Workspace 隔离

Hermes 的 profile 概念也很值得吸收。

EvoPaw 可以做一个更轻版本：

- 每个 bot persona / 租户 / workspace 有自己独立的：
  - config
  - memory
  - skills
  - sessions
  - cron

这样以后无论是多客户部署还是多人格助手，都会更清晰。

### 13.5 优先级

- `P1`：gateway core + adapter interface
- `P2`：hook system + profile/workspace isolation

---

## 14. 改进方案十：建立 Provider Runtime 和辅助模型路由

### 14.1 当前状态

EvoPaw 当前是：

- 主模型：Claude Sonnet
- Sub-Agent：Claude Haiku
- 辅助任务：Qwen（压缩、embedding）

这个组合能工作，但配置面比较散：

- 主对话模型在 `claude_client.py`
- 压缩和 embedding 走 DashScope OpenAI 兼容接口
- fallback、auxiliary、different task routing 还没有统一抽象

### 14.2 建议方向

新增 `provider_runtime/`：

```text
evopaw/provider_runtime/
├── resolve.py
├── models.py
├── fallback.py
├── auxiliary.py
└── metadata.py
```

统一管理：

- main model
- subagent model
- compression model
- session_search summarizer
- embedding model
- fallback model

### 14.3 为什么这很重要

因为后面很多能力都依赖它：

- prompt caching
- fallback
- auxiliary task routing
- session search summarization
- benchmark grading
- 安全审批里的 smart mode

### 14.4 现实建议

短期不要追 Hermes 那样的“18+ providers 全家桶”，只做：

- `anthropic`
- `openai-compatible`
- `dashscope`
- `fallback`

先把抽象打平，再扩展 provider 数量。

### 14.5 优先级

- `P1`：provider runtime 抽象
- `P2`：fallback + auxiliary model policy

---

## 15. 建议的实施路线图

### Phase 0：先补底线，别再裸奔

目标：把长期运行最危险的缺口堵上。

- 实现 `memory_service`，补齐 `memory-save` 写入闭环
- 引入 `session_search` MVP（SQLite + FTS5）
- 抽出 `prompt_builder.py`
- 去掉默认 `bypassPermissions`，加入危险命令审批
- 对 Bootstrap / Context 文件做注入扫描
- 把 cron 改成 fresh session，并禁止递归调度

### Phase 1：把运行时做成平台

目标：让 EvoPaw 从“可用项目”升级为“可演化平台”。

- `state.db` 替换/补充 `index.json + JSONL`
- 引入 `ContextEngine`
- Skill metadata 扩展
- 加入 Skill lifecycle / quarantine
- 抽象 gateway core + Feishu/TestAPI adapters
- provider runtime 抽象

### Phase 2：把闭环学习接上

目标：真正开始自我进化，而不是停留在“可手工进化”。

- 任务反思与候选生成
- 对接 `skill-creator` 现有 eval/benchmark 资产
- Skill canary / promote / rollback
- 记录 skill telemetry 和 user correction
- 建立 `/insights` 或管理报表

### Phase 3：可选高级能力

目标：扩展成更强的 Agent Runtime。

- generic delegation
- workflow/code execution runtime
- external skill dirs
- remote memory provider
- profile/workspace isolation
- 远端 skill registry / team skill repo

---

## 16. 哪些 Hermes 能力不建议现在照搬

### 16.1 不建议照搬 Hermes 的“大一体式主循环文件”

Hermes 的 `run_agent.py` 很强，但也非常大。

EvoPaw 当前模块化程度比 Hermes 更健康。应该学习其设计原则，不要学习其文件体量。

### 16.2 不建议马上追多平台

Hermes 的多平台是成熟阶段能力。

EvoPaw 当前更重要的是先把 Feishu 场景里的：

- 安全
- 记忆
- 检索
- 技能演化
- cron 隔离

做好。

### 16.3 不建议过早接远端用户建模服务

Honcho 很有启发，但会带来：

- 数据治理
- 隐私
- 额外依赖
- 网络稳定性

EvoPaw 应先把本地 provider 接口与 recall_mode 设计好，再决定是否接远端服务。

### 16.4 不建议先做公开 Skill Hub

在没有 quarantine、trust level、audit 之前，公开安装第三方 Skill 风险过高。

顺序应该是：

1. 本地生命周期治理
2. 团队共享目录
3. 再考虑远端 registry

---

## 17. 我建议优先落实的十个具体动作

如果只选最关键的十项，我建议按这个顺序做：

1. 新增 `memory_service`，补齐 `memory-save` 的真实执行闭环。
2. 把 `ctx.json` 明确定义为 session snapshot，而非长期记忆。
3. 用 SQLite + FTS5 建 `session_search`，不要只依赖 pgvector。
4. 把 `prompt_builder.py` 独立出来，形成稳定 prompt 层。
5. 去掉默认 `bypassPermissions`，引入危险命令审批。
6. 对 `soul/user/agent/memory/AGENTS/CLAUDE` 做 prompt injection 扫描。
7. 给 Skill 增加 lifecycle/source/trust/status 元数据。
8. 把 cron 任务改为 fresh session，禁止递归创建 cron。
9. 把 `skill-creator` 里的 eval/benchmark 资产接入运行时反思闭环。
10. 提前抽出 gateway core，哪怕短期仍只有 Feishu 和 TestAPI 两个 adapter。

---

## 18. 参考资料

以下资料用于形成本方案，建议后续实现时按此顺序深入：

- [Hermes Agent GitHub README](https://github.com/NousResearch/hermes-agent)
- [Hermes Architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture/)
- [Hermes Prompt Assembly](https://hermes-agent.nousresearch.com/docs/developer-guide/prompt-assembly)
- [Hermes Context Compression and Caching](https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching)
- [Hermes Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)
- [Hermes Persistent Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/)
- [Hermes Sessions / session_search](https://hermes-agent.nousresearch.com/docs/user-guide/sessions)
- [Hermes Security](https://hermes-agent.nousresearch.com/docs/user-guide/security/)
- [Hermes Tools & Toolsets](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools/)
- [Hermes Subagent Delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation/)
- [Hermes Code Execution](https://hermes-agent.nousresearch.com/docs/user-guide/features/code-execution/)
- [Hermes Cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron/)
- [Hermes Honcho Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/honcho/)
- [Hermes Provider Runtime](https://hermes-agent.nousresearch.com/docs/developer-guide/provider-runtime/)

EvoPaw 侧建议一起对照阅读：

- [docs/harness-improvement-plan.md](./harness-improvement-plan.md)
- `evopaw/agents/main_agent.py`
- `evopaw/tools/skill_loader.py`
- `evopaw/memory/bootstrap.py`
- `evopaw/memory/context_mgmt.py`
- `evopaw/memory/indexer.py`
- `evopaw/session/manager.py`
- `evopaw/llm/claude_client.py`
- `evopaw/cron/service.py`
- `evopaw/skills/skill-creator/`

---

## 19. 最终判断

如果说前一份 `harness-improvement-plan.md` 帮 EvoPaw 明确了“Agent Harness 应该有哪些层”，那么 Hermes Agent 给 EvoPaw 的更大启发是：

> **这些层不应只是存在，而应被做成一套长期运行、持续学习、可观测、可治理、可扩展的操作系统。**

EvoPaw 当前最值得做的，不是追 Hermes 的功能数量，而是先把 Hermes 已经验证过的三件事落到自己身上：

1. **热记忆 + 会话检索 + 反思闭环**
2. **稳定 Prompt 层 + 可替换 ContextEngine**
3. **安全边界 + Skill 生命周期治理**

这三件事一旦落地，EvoPaw 才会真正具备“自我进化”的工程基础。
