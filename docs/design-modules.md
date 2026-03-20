> 本文档是 [DESIGN.md](../DESIGN.md) §4 的详细内容
> 最后更新：2026-03-10

## 4. 模块设计

### 4.1 FeishuListener（飞书接入）

**职责**：维护 WebSocket 长连接，接收飞书事件，解析后交给下游。**不负责文件下载**（下载在 Runner 中 session 确定后执行）。

**接入方案**：WebSocket 长连接（lark-oapi `ws.Client`），无需公网 IP，适合本地/内网部署。

**消息类型处理**：

| msg_type | 处理逻辑 |
|----------|---------|
| `text` | 直接提取 `content.text`，构造 InboundMessage |
| `image` | 解析 `image_key`，存入 `attachment` 字段，content 置空 |
| `file` | 解析 `file_key` + `file_name`，存入 `attachment` 字段，content 置空 |
| `post` | 提取富文本纯文本部分（调用 `_extract_post_text` 解析 JSON），构造 InboundMessage |
| `audio` | 回复"暂不支持语音消息"，不进入 Agent |
| `sticker` | 忽略，不回复 |
| `merge_forward` | 回复"暂不支持转发合集"，不进入 Agent |

**关键逻辑**：
- 通过 `resolve_routing_key(event)` 将飞书事件转为 routing_key
- 解析消息内容和附件元信息（只记录 file_key，不下载）
- 构造 `InboundMessage`（含 `routing_key`、`content`、`msg_id`、`root_id`、`sender_id`、`ts`、`attachment`）
- 监听 `im.chat.member.bot.added_v1` 事件（Bot 入群欢迎），通过可选参数 `on_bot_added: Callable[[str, str], Awaitable[None]]` 解耦
- 支持可选参数 `allowed_chats: list[str] | None`：p2p 消息始终放行；群消息和 Bot 入群事件检查白名单（None 或 [] 表示允许所有）

---

### 4.2 SessionRouter（会话路由）

**职责**：将飞书事件的三种会话类型统一映射为 `routing_key`，作为 Session 的唯一标识。

**路由规则**：

| 聊天类型 | 判断条件 | routing_key |
|---------|---------|-------------|
| 单聊 | `chat_type == "p2p"` | `p2p:{open_id}` |
| 普通群聊 | `chat_type == "group"` AND `thread_id` 为空 | `group:{chat_id}` |
| 话题群（某话题）| `chat_type == "group"` AND `thread_id` 非空 | `thread:{chat_id}:{thread_id}` |

**纯函数实现**：`resolve_routing_key(event)` — 无副作用，便于单元测试。

---

### 4.3 Runner（执行引擎）

**职责**：核心协调层，串联 Session 管理、Agent 执行、存储写入、消息回复。

**并发控制**：同一 `routing_key` 的消息**串行处理**，不同 routing_key 之间**并行**。

- 队列锁在 **routing_key** 上（同一用户/群/话题的消息串行）
- 每次 `_handle` 调用 `get_or_create(routing_key)` **动态解析**当前 `active_session_id`
- `/new` 命令在 `_handle_slash` 中更新 `active_session_id`，后续消息自动使用新 session
- 同一 routing_key 下可能存在多个历史 session，但同一时刻只有一个 active

```
routing_key（队列维度）        active_session_id（动态解析）
─────────────────────        ─────────────────────────────
p2p:ou_abc123           →    s-uuid-002（/new 后变为 s-uuid-003）
group:oc_chat456        →    s-uuid-004
thread:oc_chat789:ot_x  →    s-uuid-005
```

**队列模型**：
- 每个 routing_key 维护一个 `asyncio.Queue`，消息按到达顺序入队
- 首条消息入队时自动启动该 routing_key 的 worker coroutine
- worker 逐条消费，执行完一条再取下一条
- 队列空闲超时后 worker 自动退出，释放内存

**`_handle` 执行步骤**：
1. Slash Command 拦截（不进入 Agent）
2. 动态解析当前 active session
3. 附件下载（session 确定后才知道目标目录）
4. 加载对话历史（最近 max_turns 条）
5. **发送 Loading 卡片**：send_thinking() 发起"⏳ 思考中..."卡片，获取 card_msg_id（失败返回 None，不阻断主流程）
6. 执行主 Agent
7. 写 Trace + Session，**更新卡片**：update_card(card_msg_id) 替换为最终结果；失败时降级调用 send()

