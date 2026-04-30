# 系统级测试用例设计：evopaw-with-memory

> **状态说明（2026-04-22）**：本文档描述的场景中部分（L20/L21、file pipeline、course22 case）依赖已移除的 AIO-Sandbox + `main_crew` 架构，相关测试已归档到 `tests/archive/legacy_crewai/`。
> 本文档作为**场景清单参考**保留，不代表当前测试套件的实际可运行范围。
>
> **设计视角**：用户如何把一个空白助手，变成懂自己的私人助手。
>
> **全局前提**：
> - `ANTHROPIC_API_KEY` 有效（Claude Agent SDK）
> - `QWEN_API_KEY` 有效（记忆摘要/向量化）、`MEMORY_DB_DSN` 指向本地 pgvector
> - pgvector 容器运行：`docker compose -f pgvector-docker-compose.yaml up -d`
> - 无 Mock：真实 LLM + 真实数据库

---

## 用户旅程地图

```
阶段 1：初次见面
  用户拿到助手 → 空白 workspace → 助手不认识用户
  → 用户介绍自己 → 助手能在当前会话用到，但重启后遗忘

阶段 2：建立记忆（调教期）
  用户要求助手"记住我" → memory-save 写入 workspace 文件
  → 重启后 Bootstrap 注入 → 助手真的记住了

阶段 3：固化工作流（SOP 沉淀）
  用户有重复性任务 → 要求助手固化为 Skill
  → skill-creator 创建 SKILL.md
  → 以后直接调用，不用反复描述

阶段 4：日常使用（记忆累积）
  日常对话 → ctx.json 跨 session 恢复上下文
  → pgvector 索引所有对话 → 可以搜索历史

阶段 5：记忆维护（治理期）
  记忆文件越来越大 → memory-governance 审计清理
  长对话不撑爆 → prune + compress 自动工作
```

---

## 场景 1：初次见面——空白助手

> 用户拿到全新 XiaoPaw，workspace 为空，没有任何个人信息。

---

### TC-1.1：第一次问候——助手不认识我，但能正常交流

**用户故事**：晓寒拿到新部署的 XiaoPaw，什么都没配置，发了第一条消息。

**前置状态**：
```python
# workspace 中无任何 .md 文件（纯空目录）
# ctx_dir 中无 ctx.json（全新 session）
workspace_dir = tmp_path / "blank_workspace"
workspace_dir.mkdir()
```

**测试步骤**：
1. 发送消息："你好，我是晓寒"

**预期结果**：
- HTTP 200，`reply` 非空
- 回复中有正常的问候内容
- 回复中**不出现**用户名"晓寒"（没有任何先验知识）
- 系统没有崩溃（bootstrap 容错生效）

**验证的核心机制**：Bootstrap 容错（文件缺失静默跳过）

**标记**：`@pytest.mark.llm`

---

### TC-1.2：空白助手介绍自己的能力

**用户故事**：晓寒想知道这个助手能做什么。

**前置状态**：同 TC-1.1（空白 workspace）

**测试步骤**：
1. 发送消息："你能帮我做什么？"

**预期结果**：
- 回复中包含 Skills 相关能力介绍（来自 agent.md 默认内容，或 LLM 自身知识）
- 但回复**不包含**任何关于"晓寒"或其工作的个性化信息

**标记**：`@pytest.mark.llm`

---

### TC-1.3：首轮对话后，ctx.json 自动写入

**用户故事**：用户不需要做任何操作，系统自动保存对话。

**前置状态**：空白 workspace + 空 ctx_dir

**测试步骤**：
1. 发送任意一条消息，等待回复
2. 检查文件系统

**预期结果**：
- `ctx_dir/{session_id}_ctx.json` 文件自动生成
- `ctx_dir/{session_id}_raw.jsonl` 文件自动生成
- ctx.json 是合法 JSON 列表，包含该轮 user/assistant 消息

**验证的核心机制**：run_and_index() 的自动持久化

**标记**：`@pytest.mark.llm`

---

## 场景 2：建立记忆——让助手认识我

> 用户主动教助手记住自己的信息，下次对话时助手还记得。

---

### TC-2.1：告诉助手自己的名字——下次 Session 还记得

**用户故事**：晓寒让助手记住自己的名字，关闭再开对话后，助手应该认识他。

