# EvoPaw Harness 改进方案

> 基于 [Agentic Harness Patterns](https://github.com/keli-wen/agentic-harness-patterns-skill) 六层模式，对照 EvoPaw 现状，制定系统性改进路线图。

**参考框架**: 从 Claude Code 512K 行源码中蒸馏出的六层生产级 Agent Harness 模式：
1. Memory（记忆）
2. Skills（技能）
3. Tools & Safety（工具与安全）
4. Context Engineering（上下文工程）
5. Multi-agent Coordination（多 Agent 协调）
6. Lifecycle & Extensibility（生命周期与扩展性）

---

## 目录

- [现状评估总览](#现状评估总览)
- [改进一：记忆系统升级](#改进一记忆系统升级)
- [改进二：Skills 系统优化](#改进二skills-系统优化)
- [改进三：工具安全与权限](#改进三工具安全与权限)
- [改进四：上下文工程](#改进四上下文工程)
- [改进五：多 Agent 协调](#改进五多-agent-协调)
- [改进六：生命周期与扩展性](#改进六生命周期与扩展性)
- [实施优先级与路线图](#实施优先级与路线图)

---

## 现状评估总览

| Harness 层 | Harness 黄金法则 | EvoPaw 现状 | 差距 |
|---|---|---|---|
| Memory | 分离 instruction / auto / extraction 三层 | L1(bootstrap) + L2(ctx.json) + L3(pgvector) 三层，但 auto-memory 无写入通道 | ⚠ 中等 |
| Skills | 懒加载 + 预算受限发现 | 渐进式披露 + SKILL.md frontmatter 描述 | ✅ 基本对齐 |
| Tools & Safety | 默认 fail-closed + 权限门控 | `bypassPermissions` 全开，无安全分类 | 🔴 缺失 |
| Context | 预算思维——每个 token 必须赢得位置 | 有三把剪刀(prune/chunk/compress)，但缺乏恢复指针和硬限制 | ⚠ 中等 |
| Multi-agent | 协调者综合而非委派理解 | Main → Sub-Agent 零继承模式，但仅支持 Coordinator 一种 | ⚠ 中等 |
| Lifecycle | 单一调度 hook + 类型化任务 + 依赖排序启动 | 仅 verbose hooks，无完整 hook 体系 | ⚠ 中等 |

---

## 改进一：记忆系统升级

### 1.1 现状分析

**EvoPaw 当前三层**:

| 层级 | 实现 | 对应 Harness 概念 |
|------|------|-------------------|
| L1 Bootstrap | `soul.md` / `user.md` / `agent.md` / `memory.md` → system_prompt | ≈ Instruction Memory |
| L2 Context | `ctx.json` 压缩快照 + `raw.jsonl` 审计 | ≈ 运行时压缩上下文（不完全是 auto-memory） |
| L3 Vector | pgvector 异步索引 + `search_memory` Skill 搜索 | ≈ Session Extraction（写入端） |

**缺口**:

1. **Auto-memory 写入通道缺失** — `memory-save` Skill 有 SKILL.md（6400 字）但**无执行脚本**。Agent 无法主动将学到的信息持久化到 `memory.md`
2. **无 index + topic 分层** — `memory.md` 是扁平文件，200 行硬限制后直接截断，无法按需加载详细 topic
3. **无后台 session 提取** — 没有 session 结束后异步提取记忆的机制
4. **无跨层晋升机制** — 记忆只在本层流动，不能从 L3 vector 晋升到 L1 bootstrap

### 1.2 改进方案

#### 1.2.1 实现 memory-save Skill（P0）

**目标**: 让 Agent 能主动保存记忆到 `memory.md`。

**实现路径**: `evopaw/skills/memory-save/scripts/save.py`

```python
# 核心逻辑伪代码
def save_memory(category: str, content: str, workspace_dir: str):
    """
    category: user | feedback | project | reference（四类型 auto-memory）
    content: 要保存的记忆内容
    """
    memory_path = Path(workspace_dir) / "memory.md"

    # 1. 读取现有内容
    existing = memory_path.read_text() if memory_path.exists() else ""

    # 2. 查重——避免保存已有内容
    if content_already_exists(existing, content):
        return "记忆已存在，跳过保存"

    # 3. 按 category 追加到对应 section
    updated = append_to_section(existing, category, content)

    # 4. 检查 200 行上限——超出时提示需要清理
    if line_count(updated) > 200:
        return "记忆索引已达上限，请先使用 memory-governance 清理"

    # 5. 原子写入
    memory_path.write_text(updated)
    return f"已保存到 [{category}] 分类"
```

**设计要点**:
- 四类型分类对齐 Harness 模式：`user`（用户偏好）、`feedback`（行为反馈）、`project`（项目上下文）、`reference`（稳定参考）
- **不保存可从代码库派生的内容**——这是 Harness 的明确原则
- 200 行上限与 `bootstrap.py:_MEMORY_MAX_LINES` 对齐

#### 1.2.2 引入 index + topic 分层（P1）

**目标**: memory.md 作为轻量 index（指针目录），详细内容存入独立 topic 文件，按需加载。

**目录结构变更**:

```
workspace/
├── memory.md              # index：200 行以内，只包含指针
└── memory/
    ├── user-profile.md    # topic：用户画像详情
    ├── project-arch.md    # topic：项目架构决策
    ├── tool-patterns.md   # topic：工具使用偏好
    └── ...
```

**memory.md index 格式**:

```markdown
## 用户偏好
- 偏好中文沟通 → [详情](memory/user-profile.md)
- 代码风格：black + ruff

## 项目上下文
- EvoPaw 架构：Claude Agent SDK 二层 Agent → [详情](memory/project-arch.md)

## 工具反馈
- 投资报告生成必须含数据来源链接
```

**两步保存不变量**（对齐 Harness）:
1. 先写 topic 文件
2. 再更新 index

崩溃最坏结果是孤立的 topic 文件（index 不指向），而不是 index 指向不存在的文件。

**代码变更**: `bootstrap.py` 不变（仍只读 memory.md 前 200 行）。在 `skill_loader.py` 的 reference 类型处理中，当 Agent 需要详情时，读取对应 topic 文件。

#### 1.2.3 后台 session 提取（P2）

**目标**: session 结束后异步提取关键记忆，写入 auto-memory。

**触发点**: `runner.py` worker 空闲超时退出时。

```python
# runner.py _worker() 超时退出分支
except asyncio.TimeoutError:
    # 现有清理逻辑...

    # 新增：后台提取记忆
    if self._memory_extractor:
        asyncio.create_task(
            self._memory_extractor.extract(
                session_id=last_session_id,
                routing_key=key,
            )
        )
```

**提取器设计**:
- fork 一个受限子 Agent（Haiku），只给 Read 权限
- 输入：session 的 `raw.jsonl` 完整历史
- 输出：结构化记忆条目（category + content）
- 写入：调用 memory-save 逻辑（复用，不走 MCP）
- **互斥保证**: 同一 session 同时只有一个写者

#### 1.2.4 跨层晋升审计（P3）

**目标**: 提供 `/remember` 命令，审计所有记忆层并提出晋升建议。

```
/remember → 扫描 L3 vector 高频命中条目
         → 比对 L1 index 是否已包含
         → 提出晋升建议（但不自动应用）
         → 用户确认后写入 memory.md
```

**作为 Slash 命令添加到 `runner.py`**:
```python
_SLASH_COMMANDS = frozenset({"/new", "/verbose", "/help", "/status", "/remember"})
```

---

## 改进二：Skills 系统优化

### 2.1 现状分析

**已对齐 Harness 的部分**:
- ✅ 渐进式披露（元数据 + 按需加载完整指令）
- ✅ YAML frontmatter 包含 name/description/type
- ✅ 描述截断到 200 字符
- ✅ `load_skills.yaml` 单一清单
- ✅ 路径遍历防护

**差距**:

| Harness 原则 | EvoPaw 现状 | 差距 |
|---|---|---|
| 元数据单一真相源（含 trigger hints） | frontmatter 只有 name/description/type | 缺少 trigger hints |
| 发现列表预算 ~1% 上下文窗口 | 无预算计算，Skill 数量增长时可能超限 | 无预算感知 |
| 四源发现（bundled/user/project/plugin） | 仅 bundled 一种来源 | 单来源 |
| 优雅降级（裁剪策略） | 无降级——要么全部展示，要么报错 | 无降级 |
| 内联 vs 隔离执行选择 | 硬编码：reference=内联, task=隔离 | 无运行时选择 |

### 2.2 改进方案

#### 2.2.1 扩展 SKILL.md frontmatter（P1）

**当前格式**:
```yaml
---
name: feishu_ops
description: 飞书平台操作工具集
type: task
version: "1.0"
---
```

**目标格式**:
```yaml
---
name: feishu_ops
description: 飞书平台操作工具集
type: task
version: "1.0"
triggers:                          # 新增：触发提示
  - "发飞书"
  - "创建文档"
  - "读取表格"
  - "发送消息"
execution_mode: isolated           # 新增：isolated(默认) | inline
allowed_tools:                     # 新增：Sub-Agent 工具白名单
  - Bash
  - Read
  - Write
---
```

**代码变更**: `skill_loader.py` 的 `_extract_frontmatter_description` 扩展为 `_extract_frontmatter_metadata`，返回完整元数据字典。

#### 2.2.2 发现列表预算控制（P1）

**Harness 原则**: 发现列表占 ~1% 上下文窗口（约 2000 token for 200K window）。

**实现**: 在 `_build_description_xml()` 中添加预算检查：

```python
_DISCOVERY_BUDGET_TOKENS = 2000  # ~1% of context window

def _build_description_xml(registry, session_id):
    budget_remaining = _DISCOVERY_BUDGET_TOKENS
    xml_parts = ["<available_skills>"]

    for name, info in registry.items():
        # 估算这个条目的 token 消耗
        desc = info.get("description", "")
        triggers = info.get("triggers", [])

        # 触发语言前置（Harness: 尾部会被裁剪，所以触发词放前面）
        entry_text = f"{', '.join(triggers)} | {desc}" if triggers else desc
        entry_tokens = len(entry_text) // 2

        if budget_remaining < entry_tokens:
            # 优雅降级：只保留名称
            xml_parts.append(f'  <skill><name>{name}</name></skill>')
            budget_remaining -= len(name) // 2
        else:
            xml_parts.append(
                f'  <skill>\n'
                f'    <name>{name}</name>\n'
                f'    <type>{info["type"]}</type>\n'
                f'    <description>{entry_text[:250]}</description>\n'
                f'  </skill>'
            )
            budget_remaining -= entry_tokens

        if budget_remaining <= 0:
            xml_parts.append('  <!-- 预算已用尽，更多 Skill 已省略 -->')
            break

    xml_parts.append("</available_skills>")
    return "\n".join(xml_parts)
```

#### 2.2.3 用户自定义 Skill 来源（P2）

**目标**: 支持项目级 Skill（workspace 内），除了内置 Skill。

**实现**: `load_skills.yaml` 支持多来源：

```yaml
sources:
  - type: bundled          # 内置
    path: evopaw/skills/
    priority: 100          # 高优先级
  - type: project          # 项目级
    path: workspace/skills/
    priority: 50
```

**去重**: 使用 Harness 的 Realpath 去重——通过规范路径去重，防止同名 Skill 加载两次。优先级高的覆盖低的。

#### 2.2.4 补全缺失 Skill 脚本（P0）

5 个 Skill 声明了但没有脚本实现：

| Skill | 优先级 | 方案 |
|-------|-------|------|
| `memory-save` | P0 | 见改进一 §1.2.1 |
| `memory-governance` | P1 | 实现记忆清理/归档脚本 |
| `web_browse` | P2 | 改为 reference 类型（Agent 自己使用工具浏览） |
| `daily-summary` | P2 | 实现日报生成脚本 |
| `investment-review` | P3 | 实现投资复盘脚本 |

---

## 改进三：工具安全与权限

### 3.1 现状分析

**当前状态**: EvoPaw 使用 `permission_mode="bypassPermissions"`，所有工具无审批直接执行。

**Harness 黄金法则**: 默认 fail-closed。工具串行且需权限审批，除非明确标记为安全并发和已批准。

**当前风险**:
- Sub-Agent 拥有 `Bash` 权限，可执行任意命令
- 无工具安全分类（只读 vs 读写 vs 破坏性）
- 无权限门控——飞书群聊中任何人都可触发任意 Skill

### 3.2 改进方案

#### 3.2.1 工具安全分类（P1）

**目标**: 为 Sub-Agent 的每个工具标注安全级别。

```python
# evopaw/tools/safety.py

from enum import Enum

class ToolSafety(Enum):
    READONLY = "readonly"      # Read, Grep, Glob
    WRITE = "write"            # Write, Edit
    EXECUTE = "execute"        # Bash
    DESTRUCTIVE = "destructive"  # 未来可能的 rm, git reset 等

# 工具安全分类注册表
TOOL_SAFETY_REGISTRY = {
    "Read": ToolSafety.READONLY,
    "Grep": ToolSafety.READONLY,
    "Glob": ToolSafety.READONLY,
    "Write": ToolSafety.WRITE,
    "Edit": ToolSafety.WRITE,
    "Bash": ToolSafety.EXECUTE,
}
```

**在 SKILL.md frontmatter 中声明工具需求**:
```yaml
allowed_tools:
  - Read       # readonly
  - Grep       # readonly
  - Bash       # execute — 需要此级别
max_safety_level: execute  # 该 Skill 所需的最高安全级别
```

#### 3.2.2 Skill 级权限门控（P1）

**目标**: 根据 Skill 的安全级别和触发来源决定是否需要用户确认。

```python
# evopaw/tools/permission_gate.py

class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"  # 目前在飞书场景简化为 allow + 审计日志

def check_skill_permission(
    skill_name: str,
    skill_safety_level: ToolSafety,
    routing_key: str,
    sender_id: str,
) -> PermissionDecision:
    """
    权限评估流程（对齐 Harness 严格分层）:
    1. 显式 deny（黑名单 Skill / 被封禁用户）
    2. 白名单检查（allowed_chats 已有此逻辑）
    3. 安全级别检查
    4. 默认 allow（飞书场景简化——内部工具）
    """
    # 1. 黑名单
    if skill_name in DENIED_SKILLS:
        return PermissionDecision.DENY

    # 2. 危险 Skill 在群聊中需要额外确认
    if (skill_safety_level == ToolSafety.DESTRUCTIVE
            and routing_key.startswith("group:")):
        return PermissionDecision.ASK

    # 3. 默认允许（内部飞书环境）
    return PermissionDecision.ALLOW
```

**应用位置**: `skill_loader.py` 的 `skill_loader()` 函数，在调用 `run_skill_agent` 前检查权限。

#### 3.2.3 Sub-Agent Bash 沙盒约束（P2）

**目标**: 限制 Sub-Agent 的 Bash 执行范围。

**方案**: 在 `build_sub_agent_options` 中添加沙盒路径限制指令到 system_prompt：

```python
SANDBOX_DIRECTIVE = """
<safety_constraints>
你的工作目录是 {session_path}。
- 禁止访问 /workspace/ 以外的路径
- 禁止修改 .config/ 目录下的凭证文件
- 禁止安装系统级包（sudo/apt/yum）
- 禁止执行网络监听（nc -l / python -m http.server）
- 所有输出文件必须放在 {session_path}/outputs/
</safety_constraints>
"""
```

这是 prompt 级约束（非操作系统级），但对 LLM Agent 有效。生产环境可配合 Docker 的 seccomp/AppArmor 强化。

---

## 改进四：上下文工程

### 4.1 现状分析

**Harness 四轴框架对照**:

| 轴 | Harness 要求 | EvoPaw 现状 | 评价 |
|---|---|---|---|
| **Select** | 懒加载 + 三层渐进披露 | bootstrap 预加载 + Skill 按需加载 | ✅ 基本对齐 |
| **Compress** | 截断带恢复指针 + 响应式压缩 | `maybe_compress` 有压缩，但截断无恢复指针 | ⚠ 部分缺失 |
| **Isolate** | 零继承默认 + 单层 fork | Sub-Agent 零继承 | ✅ 对齐 |
| **Write** | 写回循环——学习系统 | ctx.json + pgvector 异步写入 | ✅ 基本对齐 |

**关键差距**:

1. **截断无恢复指针** — `_format_history()` 截断时只说"已省略更早的 N 条消息"，但未给出可操作的恢复指令
2. **无硬限制可变长度块** — tool result、Skill 输出无 token 上限
3. **压缩阈值固定** — `_COMPRESS_THRESHOLD = 0.45` 硬编码，不随模型变化
4. **时间点快照无标记** — ctx.json 快照没有"捕获时间 + 不会自动更新"标签

### 4.2 改进方案

#### 4.2.1 截断带恢复指针（P0）

**Harness 原则**: 截断上下文块时，附加**具体的工具调用指令**，而非模糊的"已省略"。

**变更 `main_agent.py:_format_history()`**:

```python
def _format_history(history, max_turns=20):
    if not history:
        return "（无历史记录）"

    truncated = len(history) > max_turns
    recent = history[-max_turns:] if truncated else history

    lines = [f"{role_map.get(e.role, e.role)}: {e.content}" for e in recent]

    if truncated:
        omitted = len(history) - max_turns
        # 恢复指针：具体的工具调用指令（不是模糊的"如需查阅"）
        lines.insert(0, (
            f"（已省略更早的 {omitted} 条消息。"
            f"要查看完整历史，调用 skill_loader 工具："
            f'skill_name="history_reader", '
            f'task_context=\'{{"page": 1, "page_size": 20}}\'）'
        ))

    return "\n".join(lines)
```

#### 4.2.2 Skill 输出硬限制（P1）

**目标**: 防止 Sub-Agent 返回超长结果撑爆 Main Agent 上下文。

**变更 `skill_loader.py` 的 task 型分支**:

```python
_SKILL_OUTPUT_MAX_CHARS = 20_000  # ~10K token

result_text = await run_skill_agent(...)

# 硬限制 Skill 输出
if len(result_text) > _SKILL_OUTPUT_MAX_CHARS:
    truncated = result_text[:_SKILL_OUTPUT_MAX_CHARS]
    result_text = (
        f"{truncated}\n\n"
        f"[输出已截断（原始 {len(result_text)} 字符）。"
        f"完整结果已保存到 {_session_dir}/outputs/ 目录，"
        f"使用 Read 工具查看完整文件。]"
    )
```

同样适用于 reference 类型的 SKILL.md 返回——部分 SKILL.md 可能非常长（如 `memory-governance` 9500 字）。

#### 4.2.3 ctx.json 快照打时间标签（P1）

**Harness 原则**: 任何时间点状态必须带"捕获时间 + 不会自动更新"标签。

**变更 `context_mgmt.py:save_session_ctx()`**:

```python
def save_session_ctx(session_id, messages, ctx_dir):
    ctx_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "captured_at": datetime.datetime.now(tz=timezone.utc).isoformat(),
        "auto_update": False,  # 标记：此快照不会自动更新
        "messages": messages,
    }
    (ctx_dir / f"{session_id}_ctx.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
```

**对应 load 也需要适配**:
```python
def load_session_ctx(session_id, ctx_dir):
    p = ctx_dir / f"{session_id}_ctx.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    # 兼容新旧格式
    if isinstance(data, dict) and "messages" in data:
        return data["messages"]
    return data  # 旧格式：直接是 list
```

#### 4.2.4 响应式压缩优化（P2）

当前 `_COMPRESS_THRESHOLD = 0.45` 和 `_MODEL_CTX_LIMIT = 32000` 硬编码。

**改进**: 从 config.yaml 读取，且支持不同模型不同窗口：

```yaml
# config.yaml
context:
  compress_threshold: 0.45
  model_ctx_limits:
    claude-sonnet-4-6: 200000    # Sonnet 4.6 的实际窗口
    claude-haiku-4-5: 200000
  fresh_keep_turns: 10
  chunk_tokens: 2000
```

当前 `_MODEL_CTX_LIMIT = 32000` 严重低估了 Claude 4.6 的 200K 窗口。这意味着压缩过于激进，丢失了不必要的上下文。

---

## 改进五：多 Agent 协调

### 5.1 现状分析

**EvoPaw 当前模式**: 仅 Coordinator（零继承）。

| Harness 模式 | 说明 | EvoPaw 支持 |
|---|---|---|
| Coordinator | 协调者综合结果，worker 从零开始 | ✅ Main Agent → Sub-Agent |
| Fork | 子代继承父代完整上下文 | ❌ 不支持 |
| Swarm | 点对点通过共享任务列表 | ❌ 不支持 |

**当前问题**:
1. **Sub-Agent 无法获得对话上下文** — 零继承对 Skill 执行是对的，但某些任务需要对话上下文
2. **无 continue-vs-spawn 决策** — 所有 Skill 调用都创建新 Sub-Agent，没有复用机制
3. **协调者未综合** — Main Agent 直接将 Skill 输出返回用户，缺少综合步骤

### 5.2 改进方案

#### 5.2.1 Sub-Agent 上下文注入（P1）

**目标**: 让 task 型 Sub-Agent 在需要时能获得对话上下文摘要。

**方案**: 在 `skill_loader.py` 调用 `run_skill_agent` 时，可选注入上下文：

```python
# SKILL.md frontmatter 新增字段
# needs_context: true  # 声明此 Skill 需要对话上下文

if skill_info.get("needs_context"):
    # 注入最近对话摘要到 task_context
    ctx_summary = _build_brief_context(history_all[-10:])
    task_context = f"<conversation_context>\n{ctx_summary}\n</conversation_context>\n\n{task_context}"
```

**设计原则**: 遵循 Harness 的"选择最窄的有效边界"——只注入最近 10 条对话摘要，不注入全量历史。

#### 5.2.2 Skill 输出综合层（P2）

**Harness 原则**: 协调者必须综合而非委派理解。"根据你的发现去修复"是反模式。

**当前问题**: `skill_loader.py` 直接将 Sub-Agent 输出 `result_text` 原样返回给 Main Agent，Main Agent 再原样发给用户。

**改进方案**: 对长输出添加结构化摘要提示：

```python
# skill_loader.py task 型分支
result_text = await run_skill_agent(...)

# 若输出过长，提示 Main Agent 综合而非直传
if len(result_text) > 3000:
    result_text = (
        f"<skill_output skill=\"{skill_name}\">\n"
        f"{result_text}\n"
        f"</skill_output>\n\n"
        f"<synthesis_hint>"
        f"请综合以上 Skill 输出的关键信息，用简洁的语言回复用户。"
        f"不要直接转发全文。</synthesis_hint>"
    )
```

这让 Main Agent 作为协调者去综合 Sub-Agent 的输出，而不是简单中转。

#### 5.2.3 Fork 模式支持（P3）

**目标**: 支持需要共享上下文的并行子任务。

**场景**: 用户说"帮我同时创建飞书文档和发送通知"——两个 Skill 可以并行执行。

**实现思路**:

```python
# skill_loader.py 新增并行执行模式
async def _execute_parallel_skills(
    skill_names: list[str],
    task_contexts: list[str],
    shared_context: str,
    session_path: str,
):
    """Fork 模式：多个 Sub-Agent 并行执行，共享基础上下文"""
    tasks = [
        run_skill_agent(
            skill_name=name,
            skill_instructions=instructions,
            task_context=f"{shared_context}\n\n{ctx}",
            session_path=session_path,
        )
        for name, ctx in zip(skill_names, task_contexts)
    ]
    # 并行执行，单层（Sub-Agent 不能再 fork）
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return results
```

**关键约束**（对齐 Harness）:
- Fork 仅单层——Sub-Agent 不能再创建 Sub-Agent
- 使用 `asyncio.gather` 而非嵌套 fork

---

## 改进六：生命周期与扩展性

### 6.1 现状分析

| Harness 子模式 | EvoPaw 现状 | 差距 |
|---|---|---|
| Hook Lifecycle | 仅 verbose hooks（PreToolUse/PostToolUse） | 缺少完整 hook 体系 |
| Task Decomposition | 无任务分解系统 | 完全缺失 |
| Bootstrap Sequence | `main.py` 有依赖排序启动 | ✅ 基本对齐 |

### 6.2 改进方案

#### 6.2.1 扩展 Hook 体系（P1）

**当前**: 只有 verbose 模式的 PreToolUse/PostToolUse。

**目标 Hook 类型**:

```python
# evopaw/agents/hooks.py 扩展

class HookType(Enum):
    PRE_TOOL_USE = "PreToolUse"      # 工具调用前（已有）
    POST_TOOL_USE = "PostToolUse"    # 工具调用后（已有）
    PRE_SKILL = "PreSkill"           # Skill 调用前（新增）
    POST_SKILL = "PostSkill"         # Skill 完成后（新增）
    SESSION_START = "SessionStart"   # 新 session 创建（新增）
    SESSION_END = "SessionEnd"       # session 空闲退出（新增）
    ERROR = "Error"                  # 错误发生时（新增）

def build_hooks(
    verbose_callback: VerboseCallback | None = None,
    skill_hooks: list[SkillHook] | None = None,
    session_hooks: list[SessionHook] | None = None,
) -> dict:
    """构建完整 hook 字典——单一调度点"""
    hooks = {}

    if verbose_callback:
        # 现有 verbose hooks
        hooks.update(_build_verbose_hooks(verbose_callback))

    if skill_hooks:
        hooks.update(_build_skill_hooks(skill_hooks))

    if session_hooks:
        hooks.update(_build_session_hooks(session_hooks))

    return hooks
```

**应用场景**:
- `PreSkill`: 权限检查、审计日志、用量计费
- `PostSkill`: 记忆提取触发、结果缓存
- `SessionStart`: 加载用户偏好、初始化 workspace
- `SessionEnd`: 后台记忆提取、清理临时文件
- `Error`: 报警通知（飞书卡片 / 日志）

#### 6.2.2 session 启动事件（P2）

**目标**: session 创建时触发 hook，完成初始化工作。

**当前 `session/manager.py` 的 `get_or_create()` 只创建 index 条目。**

**扩展**:
```python
async def get_or_create(self, routing_key: str) -> SessionEntry:
    # ...现有逻辑...
    if created_new:
        # 触发 SessionStart hook
        await self._fire_hook(HookType.SESSION_START, {
            "session_id": entry.id,
            "routing_key": routing_key,
        })
    return entry
```

**SessionStart hook 可注册的动作**:
- 从 L3 vector 预加载该用户的高频记忆
- 为 workspace 创建标准目录结构
- 记录 session 启动 metrics

#### 6.2.3 启动序列优化（P2）

**当前 `main.py:async_main()` 已有依赖排序**，但有改进空间：

| Harness 最佳实践 | EvoPaw 现状 | 行动 |
|---|---|---|
| 快速路径分发 | 无——所有命令都初始化完整系统 | 添加 `--version` / `--check-config` 快速路径 |
| Memoize 顶层 init | `async_main` 不可重入 | 包装为 memoized singleton |
| 信任拐点后激活安全子系统 | 凭证在启动中段写入 | ✅ 已对齐 |
| 注册清理在 init 时 | shutdown 逻辑分散 | 集中注册到 `atexit` / signal handler |

**具体改进**:

```python
# main.py 快速路径
def main():
    import sys
    if "--version" in sys.argv:
        print(f"evopaw {__version__}")
        return
    if "--check-config" in sys.argv:
        # 只检查配置有效性，不启动系统
        _load_config()
        print("Config OK")
        return
    asyncio.run(async_main())
```

---

## 实施优先级与路线图

### Phase 1（基础加固）— 1-2 周

| 编号 | 改进项 | 对应 Harness 层 | 工作量 |
|------|--------|-----------------|--------|
| P1-1 | 实现 `memory-save` Skill 脚本 | L1 Memory | 1 天 |
| P1-2 | 截断带恢复指针 | L4 Context | 半天 |
| P1-3 | Skill 输出硬限制 | L4 Context | 半天 |
| P1-4 | 修正 `_MODEL_CTX_LIMIT` 为实际值 | L4 Context | 半天 |
| P1-5 | `ctx.json` 快照打时间标签 | L4 Context | 半天 |

**Phase 1 交付物**: 记忆可写入、上下文不会爆炸、截断有恢复路径。

### Phase 2（安全与发现）— 1-2 周

| 编号 | 改进项 | 对应 Harness 层 | 工作量 |
|------|--------|-----------------|--------|
| P2-1 | 扩展 SKILL.md frontmatter（triggers + safety） | L2 Skills | 1 天 |
| P2-2 | 发现列表预算控制 | L2 Skills | 半天 |
| P2-3 | 工具安全分类注册表 | L3 Tools | 1 天 |
| P2-4 | Skill 级权限门控 | L3 Tools | 1 天 |
| P2-5 | 实现 `memory-governance` Skill | L1 Memory | 1 天 |

**Phase 2 交付物**: Skill 触发更精确、有安全分类、记忆可治理。

### Phase 3（协调与扩展）— 2-3 周

| 编号 | 改进项 | 对应 Harness 层 | 工作量 |
|------|--------|-----------------|--------|
| P3-1 | Sub-Agent 可选上下文注入 | L5 Multi-agent | 1 天 |
| P3-2 | Skill 输出综合层 | L5 Multi-agent | 1 天 |
| P3-3 | 扩展 Hook 体系（PreSkill/PostSkill/Session） | L6 Lifecycle | 2 天 |
| P3-4 | index + topic 记忆分层 | L1 Memory | 2 天 |
| P3-5 | 用户自定义 Skill 来源 | L2 Skills | 1 天 |

**Phase 3 交付物**: 多 Agent 协调更智能、记忆可分层、用户可扩展 Skill。

### Phase 4（高级特性）— 3-4 周

| 编号 | 改进项 | 对应 Harness 层 | 工作量 |
|------|--------|-----------------|--------|
| P4-1 | 后台 session 提取（auto-memory 写入） | L1 Memory | 3 天 |
| P4-2 | Fork 模式（并行 Skill 执行） | L5 Multi-agent | 2 天 |
| P4-3 | `/remember` 跨层晋升命令 | L1 Memory | 2 天 |
| P4-4 | Sub-Agent Bash 沙盒约束 | L3 Tools | 1 天 |
| P4-5 | 启动序列优化（快速路径 + 信号处理） | L6 Lifecycle | 1 天 |

**Phase 4 交付物**: 记忆自动积累与晋升、并行 Skill、安全沙盒。

---

## 附录：Harness 10 大陷阱 vs EvoPaw 对照

| # | Harness 陷阱 | EvoPaw 是否命中 | 说明 |
|---|---|---|---|
| 1 | 并发分类是 per-call 的 | N/A | 当前无并发工具调用 |
| 2 | 权限评估有副作用 | ⚠ | `bypassPermissions` 跳过了所有检查 |
| 3 | 大多数异步工作跳过 pending 状态 | ✅ 对齐 | pgvector 索引直接 create_task，无 pending |
| 4 | Fork 子代不能再 fork | ✅ 对齐 | Sub-Agent 无法创建新 Agent |
| 5 | 上下文是 memoized 但手动失效的 | ⚠ | `instruction_cache` 在 session 内缓存，但 session 间无失效 |
| 6 | 记忆 index 有硬上限 | ✅ 对齐 | `_MEMORY_MAX_LINES = 200` |
| 7 | Skill 列表预算很紧 | ⚠ | 当前无预算控制 |
| 8 | Hook 信任是 all-or-nothing | N/A | 当前 hook 体系简单 |
| 9 | 工具的默认权限是 allow | 🔴 | `bypassPermissions` = 全 allow |
| 10 | 驱逐需要通知 | N/A | 无任务分解系统 |

---

## 附录：关键文件变更清单

| 文件 | Phase | 变更类型 | 说明 |
|------|-------|---------|------|
| `evopaw/skills/memory-save/scripts/save.py` | P1 | 新建 | memory-save 脚本 |
| `evopaw/agents/main_agent.py:_format_history` | P1 | 修改 | 恢复指针 |
| `evopaw/tools/skill_loader.py` | P1+P2 | 修改 | 输出硬限制、预算控制、综合提示 |
| `evopaw/memory/context_mgmt.py` | P1 | 修改 | ctx.json 时间标签、CTX_LIMIT 配置化 |
| `evopaw/skills/*/SKILL.md` | P2 | 修改 | frontmatter 扩展 triggers/safety |
| `evopaw/tools/safety.py` | P2 | 新建 | 工具安全分类 |
| `evopaw/tools/permission_gate.py` | P2 | 新建 | 权限门控 |
| `evopaw/skills/memory-governance/scripts/` | P2 | 新建 | 记忆治理脚本 |
| `evopaw/agents/hooks.py` | P3 | 修改 | 扩展 hook 体系 |
| `evopaw/memory/bootstrap.py` | P3 | 修改 | 支持 topic 文件加载 |
| `evopaw/agents/skill_agent.py` | P4 | 修改 | 并行执行支持 |
| `evopaw/runner.py` | P4 | 修改 | /remember 命令、session 提取 |