**Slash Command 处理**（在进入 Agent 前拦截）：

| 命令 | 处理逻辑 |
|------|---------|
| `/new` | 创建新 Session，更新 index.json active_session_id |
| `/verbose on/off` | 更新 session.verbose，立即生效 |

---

### 4.4 Main Agent + SkillLoaderTool

**Main Agent 设计原则**：极简，唯一工具是 SkillLoaderTool。避免直接绑定飞书 API 等工具，保持 Agent 的能力可扩展性。

**配置要点**：
- role: "XiaoPaw 工作助手"
- tools: `[SkillLoaderTool(...)]` — 唯一工具
- llm: `qwen3-max`，max_iter: 50

**安全隔离原则**：`session_id` 不通过 LLM 上下文（task description / akickoff inputs）传递，仅由系统内部管理（`SkillLoaderTool._session_id` PrivateAttr）。LLM 只能看到注入 description 中的实际路径，无法获取或篡改 session_id 本身。

**SkillLoaderTool 初始化参数**：

| 参数 | 说明 |
|------|------|
| `session_id` | 当前会话 ID，注入沙盒路径（不传给 LLM） |
| `sandbox_url` | AIO-Sandbox MCP 端点 URL |
| `routing_key` | 当前用户路由键，注入 sandbox_execution_directive |
| `history_all` | 完整历史消息列表（供 history_reader 内联分页使用） |

**SkillLoaderTool 工作原理**（渐进式披露）：

```mermaid
flowchart TD
    CALL([Main Agent 调用 SkillLoaderTool]) --> PARSE["解析参数\nskill_name, task_context"]
    PARSE --> HR{skill_name ==\nhistory_reader?}

    HR -->|是| INLINE["内联处理：从 _history_all 分页\n不启动 Sub-Crew，不依赖沙盒"]
    INLINE --> RET

    HR -->|否| FIND{skill_name 在\nload_skills.yaml 中?}
    FIND -->|否| ERR["建设性报错：\n'可用 Skills：xxx，请重新选择'"]
    FIND -->|是| TYPE{SKILL.md frontmatter\ntype 字段?}

    TYPE -->|reference| REF["加载完整 SKILL.md 正文\n（去掉 frontmatter 头）"]
    REF --> INJECT["返回指令文本\nMain Agent 自行推理执行"]

    TYPE -->|task| BUILD["build_skill_crew(skill_name, full_instructions)"]
    BUILD --> CREW["Sub-Crew.kickoff(inputs={task_context, + extra_vars自映射})"]
    CREW --> SB["AIO-Sandbox MCP\n全部工具开放执行"]
    SB --> RES{SkillResult\nerrcode?}
    RES -->|0 成功| OK["返回 message + files 摘要"]
    RES -->|非0 失败| FAIL["返回建设性错误 + 重试建议"]
    OK & FAIL --> TW["写入 Trace\nskills/{name}.jsonl"]
    TW --> RET([返回摘要给 Main Agent])
    INJECT --> RET
```

**history_reader 内联处理**：当 `skill_name == "history_reader"` 时，SkillLoaderTool 直接从 `_history_all`（构造时由系统注入的完整历史）按 `page` / `page_size` 分页，返回 JSON 结果，不经过 Sub-Crew 或沙盒。LLM 只需传入分页参数，无需知道 session_id。

**SKILL.md 模板变量处理**：`_get_skill_instructions()` 在返回指令前：
1. 替换已知路径占位符（`{skill_base}` / `{session_dir}` 等）
2. 将剩余的 `{var}` 全部转义为 `{{var}}`，防止 CrewAI 误解析为模板变量
3. 在 `_execute_skill_async()` 中，`_CREWAI_VAR_PATTERN` 扫描转义后的 instructions，将 CrewAI 正则仍能找到的变量名（`{{var}}` 内部的 `{var}`）以自映射形式加入 `akickoff(inputs=...)` 中，避免 "Template variable not found" 报错。