**前置状态**：
```python
# 标准初始 workspace（soul/user/agent/memory.md 来自 workspace-init/）
# 空 ctx_dir（新 Session）
session_id_1 = "session-introduce"
session_id_2 = "session-next-day"  # 模拟第二天重新对话
```

**测试步骤**：
```
Session 1（session_id_1）：
  Step 1: 发送 "帮我记住：我叫晓寒，是一名 AI 课程讲师"
  Step 2: 等待回复（含 memory-save Skill 调用）
  Step 3: 检查 workspace/user.md 是否包含"晓寒"

Session 2（session_id_2，全新 session，无 ctx.json）：
  Step 4: 发送 "你认识我吗？"
  Step 5: 等待回复
```

**预期结果**：
- Step 2：回复中提到"已记住"或"已保存"
- Step 3：`user.md` 包含"晓寒"和"AI课程讲师"
- Step 5：回复中出现"晓寒"（Bootstrap 从 user.md 读取，跨 session 生效）

**验证的核心机制**：memory-save Skill → user.md → Bootstrap 注入 → 新 Session 感知

**标记**：`@pytest.mark.llm`, `@pytest.mark.sandbox`

---

### TC-2.2：告诉助手工作偏好——以后行为自动适配

**用户故事**：晓寒告诉助手他喜欢简洁的回复风格，以后助手应该自动遵守。

**前置状态**：
```python
# user.md 已有基本信息（晓寒，AI讲师）
# 但未记录回复偏好
```

**测试步骤**：
```
Session 1：
  Step 1: 发送 "帮我记住：我不喜欢长篇大论，回复控制在3句话以内"
  Step 2: 等待回复
  Step 3: 检查 user.md 更新

Session 2（新 session）：
  Step 4: 发送一个需要较长解释的问题："Python 和 Go 哪个适合做 AI 应用？"
  Step 5: 检查回复长度
```

**预期结果**：
- Step 3：`user.md` 包含"3句话"或"简洁"相关内容
- Step 5：回复不超过 6 句话（助手从 Bootstrap 读到了偏好约束）

**标记**：`@pytest.mark.llm`, `@pytest.mark.sandbox`

---

### TC-2.3：告诉助手当前项目——搜索历史时可以召回

**用户故事**：晓寒告诉助手自己在做的项目，一周后想搜索相关历史。

**前置状态**：
```python
# 干净的 memories 表
# routing_key = "p2p:ou_project_test"
```

**测试步骤**：
```
Session 1：
  Step 1: 发送 "我在开发一个叫 XiaoPaw 的飞书 AI 助手，核心功能是三层记忆"
  Step 2: 等待 5s（pgvector 索引写入）
  Step 3: 直接查询 memories 表，确认写入

Session 2（新 session_id，同 routing_key）：
  Step 4: 发送 "帮我搜索我之前说过的项目信息"
  Step 5: 等待回复（search_memory Skill 被调用）
```

**预期结果**：
- Step 3：`memories` 表有 `routing_key='p2p:ou_project_test'` 的记录
- Step 5：回复中出现"XiaoPaw"或"三层记忆"（语义搜索命中）
- `skills_called` 包含 `search_memory`

**验证的核心机制**：async_index_turn → pgvector → search_memory Skill

**标记**：`@pytest.mark.llm`, `@pytest.mark.sandbox`

---

## 场景 3：固化工作流——把重复动作变成技能

> 用户有一件经常做的事情，想让助手固化为可复用的 Skill。

---

### TC-3.1：用户描述 SOP → 助手创建新 Skill

**用户故事**：晓寒每周要生成课程进度报告，想让助手学会这个流程。

**前置状态**：
```python
# 确保 evopaw/skills/ 中不存在 "weekly-report" skill
assert not (skills_dir / "weekly-report").exists()
```

**测试步骤**：
1. 发送消息：
   ```
   每周我需要做一个课程进度报告，步骤是：
   1. 读取 /workspace/course-status.md 里的课程进度
   2. 统计已完成课数和待完成课数
   3. 生成一个 Markdown 格式的报告，存到 /workspace/reports/weekly_{日期}.md

   帮我把这个流程做成一个 Skill，叫 weekly-report
   ```
