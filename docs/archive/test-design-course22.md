> ⚠️ **归档文档 — CrewAI / AIO-Sandbox 时代** ⚠️
>
> 本文档以 AIO-Sandbox 容器运行为测试前提，属于迁移前架构。对应的 `tests/integration/test_course22_cases.py` 中仍保留对 `evopaw.agents.main_crew` 的引用（实际模块已不存在）。
> 当前测试指南参见 `tests/integration/TEST_CASES.md`（仍待按 F2 拆分为"当前测试"与"历史课程材料"）。
>
> 归档日期：2026-04-22（按 `docs/redundancy-audit-2026-04-21.md` F1 处理）

---

# 第22课系统测试设计文档

> **对应课程**：第22课 项目实战3 — XiaoPaw 记忆篇
> **测试文件**：`tests/integration/test_course22_cases.py`
> **设计时间**：2026-03-24

---

## 一、测试目标

验证第22课演示的**四个核心 Case** 在真实基础设施上端到端可运行。
所有测试使用 `TestAPI`（`POST /api/test/message`），**不 Mock** 任何组件，依赖真实 Docker 和后端。

| 课程页 | Case 主题 | 测试 Group |
|--------|----------|-----------|
| P2 | 一句话生成投资早报（SOP 已在记忆里） | Group U |
| P3 | 阿里该不该挂单（搜索层 + 文件层协同） | Group V |
| P4 | 初始引导（agent.md 自我引导 SOP） | Group W（补充 Group T 未覆盖场景） |
| P5 | SOP 调教全流程（描述 → 固化 → 触发） | Group X |

---

## 二、基础设施要求

### 2.1 必须运行的服务

| 服务 | 地址 | 用途 | 对应 marker |
|------|------|------|-------------|
| AIO-Sandbox | `localhost:8022` | skill-creator / memory-save 执行 | `@pytest.mark.sandbox` |
| pgvector | `localhost:5432` | 对话历史向量索引 + 搜索 | `@pytest.mark.pgvector` |
| 通义 Qwen API | 外网 | LLM 推理 | `@pytest.mark.llm` |

### 2.2 环境变量

```bash
export QWEN_API_KEY=sk-xxx          # 通义 API Key（必须）
export MEMORY_DB_DSN="postgresql://evopaw:evopaw123@localhost:5432/evopaw_memory"  # Group V 必须
```

### 2.3 Docker 启动命令

```bash
# AIO-Sandbox
docker compose -f docker-compose.sandbox.yml up -d

# pgvector
docker compose -f docker-compose.pgvector.yml up -d

# 确认 memories 表已创建（首次运行）
psql $MEMORY_DB_DSN -f migrations/001_create_memories.sql
```

---

## 三、测试用例矩阵

| TC ID | 测试名 | Group | LLM | Sandbox | pgvector | 耗时(s) |
|-------|--------|-------|-----|---------|----------|---------|
| U-1 | SOP 描述触发 skill-creator | U | ✓ | ✓ | — | 60-90 |
| U-2 | 早报触发词自动路由到已创建技能 | U | ✓ | ✓ | — | 90-120 |
| U-3 | 多种触发词均路由到同一技能 | U | ✓ | ✓ | — | 90-120 |
| V-1 | 持仓写入 pgvector，新 session 能检索 | V | ✓ | ✓ | ✓ | 60-90 |
| V-2 | 决策回答同时引用搜索层和文件层 | V | ✓ | ✓ | ✓ | 120-180 |
| V-3 | 用户不重复说持仓，Agent 自己搜索 | V | ✓ | ✓ | ✓ | 90-120 |
| W-1 | 引导 SOP 完成后从 agent.md 自删 | W | ✓ | ✓ | — | 120-180 |
| X-1 | 每日收工汇报 SOP 全生命周期 | X | ✓ | ✓ | — | 120-180 |
| X-2 | 多步对话：描述→整理→确认→创建 | X | ✓ | ✓ | — | 90-120 |

---

## 四、运行命令

```bash
# Group U: P2 - SOP 技能路由（需要 sandbox）
pytest tests/integration/test_course22_cases.py -m "sandbox" -k "TestSOPSkillRouting" -v -s --timeout=300

# Group V: P3 - 持仓决策（需要 pgvector + sandbox）
pytest tests/integration/test_course22_cases.py -m "pgvector" -k "TestHoldingsDecision" -v -s --timeout=600

# Group W: P4 - 引导 SOP（需要 sandbox）
pytest tests/integration/test_course22_cases.py -m "sandbox" -k "TestOnboardingCompletion" -v -s --timeout=300

# Group X: P5 - SOP 调教（需要 sandbox）
pytest tests/integration/test_course22_cases.py -m "sandbox" -k "TestSOPTraining" -v -s --timeout=300

# 完整套件
pytest tests/integration/test_course22_cases.py -v -s --timeout=600

# 快速冒烟（只跑不依赖 sandbox 的基础验证）
pytest tests/integration/test_course22_cases.py -m "llm and not sandbox and not pgvector" -v -s
```

