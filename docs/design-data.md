> 本文档是 [DESIGN.md](../DESIGN.md) §5 的详细内容
> 最后更新：2026-03-06

## 5. 数据设计

### 5.1 飞书事件数据结构

> 来源：SDK `lark_oapi/api/im/v1/model/event_message.py` + `sender.py`（已验证）

**EventMessage 关键字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `message_id` | str | 消息唯一 ID，如 "om_xxxxx" |
| `root_id` | str | 话题根消息 ID（话题群中有值，用于 reply_in_thread） |
| `parent_id` | str | 父消息 ID（回复链） |
| `create_time` | int | 消息创建时间（毫秒时间戳） |
| `chat_id` | str | 会话 ID（群聊时有值） |
| `thread_id` | str | 话题 ID（话题群的某话题内有值，否则为空） |
| `chat_type` | str | "p2p" \| "group" |
| `message_type` | str | "text" \| "image" \| "file" \| "audio" \| ... |
| `content` | str | JSON 字符串（见下表） |
| `mentions` | list | @提及列表 |

**Sender 关键字段**：`id`（open_id）、`id_type`（"open_id"）、`sender_type`（"user"）、`tenant_key`

**content 字段结构（按 msg_type）**：

| msg_type | content JSON |
|----------|-------------|
| `text` | `{"text": "用户消息内容"}` |
| `image` | `{"image_key": "img_xxxxx"}` |
| `file` | `{"file_key": "file_xxxxx", "file_name": "report.pdf"}` |
| `audio` | `{"file_key": "..."}` |

---

### 5.2 内部消息：InboundMessage

框架内流转的标准化消息对象：

```
Attachment:
  msg_type:    str   # "image" | "file"
  file_key:    str   # 飞书 file_key / image_key
  file_name:   str   # 文件名（image 无文件名时用 "{image_key}.jpg"）

InboundMessage:
  routing_key: str                    # "p2p:ou_xxx" | "group:oc_xxx" | "thread:oc_xxx:ot_xxx"
  content:     str                    # 纯文本内容（附件消息时可为空）
  msg_id:      str                    # 飞书 message_id（用于幂等、Trace、下载）
  root_id:     str                    # 话题根消息 ID（thread 回复时用，非 thread 时 = msg_id）
  sender_id:   str                    # open_id（发送者）
  ts:          int                    # 创建时间（毫秒时间戳）
  is_cron:     bool = False           # True = CronService 注入的 fake 消息
  attachment:  Attachment | None      # 附件元信息（Runner 负责下载）
```

---

### 5.3 Session 存储

#### index.json（路由映射 + session 元数据）

```json
{
  "p2p:ou_abc123": {
    "active_session_id": "s-uuid-002",
    "sessions": [
      {
        "id": "s-uuid-001",
        "created_at": "2026-01-15T09:00:00Z",
        "verbose": false,
        "message_count": 12
      },
      {
        "id": "s-uuid-002",
        "created_at": "2026-01-20T14:00:00Z",
        "verbose": true,
        "message_count": 8
      }
    ]
  },
  "group:oc_chat456": {
    "active_session_id": "s-uuid-003",
    "sessions": [
      {
        "id": "s-uuid-003",
        "created_at": "2026-01-18T10:00:00Z",
        "verbose": false,
        "message_count": 5
      }
    ]
  }
}
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `active_session_id` | 当前活跃的 session（`/new` 命令会更新此字段） |
| `verbose` | 详细模式开关，session 级别，默认 `false` |
| `message_count` | 用于快速判断 session 大小，不需读全部 JSONL |

#### {session_id}.jsonl（清洁对话记录）

```jsonl
{"type":"meta","session_id":"s-uuid-002","routing_key":"p2p:ou_abc123","workspace_id":"xiaopaw-hr","created_at":"2026-01-20T14:00:00Z"}
{"type":"message","role":"user","content":"帮我把这个 PDF 转成 Word","ts":1737000000,"feishu_msg_id":"om_xxx"}
{"type":"message","role":"assistant","content":"转换完成，文件已保存到 outputs/result.docx","ts":1737000025}
{"type":"message","role":"user","content":"每周一9点给我发周报提醒","ts":1737001000,"feishu_msg_id":"om_yyy"}
{"type":"message","role":"assistant","content":"已创建定时任务：每周一 09:00 生成并发送周报摘要。","ts":1737001010}
```

#### 文件并发安全

所有文件存储统一采用**文件锁 + 安全写入**策略：

**1. index.json — `asyncio.Lock` + write-then-rename**

index.json 是全局共享资源，多个 routing_key 的 worker 可能并发读写（如 `/new` 创建新 session）。通过进程内 `asyncio.Lock` 互斥，写入使用 write-then-rename（先写临时文件再原子 rename），防止写入中途崩溃导致文件损坏。

**2. JSONL — `asyncio.Lock` per session + flush + fsync**

同一 session 的 JSONL 写入已被 Runner 的 per-routing_key 队列串行化，但 CronService 的 fake 消息也可能触发写入，因此仍需 per-session 锁。写入后执行 `flush() + os.fsync()` 确保数据落盘。

**3. cron/tasks.json — 同 index.json 的 write-then-rename 模式**

CronService 读写 tasks.json 时使用 write-then-rename 原子更新；scheduler_mgr Skill 在沙盒内通过脚本封装写入同一文件。对于 `cron` 任务，CronService 在每次加载时会根据当前 `expr`/`tz` 重新计算 `state.next_run_at_ms`，避免外部误用旧值。

**设计原则**：
- 进程内并发用 `asyncio.Lock`（单进程架构，无需跨进程文件锁）
- 全量写入文件统一用 **write-then-rename**（原子性）
- JSONL append 后 **flush + fsync**（持久性）
- 如启动时发现 `.tmp` 文件残留，忽略（index.json 保持上次完整版本）

---

### 5.4 Trace 存储

每次消息处理对应一个 Trace 目录，记录完整的 LLM 上下文：

```
data/traces/{session_id}/{ts}_{msg_id}/
├── meta.json          # 执行摘要
├── main.jsonl         # 主 Agent 完整 context_messages
└── skills/
    ├── file_processor.jsonl   # file_processor Sub-Crew context
    └── feishu_ops.jsonl