2. 等待回复（最长 180s，含 skill-creator Sub-Crew）
3. 检查文件系统

**预期结果**：
- 回复中提到"Skill 已创建"或"weekly-report 已生成"
- `evopaw/skills/weekly-report/SKILL.md` 文件存在
- SKILL.md 包含 `name: weekly-report`
- SKILL.md 包含对三个步骤的描述

**验证的核心机制**：skill-creator Skill → /mnt/skills/weekly-report/SKILL.md

**标记**：`@pytest.mark.llm`, `@pytest.mark.sandbox`

---

### TC-3.2：新 Skill 创建后立即可用

**用户故事**：TC-3.1 创建了 weekly-report Skill，现在用户想直接调用它。

**前置状态**：TC-3.1 执行完毕，`weekly-report/SKILL.md` 存在；同时在 workspace 中准备好 course-status.md：
```python
(workspace_dir / "course-status.md").write_text(
    "# 课程进度\n已完成：22课\n待完成：18课"
)
```

**测试步骤**：
1. 发送消息（新 session）："帮我生成本周的课程进度报告"
2. 等待回复（最长 120s）
3. 检查 `data/workspace/reports/` 目录

**预期结果**：
- 回复中提到报告已生成或提供了报告文件路径
- `data/workspace/reports/` 目录中存在本周的报告文件（或回复直接包含报告内容）

**标记**：`@pytest.mark.llm`, `@pytest.mark.sandbox`

---

## 场景 4：日常使用——跨天对话不失忆

> 用户每天都和助手交流，助手应该记住昨天、上周的事情。

---

### TC-4.1：昨天告诉助手的事，今天还记得

**用户故事**：晓寒昨天告诉助手"第22课准备周五发布"，今天想确认这件事。

**前置状态**：
```python
# 直接构造"昨天的 ctx.json"（模拟上一次 session 结束后的状态）
session_id = "evopaw-daily-user"
ctx_content = [
    {"role": "system", "content": "你是XiaoPaw"},   # 旧 backstory（会被过滤）
    {"role": "user",   "content": "第22课准备周五发布"},
    {"role": "assistant", "content": "好的，我记住了，第22课计划周五发布。"},
    {"role": "user",   "content": "帮我记到 memory.md 里"},
    {"role": "assistant", "content": "已记录。"},
]
# 写入 ctx.json
(ctx_dir / f"{session_id}_ctx.json").write_text(
    json.dumps(ctx_content, ensure_ascii=False)
)
```

**测试步骤**：
1. 用相同 `session_id` 发送消息："我上次说第22课什么时候发？"
2. 等待回复

**预期结果**：
- 回复中包含"周五"（从 ctx.json 恢复的历史中读到）
- **不需要**用户重新告知

**验证的核心机制**：_restore_session → ctx.json 恢复 → LLM 感知历史

**标记**：`@pytest.mark.llm`

---

### TC-4.2：两周前的对话——ctx.json 已压缩，但核心信息仍可搜索

**用户故事**：两周前晓寒和助手讨论过一个技术方案，现在想找回来。ctx.json 经过多次压缩，原始内容已被 summary 替代，但 pgvector 里有索引。

**前置状态**：
```python
# 直接向 pgvector 插入两周前的对话记录
# 使用真实 embed_texts 生成向量
two_weeks_ago_ts = int((time.time() - 14 * 86400) * 1000)
INSERT INTO memories VALUES (
    ..., routing_key='p2p:ou_daily_user',
    turn_ts={two_weeks_ago_ts},
    user_msg='Redis 缓存方案用 String 还是 Hash 类型存用户会话？',
    assistant_reply='建议用 Hash 类型，因为可以只更新单个字段，避免序列化整个对象...',
    summary='讨论了 Redis 缓存方案，结论是用 Hash 类型存用户会话'
)
```

**测试步骤**：
1. 发送消息（新 session，routing_key=ou_daily_user）："我之前问过 Redis 存用户会话的问题，结论是什么来着？"
2. 等待回复（search_memory Skill 被触发）

**预期结果**：
- 回复中包含"Hash"或"Hash类型"（语义搜索命中两周前的记录）
- `skills_called` 包含 `search_memory`

**标记**：`@pytest.mark.llm`, `@pytest.mark.sandbox`

---