---

### 4.5 Sub-Crew 工厂

**职责**：任务型 Skill 触发时，动态构建隔离的 Sub-Crew，接入 AIO-Sandbox。

**MCP 工具开放策略**：

Sub-Crew 通过 `MCPServerHTTP` 接入 AIO-Sandbox，开放全部 MCP 工具（无白名单过滤），包括：
- **核心执行工具**：`sandbox_execute_bash`、`sandbox_execute_code`、`sandbox_file_operations`、`sandbox_str_replace_editor`
- **网页工具**：`sandbox_convert_to_markdown`（URL → Markdown）
- **浏览器自动化**：`browser_navigate`、`browser_get_markdown`、`browser_screenshot`、`browser_get_clickable_elements` 等 browser_* 系列工具
- **环境探查**：`sandbox_get_context`、`sandbox_get_packages`

工具约束通过 **Agent backstory 行为规范**实现（而非接口级过滤）：backstory 明确告知 Sub-Agent 可用工具列表，并禁止将 skill_name 当作工具名调用。

**设计要点**：
- 每次 Skill 调用都构建**新实例**，防止状态污染
- Sub-Crew 不注入 `step_callback`（verbose 只推主 Agent）
- session 工作目录通过 `SkillLoaderTool._get_skill_instructions()` 注入到任务指令（sandbox_execution_directive），不经过 LLM 可见的 task inputs
- Agent 配置：role=`{skill_name} 执行专家`，model=`qwen3-max`，max_iter=20
- Task 期望输出：JSON 格式的 `SkillResult`（output_pydantic=SkillResult）

---

### 4.6 CronService（定时调度）

**职责**：读取 `cron/tasks.json`，精确调度定时任务，触发时构造 fake InboundMessage 进入 Runner 管道。

**核心设计**：
- **asyncio timer**（非 APScheduler/轮询），精确睡眠到下一个 job 触发时刻
- **mtime 热重载**：scheduler_mgr Skill 写入 tasks.json 后，CronService 下次 tick 自动感知并重新解析
- **cron 任务自动重算**：每次加载 tasks.json 时，对所有 `enabled` 的 `cron` 任务根据当前 `expr`/`tz` 重新计算 `state.next_run_at_ms`，忽略文件中旧值，保证“修改 cron 表达式或通过脚本更新任务”能立即按新规则生效
- **内置清理 Job**：每日 3:00 触发 CleanupService，内存注册不写入 tasks.json

```mermaid
sequenceDiagram
    participant Skill as scheduler_mgr Skill
    participant TJ as tasks.json
    participant CS as CronService
    participant R as Runner
    participant F as 飞书

    Note over CS: 进程启动，加载 tasks.json，arm_timer()

    Skill->>TJ: 写入新 Job（用户创建定时任务）
    CS->>TJ: 检测 mtime 变化（下次 tick 时）
    CS->>CS: _load_store() 重新解析，_arm_timer() 重算

    Note over CS: 精确 sleep 到触发时刻
    CS->>CS: _on_timer()
    loop 遍历到期 jobs
        CS->>R: fake InboundMessage{routing_key, message, is_cron=True}
        R->>R: 正常 Agent 执行流程
        R->>F: 发送结果
        CS->>CS: 更新 next_run_at_ms
        alt kind == "at"（一次性）
            CS->>TJ: delete_after_run=true → 删除；否则 enabled=false
        else kind == "every" / "cron"
            CS->>CS: 计算下次触发时间
        end
    end
    CS->>TJ: 保存 tasks.json
    CS->>CS: _arm_timer() 重新挂载
```

**三种调度模式**：

| 用户说 | schedule.kind | 参数示例 | 到期后 |
|-------|--------------|---------|-------|
| 明天10点提醒开会 | `at` | `at_ms: 1738800000000` | `delete_after_run: true` 自动删除 |
| 每20分钟提醒站起来 | `every` | `every_ms: 1200000` | 循环执行 |
| 每周一早9点生成周报 | `cron` | `expr: "0 9 * * 1"`, `tz: "Asia/Shanghai"` | croniter 计算下次，循环执行 |

---

### 4.7 FeishuSender（消息发送）