---

## 五、Group U：P2 — SOP 技能路由全链路

**课程 Case**（P2）：用户发一句话"帮我生成今天的投资早报" → XiaoPaw 直接调用已沉淀的 `investment-report/SKILL.md` → 按 SOP 执行，不需要用户再解释。

### 测试前置条件

- workspace 使用 `workspace-init/` 初始状态（含引导 SOP 的 agent.md，soul/user.md 为空）
- AIO-Sandbox 可达（skill-creator 需要 sandbox 写 SKILL.md）

### 测试用例详情

#### TC-U1：SOP 描述触发 skill-creator 并确认创建

**场景**：用户向 Agent 描述投资早报 SOP，要求保存为可复用技能。

**前置**：新 workspace，sandbox 可达。

**步骤**：
1. 发送：描述早报 SOP（每日行情、换手率、均线分析、输出格式）并要求保存为 `investment-report` 技能
2. 等待 Agent 调用 skill-creator
3. 断言：回复确认技能已创建，提及 `investment-report` 或 `SKILL.md`

**断言关键词**：`["创建", "技能", "skill", "investment-report", "SKILL", "保存", "已", "成功"]`

---

#### TC-U2：早报触发词自动路由到已创建技能

**场景**：TC-U1 创建技能后，发一句话"帮我生成今天的投资早报" → Agent 应路由到技能执行，不再要求解释 SOP。

**前置**：TC-U1 已执行，同一 `routing_key`，同一 session（无需 /new）。

**步骤**：
1. 继承 TC-U1 后的 session 状态（技能已创建）
2. 发送：`"帮我生成今天的投资早报"`
3. 断言：
   - 回复长度 > 50（说明 Agent 尝试执行而非只是确认）
   - 回复包含"早报"相关内容或执行步骤
   - 不包含"你能描述一下"、"请告诉我 SOP"等重新询问内容

**关键断言**：Agent **不重新询问** SOP，直接执行（体现"程序记忆"价值）

---

#### TC-U3：多种触发词均路由到同一技能

**场景**：早报技能的 description 应足够"pushy"，覆盖用户可能说的各种表达。

**前置**：技能已创建。

**步骤**：
1. 发送：`"今日行情怎么样？"`
2. 断言：Agent 调用了 investment-report 或给出行情分析
3. 发送（新轮次）：`"给我生成一份今日投资报告"`
4. 断言：同上

---

## 六、Group V：P3 — 持仓决策（搜索层 + 文件层协同）

**课程 Case**（P3）：用户问"阿里今天该不该挂单卖出" → XiaoPaw 判断需要历史信息 → 触发 search_memory → 从 pgvector 找到持仓记录 → 同时从 workspace 读取用户操作问题 → 给出综合建议。

**核心演示点**：用户**没有再次告诉** Agent 持仓。Agent 自己去记忆里找的。

### 测试前置条件

- pgvector 可达，`memories` 表存在
- AIO-Sandbox 可达（search_memory 需要 sandbox 访问 pgvector）
- 需要 QWEN_API_KEY

### 测试用例详情

#### TC-V1：持仓信息写入 pgvector 后，新 session 能检索到

**场景**：第一 session 告知持仓 → 等待 pgvector 异步索引 → /new 清空 context → 发决策问题 → Agent 从 pgvector 找到持仓。

**步骤**：
1. 发送：`"我持有阿里巴巴 2000 股，成本价 88 港元，持有 3 个月，打算长期持有"`
2. 等待 8 秒（pgvector 异步索引）
3. 发送：`"/new"`（清空 context 窗口，模拟跨 session）
4. 发送：`"根据我们之前讨论的持仓，阿里今天跌了 2%，我该继续持有还是减仓？"`
5. 断言：回复包含 `["阿里", "持仓", "88", "2000", "成本", "建议", "分析"]` 中的关键词

**核心验证**：第 4 步不重复说持仓，Agent 自己从 pgvector 找

---

#### TC-V2：决策分析同时引用 pgvector（持仓）和 workspace（操作问题）

**场景**：验证 P3 的核心亮点——"用你自己的复盘来说服你"。

**步骤**：
1. **预写 workspace**：向 `user.md` 写入「用户操作问题：容易在下跌时情绪化追单」（模拟 memory-save 已写入）
2. 发送：告知阿里持仓信息
3. 等待 8 秒
4. 发送：`"/new"`
5. 发送：`"阿里今天该不该挂单卖出？"`
6. 断言：
   - 回复包含持仓相关关键词（来自 pgvector）
   - 回复包含操作问题相关词如「情绪」「追单」「复盘」（来自 workspace）

**核心验证**：两层记忆在同一次回复中协同使用

---

#### TC-V3：用户不重复持仓，Agent 主动搜索（P3 核心演示点）