### TC-4.3：同一会话中多轮连续追问——上下文不丢失

**用户故事**：晓寒在一次工作会话中连续追问，助手应该保持上下文连贯性。

**前置状态**：
```python
routing_key = "p2p:ou_continuous_chat"
# 每次 send_message 用同一 routing_key，session_mgr 会保持同一 session
```

**测试步骤**：
```
第 1 轮: "我在设计一个多 Agent 系统，用于自动生成周报"
第 2 轮: "这个系统需要哪些 Agent？"
第 3 轮: "其中负责数据收集的 Agent 应该用什么工具？"
第 4 轮: "好，那第一个 Agent 叫什么来着？"  ← 考验上下文
```

**预期结果**：
- 第 2 轮：回复中提到多个 Agent（如数据收集、报告生成等）
- 第 3 轮：回复具体描述数据收集 Agent 的工具
- 第 4 轮：回复中出现第 2 轮提到的具体 Agent 名字（无需用户重复）

**标记**：`@pytest.mark.llm`

---

### TC-4.4：当天对话超过 20 轮——长会话不撑爆

**用户故事**：晓寒某天工作特别多，和助手连续对话了 20+ 轮，系统应该自动压缩，用户感知不到。

**前置状态**：
```python
routing_key = "p2p:ou_long_session"
# 每轮消息约 300 字符
```

**测试步骤**：
1. 连续发送 22 条消息（模拟一整天的工作对话）
2. 每条消息主题各不同（避免 LLM 直接 cache）
3. 第 22 条发送："把我们今天聊的主要内容总结一下"

**预期结果**：
- 全部 22 条消息均返回 HTTP 200，无 token 超限报错
- 第 22 条的回复中能总结出至少 3 个之前讨论过的主题
- ctx.json 文件大小 < 200KB（压缩有效，不无限增长）
- `raw.jsonl` 每轮都有追加记录（完整历史存档）

**验证的核心机制**：prune_tool_results + maybe_compress 协同工作

**标记**：`@pytest.mark.llm`（耗时较长，约 5-10 分钟）

---

## 场景 5：记忆维护——防止助手"脑子越来越乱"

> 随着使用时间增长，记忆文件积累了很多内容，需要定期清理整理。

---

### TC-5.1：memory.md 指向了不存在的文件——governance 发现并报告

**用户故事**：晓寒的 memory.md 里有几条指向已删除文件的死链，运行 governance 后发现问题。

**前置状态**：
```python
# memory.md 中有死链
(workspace_dir / "memory.md").write_text("""
# XiaoPaw 记忆索引

## 用户偏好
→ 详见：[topics/coding-style.md](./topics/coding-style.md)

## 项目进度
→ 详见：[topics/evopaw-project.md](./topics/evopaw-project.md)
""")
# topics/ 目录不存在
```

**测试步骤**：
1. 发送消息："帮我检查一下记忆文件的健康状态，看看有没有失效的链接"
2. 等待回复（最长 120s）

**预期结果**：
- 回复中明确提到存在死链（文件不存在）
- 具体列出了哪些文件找不到（`coding-style.md` 或 `evopaw-project.md`）

**标记**：`@pytest.mark.llm`, `@pytest.mark.sandbox`

---

### TC-5.2：memory.md 超过 200 行——Bootstrap 自动截断，不影响运行

**用户故事**：用户在 memory.md 里积累了大量记忆，超过 200 行，但系统应该自动截断，不崩溃。

**前置状态**：
```python
# 生成 300 行 memory.md
# 第 50 行有 EARLY_MARKER（应注入）
# 第 250 行有 LATE_MARKER（不应注入）
lines = [f"- 记忆条目 {i:03d}" for i in range(300)]
lines[49]  = "- EARLY_MARKER: 早期重要信息"
lines[249] = "- LATE_MARKER: 超出截断线的信息"
(workspace_dir / "memory.md").write_text("\n".join(lines))
```

**测试步骤**：
1. 调用 `build_bootstrap_prompt(workspace_dir)`
2. 直接检查返回的字符串（不需要 LLM）

**预期结果**：
- 返回字符串包含 `EARLY_MARKER`
- 返回字符串不包含 `LATE_MARKER`

**标记**：无（纯函数，最快）

---

### TC-5.3：Skill 目录越来越多——助手仍能正确选择工具