**职责**：根据 routing_key 类型选择正确的飞书发送 API，支持交互式卡片、纯文本、卡片更新，含幂等控制和重试。

**三个核心方法**：

| 方法 | 功能 | 用途 |
|------|------|------|
| `send(routing_key, content, root_id)` | 发送 interactive 卡片（lark_md Markdown 格式） | 主 Agent 最终回复 |
| `send_thinking(routing_key, root_id)` | 发送"⏳ 思考中..."加载卡片，返回 card_msg_id | Runner 步骤 5，显示 Loading 状态 |
| `update_card(card_msg_id, content)` | PATCH 更新已发送卡片的内容 | Runner 步骤 8，将 Loading 卡片替换为结果 |
| `send_text(routing_key, content, root_id)` | 发送纯文本消息（msg_type="text"） | Slash 命令回复 |

**send_thinking 实现细节**：
- 发送成功返回 `message_id`（即 card_msg_id），失败返回 None（不阻断主流程）
- 内容固定为"⏳ 思考中，请稍候..."
- 卡片格式：`{"config": {"wide_screen_mode": true}, "elements": [{"tag": "div", "text": {"content": "...", "tag": "lark_md"}}]}`

**update_card 实现细节**：
- 调用 `PATCH /im/v1/messages/:message_id`，使用 `PatchMessageRequestBody`
- 失败时抛出异常，由 Runner 捕获后降级为 send()

**_build_card 辅助方法**：
- 构建交互式卡片 JSON（lark_md Markdown 格式）
- 用于 send() 和 send_thinking()

**API 选择逻辑**：

```mermaid
flowchart TD
    SEND([FeishuSender.send]) --> RK{routing_key 类型}

    RK -->|p2p:{open_id}| P2P["POST /im/v1/messages\nreceive_id_type=open_id"]
    RK -->|group:{chat_id}| GRP["POST /im/v1/messages\nreceive_id_type=chat_id"]
    RK -->|thread:{chat_id}:{thread_id}| THR["POST /im/v1/messages/:root_id/reply\nreply_in_thread=true"]

    P2P & GRP & THR --> BODY["RequestBody\nmsg_type='interactive' 或 'text'\ncontent=lark_md JSON 或 纯文本\nuuid={msg_id}  ← 幂等去重"]
    BODY --> RESP{API 响应}
    RESP -->|成功| DONE([完成])
    RESP -->|失败| RETRY["最多重试 3 次\n指数退避 1s/2s/4s"]
```

**关键点**：
- 话题群（thread）回复：使用 `ReplyMessage` API，`message_id=root_id`，`reply_in_thread=True`
- `uuid` 字段传入 `feishu_msg_id`，防止网络重试重复发送（飞书幂等去重）
- Bot 自身回复**不走 Skill**，直接在 Runner 层调用 FeishuSender
- send_thinking 失败（返回 None）时，Runner 仍继续执行 Agent，后续 send() 或 update_card() 可能成功

---

### 4.8 CleanupService（存储清理）

**职责**：按策略清理过期文件，防止磁盘无限增长。双触发：启动时 Sweep + 每日 3:00 定时任务。

**启动初始化**（除清理外，还执行以下操作）：
- `ensure_workspace_dirs()`：创建 session workspace 目录结构
- `write_feishu_credentials(app_id, app_secret)`：将飞书凭证写入沙盒 `.config/feishu.json`（0o600 权限，write-then-rename 原子写）
- `write_baidu_credentials(api_key)`：将百度千帆 API Key 写入沙盒 `.config/baidu.json`（同上；`api_key` 为空时跳过，不阻断主流程）

**清理策略**：

| 目录 | 保留天数 |
|------|---------|
| `data/workspace/sessions/*/tmp/` | 1 天（Session 结束时主动清理，兜底 1 天）|
| `data/workspace/sessions/*/uploads/` | 7 天 |
| `data/workspace/sessions/*/outputs/` | 30 天 |
| `data/traces/` | 30 天 |
| `data/sessions/*.jsonl` | 365 天 |

详见主文件 §11.2 存储清理策略。

---

### 4.9 TestAPI（测试接口）