**场景**：最纯粹的 P3 演示——用户的问题里**没有任何持仓信息**，只有决策问题。

**步骤**：
1. 第一 session：告知持仓（腾讯 1000 股，成本 320 港元）
2. 等待索引
3. `"/new"`
4. 发送：`"腾讯今天该不该挂单卖出？"` ← 问题中无持仓信息
5. 断言：
   - 回复长度 > 30（非敷衍回复）
   - 不包含"您没有提供持仓信息"等无法回答的提示
   - 包含 `["腾讯", "持仓", "320", "成本", "建议", "分析", "仓"]` 等持仓相关词

**核心断言**：回复中**包含**历史持仓数据，证明 Agent 自行触发了 search_memory

---

## 七、Group W：P4 补充 — 引导 SOP 自删

**课程 Case**（P4）：初始引导完成后，Agent 调用 memory-save 将 agent.md 中的引导 SOP 整节移除（"使命终结，自我清除"）。

**Group T 已覆盖**：触发引导、跨 session 推进、打断后切换模式。
**本 Group 补充**：验证引导**完成后** agent.md 的内容变化。

### 测试用例详情

#### TC-W1：引导 SOP 完成后从 agent.md 自删

**场景**：完整走完 6 步引导流程，Agent 最终调用 memory-save 更新 agent.md，引导 SOP 节被移除。

**步骤**：
1. 全新 workspace（含引导 SOP 的 agent.md）
2. 快速走完引导：起名 → 用途 → 风格 → 用户信息 → 禁忌 → SOP 调教确认
3. 等待 Agent 自我清除写入
4. 读取 `workspace_dir / "agent.md"`
5. 断言：`"初始引导 SOP"` 或 `"引导进度"` **不再出现**在 agent.md 中

---

## 八、Group X：P5 — SOP 调教完整流程

**课程 Case**（P5）：用户用自然语言描述 SOP → Agent 确认结构化 → 调用 skill-creator → 技能注册到 `load_skills.yaml` → 下次直接触发。

### 测试用例详情

#### TC-X1：每日收工汇报 SOP 全生命周期（描述→创建→触发）

**场景**：从描述到使用的完整链路，对应课程 P5 四步流程。

**步骤**：
1. 发送：描述每日收工汇报 SOP（收工时列今日完成、明日计划、Blockers）
2. Agent 确认理解后（如果 Agent 先整理再确认，需要多轮）
3. 发送确认：`"好的，就按这个创建为 daily-summary 技能"`
4. 断言创建确认（关键词 `["daily-summary", "创建", "技能", "skill"]`）
5. **同一 session**，发送：`"收工"`
6. 断言：回复尝试执行收工汇报 SOP（提及今日完成、明日计划等结构）

---

#### TC-X2：SOP 调教中 Agent 主动整理结构化再确认

**场景**：验证 P5 第二步——"Agent 确认理解并结构化"，不是直接创建，而是先整理给用户确认。

**步骤**：
1. 发送：较为口语化的 SOP 描述（`"每次复盘操作时，先说哪里做对了，再说哪里做错了，最后总结一句话结论"`）
2. 断言第一轮回复：
   - 包含结构化整理（Agent 用清晰格式复述 SOP）
   - 包含确认请求（`["确认", "这样", "对吗", "是否", "?", "？"]`）
3. 发送确认：`"对，保存为 investment-review 技能"`
4. 断言：确认创建（关键词 `["investment-review", "创建", "保存"]`）

---

## 九、测试隔离策略

| 策略 | 实现 |
|------|------|
| **路由键隔离** | 每个测试用例使用唯一 `routing_key`（包含 `uuid.hex[:8]`） |
| **workspace 隔离** | 每个 fixture 在 `tmp_path` 下创建独立 workspace |
| **pgvector 隔离** | 用唯一 routing_key 区分记录，测试结束不清理（幂等插入） |
| **fixture 生命周期** | 所有 TestClient fixture 作用域为 function 级（每测试独立） |
| **sandbox 跳过** | `if not sandbox_available: pytest.skip(...)` |
| **pgvector 跳过** | `memory_client_pgvector` fixture 在不可达时自动 skip |

---

## 十、与现有测试的关系

| 现有文件 | 覆盖内容 | 本文件补充 |
|---------|---------|-----------|
| `test_memory_system.py` Group P | Bootstrap 注入 | — |
| `test_memory_system.py` Group Q | ctx.json 持久化 | — |
| `test_memory_system.py` Group R | memory-save / skill-creator 触发 | **U：skill创建后立即触发验证** |
| `test_memory_system.py` Group S | pgvector 索引 + search_memory | **V：阿里挂单具体场景 + 两层协同** |
| `test_memory_system.py` Group T | 初始引导触发 + 跨轮推进 | **W：引导完成后 agent.md 自删** |
| `test_memory_e2e.py` | Bootstrap 截断纯函数 | — |