**用户故事**：用户创建了多个自定义 Skill 后，助手面对一个新任务时，仍能找到最合适的 Skill。

**前置状态**：
```python
# skills 目录已有多个 Skill：
# - weekly-report（课程报告）
# - daily-standup（每日站会）
# - expense-report（费用报销）
# 用户发送一个明确对应 weekly-report 的请求
```

**测试步骤**：
1. 确保上述 3 个 Skill 的 SKILL.md 存在（可从 TC-3.1 延续，或手动创建 stub）
2. 发送消息："帮我出一个课程进度总结"
3. 等待回复

**预期结果**：
- `skills_called` 包含 `weekly-report`
- 不应触发 `expense-report` 或 `daily-standup`

**标记**：`@pytest.mark.llm`, `@pytest.mark.sandbox`

---

## 场景 6：系统级健壮性——异常和边界

> 以上所有场景的"出错了会怎样"。

---

### TC-6.1：pgvector 不可达时，对话仍然正常

**用户故事**：DBA 临时重启了数据库，但助手不应因此挂掉。

**前置状态**：
```python
# 传入一个无法连接的 db_dsn
db_dsn = "postgresql://evopaw:wrong@localhost:15432/evopaw_memory"
```

**测试步骤**：
1. 以无效 `db_dsn` 初始化 `build_agent_fn`
2. 发送普通消息："你好"
3. 等待回复

**预期结果**：
- 返回 HTTP 200，`reply` 是正常的问候回复
- 索引写入失败，但不影响对话本身（async_index_turn 在后台静默失败）

**验证的核心机制**：indexer 异常不传播到主流程

**标记**：`@pytest.mark.llm`

---

### TC-6.2：新 session 的 user 消息不被旧 session 的 system backstory 污染

**用户故事**：两个用户共用同一个 XiaoPaw，各自的 session 不应互相干扰。

**前置状态**：
```python
# user_A 的 ctx.json 包含"你是张三的专属助手"的旧 backstory
# user_B 是全新用户，没有 ctx.json
session_id_a = "session-user-a"
session_id_b = "session-user-b"
ctx_content_a = [
    {"role": "system", "content": "你是张三的专属助手，不许回答其他人"},
    {"role": "user",   "content": "我叫张三"},
    {"role": "assistant", "content": "你好张三"},
]
(ctx_dir / f"{session_id_a}_ctx.json").write_text(
    json.dumps(ctx_content_a, ensure_ascii=False)
)
```

**测试步骤**：
1. 以 `session_id_b` 发送消息："你好，我是李四"
2. 检查 `context.messages`（通过 hook 插桩观察）

**预期结果**：
- session_b 的 context 中无任何 session_a 的内容
- 回复中不出现"张三"
- 回复正常问候"李四"

**标记**：`@pytest.mark.llm`

---

### TC-6.3：Bot 重启后，用相同 routing_key 发消息——ctx 正确恢复

**用户故事**：晓寒关掉了 XiaoPaw 后重新启动，继续上次的对话。

**前置状态**：
```python
# 模拟"上次进程"：构造已有的 ctx.json
session_id = "persist-session"
ctx_content = [
    {"role": "system", "content": "你是XiaoPaw"},  # 旧 backstory，应被过滤
    {"role": "user",   "content": "帮我把 PR #42 的代码审查一遍"},
    {"role": "assistant", "content": "好的，PR #42 已完成审查，主要问题：缺少错误处理。"},
]
(ctx_dir / f"{session_id}_ctx.json").write_text(
    json.dumps(ctx_content, ensure_ascii=False)
)
```

**测试步骤**：
1. 以全新 MemoryAwareCrew 实例（模拟重启后新进程）
2. 用相同 `session_id` 发送消息："我们刚才说的 PR，结论是什么？"
3. 等待回复

**预期结果**：
- 回复中出现"PR #42"或"缺少错误处理"（ctx.json 恢复成功）
- 回复中的 system backstory 来自最新的 soul.md（不含旧 backstory 内容）

**验证的核心机制**：_restore_session 的系统消息过滤逻辑（H3 修复）

**标记**：`@pytest.mark.llm`

---

## 测试基础设施

### conftest.py 需新增 Fixtures