**职责**：提供 HTTP 接口模拟飞书消息事件，绕过 WebSocket 直接注入 Runner，同步返回 Bot 回复。仅 `debug.enable_test_api: true` 时启用。

**设计要点**：
- 与 FeishuListener 入口等价：构造 InboundMessage → `Runner.dispatch()`
- 通过可替换的 `FeishuSender` 接口捕获回复内容，同步返回给调用方
- 支持模拟附件消息（提供本地文件路径，跳过飞书下载）
- 集成测试可编排多轮对话、验证 session 隔离、测试 slash command

**API 端点**：

```
POST /api/test/message    → 发送消息（含可选附件），同步等待 Bot 回复
DELETE /api/test/sessions → 清空所有测试 session 数据
```

**请求/响应结构**（核心字段）：

```
TestRequest:
  routing_key:          str          # 必填："p2p:ou_test001" 等
  content:              str          # 用户消息文本
  msg_id:               str | None   # 可选，自动生成 "test_{uuid}"
  sender_id:            str          # 模拟用户 open_id
  attachment.file_path: str          # 本地文件路径（非飞书 file_key）
  attachment.file_name: str | None   # 可选，覆盖原始文件名

TestResponse:
  msg_id:        str        # 请求消息 ID
  reply:         str        # Bot 回复内容
  session_id:    str        # 使用的 session ID
  duration_ms:   int        # 处理耗时
  skills_called: list[str]  # 调用的 Skill 列表
```

**实现架构**：

```mermaid
sequenceDiagram
    participant T as 测试客户端
    participant API as TestAPI Server
    participant R as Runner
    participant CS as CaptureSender

    T->>API: POST /api/test/message
    API->>API: 构造 InboundMessage
    API->>API: 注册 Future (msg_id → asyncio.Future)

    alt 有 attachment.file_path
        API->>API: 复制本地文件到 session uploads/（跳过飞书下载）
        API->>API: content 改写为文件路径提示
    end

    API->>R: dispatch(inbound)

    Note over R: Runner 正常处理流程
    R->>CS: send(routing_key, reply, root_id)
    CS->>CS: 捕获 reply 内容
    CS->>API: resolve Future(msg_id → reply)

    API-->>T: TestResponse{reply, session_id, duration_ms, ...}
```

**CaptureSender**：实现 `SenderProtocol`（与 `FeishuSender` 共同接口），通过 `asyncio.Future` 捕获回复内容。Runner 构造时注入 `sender: SenderProtocol`，测试时替换为 `CaptureSender`。

**测试模式下附件处理**（`_copy_attachment` 函数）：
1. 解析 routing_key → 获取当前 active session_id
2. 将文件复制到 `workspace_dir/sessions/{sid}/uploads/`（`workspace_dir` 在构建时注入，默认 `data/workspace`）
3. 将 InboundMessage.content 改写为沙盒路径提示（`/workspace/sessions/{sid}/uploads/{filename}`）
4. 不设置 `inbound.attachment`（跳过 Runner 的飞书下载步骤）

---

### 4.10 feishu_ops Skill 脚本架构

feishu_ops Skill 采用**脚本化架构**：每类操作独立为一个 Python 脚本，Sub-Crew 通过 `sandbox_execute_bash` 直接调用。

**脚本清单**（`skills/feishu_ops/scripts/`）：

| 脚本 | 功能 | 关键参数 |
|------|------|---------|
| `_feishu_auth.py` | 共享鉴权模块（不直接调用） | — |
| `send_text.py` | 发送纯文字消息 | `--routing_key`, `--text` |
| `send_post.py` | 发送富文本消息（标题+多段落） | `--routing_key`, `--title`, `--paragraphs` |
| `send_image.py` | 上传图片并发送 | `--routing_key`, `--image_path` |
| `send_file.py` | 上传文件并发送 | `--routing_key`, `--file_path` |
| `read_doc.py` | 读取飞书文档纯文本 | `--doc`（URL 或 token） |
| `read_sheet.py` | 读取飞书电子表格 | `--sheet`, `--sheet_id`, `--range` |
| `get_chat_members.py` | 获取群组成员列表 | `--chat_id` |
| `list_events.py` | 查询日历事件 | `--calendar_id`, `--start_time`, `--end_time` |
| `create_event.py` | 创建日历事件 | `--calendar_id`, `--summary`, `--start_time`, `--end_time` |