```

**meta.json**：

```json
{
  "session_id":    "s-uuid-002",
  "feishu_msg_id": "om_xxx",
  "root_id":       "om_root_xxx",
  "routing_key":   "p2p:ou_abc123",
  "user_message":  "帮我把这个 PDF 转成 Word",
  "skills_called": ["file_processor"],
  "duration_ms":   25340,
  "ts_start":      1737000000000,
  "ts_end":        1737000025340,
  "is_cron":       false
}
```

**main.jsonl**（主 Agent LLM context，每行一个 message）：

```jsonl
{"role":"user","content":"【历史】...\n【session目录】/workspace/sessions/s-uuid-002\n【当前】帮我把这个 PDF 转成 Word"}
{"role":"assistant","tool_calls":[{"name":"skill_loader","args":{"skill_name":"file_processor","task_context":"..."},"call_id":"c001"}]}
{"role":"tool","name":"skill_loader","call_id":"c001","content":"{\"errcode\":0,\"message\":\"转换成功\",\"files\":[\"...\"]}"}
{"role":"assistant","content":"转换完成，文件已保存到 outputs/result.docx。"}
```

---

### 5.5 Session 工作空间（中间产物）

每个 session 在主机上拥有独立的文件工作区，整体挂载进 AIO-Sandbox 容器：

```
data/workspace/
├── .config/
│   └── feishu.json              # 启动时写入，Sub-Crew 从此读取 credentials
└── sessions/
    └── {session_id}/
        ├── uploads/             # 用户通过飞书发来的文件（自动下载）
        ├── outputs/             # Skill 产出的成果文件
        └── tmp/                 # Sub-Crew 临时工作区（session 结束后主动清理）
```

**沙盒内可见路径**（docker-compose 挂载整个 workspace/）：

```
/workspace/.config/feishu.json
/workspace/sessions/{session_id}/uploads/
/workspace/sessions/{session_id}/outputs/
/workspace/sessions/{session_id}/tmp/
```

**设计原则**：
- 不同 session 目录完全隔离，Sub-Crew 只能访问自己 session 的目录
- SkillLoaderTool 在 `sandbox_execution_directive` 中注入实际的 session 目录路径，LLM 可以看到路径但无法获取或篡改 session_id 本身（session_id 不进入 akickoff inputs）

---

### 5.6 CronJob 数据结构

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "job-abc123",
      "name": "每周工作摘要",
      "enabled": true,
      "schedule": {
        "kind": "cron",
        "expr": "0 9 * * 1",
        "tz": "Asia/Shanghai",
        "at_ms": null,
        "every_ms": null
      },
      "payload": {
        "routing_key": "p2p:ou_abc123",
        "message": "请生成本周工作摘要并发给我"
      },
      "state": {
        "next_run_at_ms": 1738800000000,
        "last_run_at_ms": null,
        "last_status": null,
        "last_error": null
      },
      "created_at_ms": 1736900000000,
      "updated_at_ms": 1736900000000,
      "delete_after_run": false
    }
  ]
}
```