```python
@pytest.fixture
async def memory_client(tmp_path, qwen_api_key, sandbox_available, session_mgr):
    """完整三层记忆 E2E 客户端。"""
    import shutil, os
    from evopaw.agents.main_crew import build_agent_fn

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    ctx_dir = tmp_path / "ctx"
    ctx_dir.mkdir()

    # 从 workspace-init/ 复制初始文件（提供合理的 soul/user/agent/memory.md）
    init_dir = Path(__file__).parents[2] / "workspace-init"
    for f in init_dir.glob("*.md"):
        shutil.copy(f, workspace_dir / f.name)

    db_dsn = os.getenv("MEMORY_DB_DSN", "")
    sender = CaptureSender()
    agent_fn = build_agent_fn(
        sender=sender,
        workspace_dir=workspace_dir,
        ctx_dir=ctx_dir,
        db_dsn=db_dsn,
        max_history_turns=20,
        sandbox_url=SANDBOX_URL if sandbox_available else "",
    )
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        idle_timeout=30.0,
    )
    app = create_test_app(
        runner=runner, sender=sender,
        session_mgr=session_mgr, workspace_dir=workspace_dir,
    )
    async with TestClient(TestServer(app)) as cli:
        cli._workspace_dir = workspace_dir  # 方便测试用例读取文件
        cli._ctx_dir = ctx_dir
        yield cli
    await runner.shutdown()


@pytest.fixture
def pgvector_available():
    dsn = os.getenv("MEMORY_DB_DSN", "")
    if not dsn:
        pytest.skip("MEMORY_DB_DSN 未配置")
    return dsn
```

### 用例矩阵总览

| 用例 | 用户故事 | LLM | Sandbox | pgvector | 预计耗时 |
|------|---------|:---:|:-------:|:--------:|---------|
| TC-1.1 | 初次见面，空白助手 | ✅ | ❌ | ❌ | ~15s |
| TC-1.2 | 问助手能做什么 | ✅ | ❌ | ❌ | ~15s |
| TC-1.3 | ctx.json 自动写入 | ✅ | ❌ | ❌ | ~15s |
| TC-2.1 | 让助手记住名字，下次还记得 | ✅ | ✅ | ❌ | ~60s |
| TC-2.2 | 记住偏好，行为自动适配 | ✅ | ✅ | ❌ | ~60s |
| TC-2.3 | 记住项目，可搜索历史 | ✅ | ✅ | ✅ | ~60s |
| TC-3.1 | 描述 SOP → 创建新 Skill | ✅ | ✅ | ❌ | ~120s |
| TC-3.2 | 新 Skill 立即可调用 | ✅ | ✅ | ❌ | ~60s |
| TC-4.1 | 昨天的事今天还记得（ctx 恢复） | ✅ | ❌ | ❌ | ~20s |
| TC-4.2 | 两周前的事可以搜索 | ✅ | ✅ | ✅ | ~60s |
| TC-4.3 | 同 Session 多轮追问 | ✅ | ❌ | ❌ | ~90s |
| TC-4.4 | 22 轮长会话不撑爆 | ✅ | ❌ | ❌ | ~600s |
| TC-5.1 | 死链被 governance 发现 | ✅ | ✅ | ❌ | ~60s |
| TC-5.2 | memory.md 超 200 行截断 | ❌ | ❌ | ❌ | <1s |
| TC-5.3 | 多 Skill 时正确选择工具 | ✅ | ✅ | ❌ | ~60s |
| TC-6.1 | pgvector 不可达时对话不挂 | ✅ | ❌ | ❌ | ~20s |
| TC-6.2 | 多用户 session 不互相污染 | ✅ | ❌ | ❌ | ~20s |
| TC-6.3 | 重启后 ctx 正确恢复 | ✅ | ❌ | ❌ | ~20s |

### 运行命令

```bash
# 快速冒烟（无 Sandbox/pgvector，约 5 分钟）
pytest tests/integration/test_memory_e2e.py -m "llm and not sandbox" -v -s --timeout=120

# 含 Sandbox（约 15 分钟）
pytest tests/integration/test_memory_e2e.py -m "llm and sandbox" -v -s --timeout=300

# 完整套件（约 30 分钟）
pytest tests/integration/test_memory_e2e.py -v -s --timeout=700
```