**`_feishu_auth.py` 共享模块**（所有脚本通过 `sys.path.insert` 导入）：
- `get_headers()` — 获取 tenant_access_token，返回 JSON 请求头
- `get_auth_header()` — 仅含 Authorization（用于 multipart 上传）
- `parse_routing_key(key)` — `"p2p:ou_xxx"` → `("open_id", "ou_xxx")`；`"group:oc_xxx"` → `("chat_id", "oc_xxx")`
- `parse_doc_token(url_or_token)` — 从 URL 或直接 token 提取文档 token
- `parse_sheet_token(url_or_token)` — 同上，针对表格
- `output_ok(data)` — 打印 `{"errcode":0,"errmsg":"success","data":{...}}` 后 `sys.exit(0)`
- `output_error(msg, hint)` — 打印 `{"errcode":1,"errmsg":"..."}` 后 `sys.exit(0)`
- `check_feishu_resp(data, hint)` — 检查飞书 API 响应 code，非0时调用 `output_error`

**设计原则**：
- 所有脚本统一输出 JSON 到 stdout，退出码恒为 0（错误通过 errcode 字段区分）
- 仅使用 tenant_access_token（应用级鉴权），无需 user_access_token
- 凭证从 `/workspace/.config/feishu.json` 读取，不进入模型上下文
- 图片/文件先上传获取 key，再发消息（两步走）
- routing_key 支持 `p2p:ou_xxx`、`group:oc_xxx`、裸 `ou_xxx`、裸 `oc_xxx` 四种格式

---

### 4.11 scheduler_mgr Skill 脚本架构

`scheduler_mgr` 采用与 `feishu_ops` 类似的**脚本化架构**，但面向的是 `cron/tasks.json` 的安全读写：

- 目录：`skills/scheduler_mgr/scripts/`
- 入口脚本：
  - `_tasks_store.py`：内部工具，不直接给 LLM 调用；封装 tasks.json 的加载/校验/原子写入，提供 `list_jobs/create_job/delete_job/update_job` 四个函数，并在 CLI 模式下支持 `action=list|create|delete|update`。
  - `list_tasks.py`：输出当前所有 Job 列表。
  - `create_task.py`：创建新任务（支持 `schedule_kind=at|every|cron`，自动补齐 `created_at_ms` 等字段，并将 `state.next_run_at_ms` 设为 `null` 交给 CronService 处理）。
  - `update_task.py`：按 `job_id` 更新任务（包括修改 cron 表达式 / 启用禁用等），内部自动将该任务的 `state.next_run_at_ms` 置为 `null`，以便 CronService 按最新配置重算下一次触发时间。
  - `delete_task.py`：按 `job_id` 删除任务。

**调用方式**（由 Sub-Crew 通过 `sandbox_execute_bash` 执行）：

```bash
python {_skill_base}/scripts/list_tasks.py
python {_skill_base}/scripts/create_task.py --name ... --routing_key ... --message ... --schedule_kind cron --expr "50 18 * * 1-5" --tz "Asia/Shanghai"
python {_skill_base}/scripts/update_task.py --job_id job-xxxx --expr "50 18 * * 1-5" --schedule_kind cron --tz "Asia/Shanghai"
python {_skill_base}/scripts/delete_task.py --job_id job-xxxx
```

**约束与约定**：

- `_tasks_store.py` 为内部封装脚本，**禁止**直接通过 LLM 调用；推荐始终使用 `create_task.py` / `update_task.py` / `list_tasks.py` / `delete_task.py` 这几个入口脚本。
- 所有脚本统一输出 `{"errcode": 0/1, "errmsg": "...", "data": {...}}` 到 stdout，与其它 Skills 保持一致。
- scheduler_mgr 只负责**配置层**：如何在 `cron/tasks.json` 中表达“何时触发、触发时给主 Agent 说什么”；具体业务执行逻辑始终由主 Agent + 其它 Skills（如 `baidu_search` / `web_browse`）完成。