**schedule.kind 三种模式**：
- `at`：一次性，`at_ms` 为触发时刻毫秒时间戳，`delete_after_run: true` 时触发后自动删除
- `every`：周期，`every_ms` 为间隔毫秒数，循环执行
- `cron`：Cron 表达式，`expr` + `tz` 组合，croniter 计算下次触发时间。对于 `cron` 任务，外部（包含 scheduler_mgr Skill）写入 tasks.json 时通常将 `state.next_run_at_ms` 设为 `null`，由 CronService 在加载时统一根据最新表达式重算，以避免“改了 expr 却仍按旧时间触发”的问题。

---

### 5.7 Skill 定义结构

#### SKILL.md frontmatter

```yaml
---
name: file_processor           # Skill 唯一标识（与目录名一致）
description: "PDF/DOCX 解析与格式转换"  # XML 摘要中展示，帮助主 Agent 选择
type: task                     # reference（参考型）| task（任务型）
version: "1.0"
---

# file_processor Skill

## 功能说明
...（完整执行指令，由 Sub-Crew 读取）
```

#### load_skills.yaml

```yaml
skills:
  - name: pdf
    type: task
    enabled: true
  - name: docx
    type: task
    enabled: true
  - name: pptx
    type: task
    enabled: true
  - name: xlsx
    type: task
    enabled: true
  - name: feishu_ops
    type: task        # 脚本化架构：Sub-Crew 调用 feishu_ops/scripts/ 下独立 Python 脚本
    enabled: true
  - name: scheduler_mgr
    type: task        # 脚本化架构：Sub-Crew 调用 scheduler_mgr/scripts/ 下独立 Python 脚本
    enabled: true
  - name: history_reader
    type: reference   # SkillLoaderTool 内联处理，不启动 Sub-Crew
    enabled: true
```

#### 主 Agent 注入的 XML 摘要（SkillLoaderTool description 中携带）

```xml
<skills>
  <skill type="task">
    <name>pdf</name>
    <description>PDF 解析与文本提取，支持格式转换</description>
  </skill>
  <skill type="task">
    <name>docx</name>
    <description>Word 文档读取与处理</description>
  </skill>
  <skill type="task">
    <name>pptx</name>
    <description>PowerPoint 文档读取与处理</description>
  </skill>
  <skill type="task">
    <name>xlsx</name>
    <description>Excel 表格读取与数据处理</description>
  </skill>
  <skill type="task">
    <name>feishu_ops</name>
    <description>飞书操作：读取云文档内容、向指定群/用户发消息</description>
  </skill>
  <skill type="task">
    <name>scheduler_mgr</name>
    <description>创建/查看/删除定时任务，支持一次性/周期/cron 三种触发模式</description>
  </skill>
  <skill type="reference">
    <name>history_reader</name>
    <description>分页读取历史对话记录，适合"我之前说过什么"类查询</description>
  </skill>
</skills>
```

---

### 5.8 SkillLoaderTool I/O（Pydantic）

**SkillLoaderInput**：

| 字段 | 说明 |
|------|------|
| `skill_name` | 要调用的 Skill 名称，必须是 XML 摘要中 `<name>` 标签的值之一 |
| `task_context` | 任务型：须包含子任务描述、期望输出格式、输入/输出路径（使用 SkillLoaderTool description 中展示的实际路径）、特殊要求；参考型：留空或传用户原始问题 |

> **安全说明**：`task_context` 描述中不再出现 `{session_id}` 占位符，路径由 SkillLoaderTool description 直接告知 LLM，LLM 无需关心 session_id 本身。

**SkillResult**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `errcode` | int | 0=成功，非0=失败 |
| `message` | str | 人类可读的结果摘要（主 Agent 直接用于回复用户） |
| `data` | dict | 结构化结果数据（可选，默认 `{}`） |
| `files` | list[str] | 产出文件在沙盒中的绝对路径列表（可选，默认 `[]`） |

**history_reader 特殊格式**（内联返回，非 SkillResult）：

```json
{
  "errcode": 0,
  "message": "成功读取第 1 页，共 35 条消息，本页 20 条",
  "data": {
    "messages": [{"role": "user", "content": "..."}, ...],
    "total": 35,
    "page": 1,
    "page_size": 20,
    "total_pages": 2
  }
}
```
