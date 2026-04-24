# EvoPaw 消息流完整解析

> 本文档系统梳理从飞书消息接收到最终回复的完整运行逻辑，涵盖进程启动、消息路由、Agent 调用、Skill 执行、记忆持久化等全链路。

---

## 一、进程启动 (`evopaw/main.py`)

```
async_main() 启动顺序：

1. 加载 config.yaml（飞书凭证、agent 参数、记忆配置、调试选项）
2. 初始化日志 + Prometheus metrics
3. 检测 Claude Code CLI（shutil.which("claude")）
4. 读取关键配置：
   - feishu.app_id / app_secret
   - memory.workspace_dir / ctx_dir / db_dsn
   - agent.planner_model / sub_agent_model / max_turns
   - sender.max_retries / retry_backoff
   - runner.queue_idle_timeout_s
5. 构建核心服务：
   - SessionManager(data_dir)
   - FeishuSender(client, max_retries, retry_backoff)
   - FeishuDownloader(client, data_dir, workspace_dir)
   - CleanupService(data_dir, workspace_dir)
6. 凭证注入（写到 workspace/.config/，不经过 LLM）：
   - write_feishu_credentials() → feishu.json
   - write_tavily_credentials() → tavily.json
7. 启动时清理：cleanup_svc.sweep()
8. build_agent_fn() → 返回闭包（绑定 sender、workspace、ctx、db_dsn）
9. 构建 Runner（队列引擎）
10. 启动 CronService（定时任务 timer）
11. 启动 FeishuListener（WebSocket 长连接，on_message → runner.dispatch）
12. 并行运行：飞书监听 + metrics server + 每日清理定时器 + 可选 TestAPI
```

---

## 二、飞书消息接收 (`evopaw/feishu/listener.py`)

### 2.1 WebSocket 连接

`FeishuListener` 使用 lark-oapi 的 `WSClient` 建立 WebSocket 长连接，**无需公网 IP**。由于 `start()` 是阻塞同步方法，通过 `run_in_executor` 隔离到线程池。`run_forever()` 在异常后 sleep 5s 自动重连。

### 2.2 事件处理

`_EvoPawEventHandler.do_without_validation(payload)` 处理流程：

```
bytes payload
  → JSON 解析，提取 header.event_type + event
  → 事件分发：
     ├─ im.chat.member.bot.added_v1 → on_bot_added 回调（可选）
     ├─ im.message.receive_v1       → 消息处理（核心路径）
     └─ 其他                        → 静默忽略
```

### 2.3 消息解析

```
im.message.receive_v1 事件：
  1. 白名单检查（p2p 始终放行，群聊检查 chat_id）
  2. 提取字段：
     - sender_open_id
     - chat_type ("p2p" / "group")
     - chat_id / thread_id
  3. resolve_routing_key() → 三种路由规则
  4. _extract_content() → 支持 text 和 post（富文本）类型
  5. _extract_attachment() → 支持 image 和 file 类型
  6. 构造 InboundMessage
  7. asyncio.run_coroutine_threadsafe(on_message(inbound), loop)  # 跨线程调度到主循环
```

---

## 三、路由键解析 (`evopaw/feishu/session_key.py`)

routing_key 是整个系统的核心标识，贯穿 session 隔离、队列隔离、消息路由：

| 场景 | routing_key 格式 | 隔离粒度 |
|------|------------------|----------|
| 单聊 | `p2p:{open_id}` | 每个用户独立 session |
| 群聊 | `group:{chat_id}` | 群内所有消息共享 session |
| 话题 | `thread:{chat_id}:{thread_id}` | 群内话题隔离 |

---

## 四、数据模型

### `evopaw/models.py`

| 模型 | 说明 |
|------|------|
| `Attachment` | frozen dataclass: msg_type, file_key, file_name |
| `InboundMessage` | 框架内流转的标准化消息: routing_key, content, msg_id, root_id, sender_id, ts, is_cron, attachment |
| `SenderProtocol` | Protocol: send(), send_thinking(), update_card(), send_text() |

### `evopaw/session/models.py`

| 模型 | 说明 |
|------|------|
| `SessionEntry` | id, created_at, verbose, message_count |
| `RoutingEntry` | active_session_id + sessions 列表 |
| `MessageEntry` | role, content, ts, feishu_msg_id |

---

## 五、执行引擎 (`evopaw/runner.py`)

### 5.1 并发模型

```
per-routing_key 队列 + worker：

dispatch(inbound)
  → _dispatch_lock 保护
  → 该 routing_key 无队列？创建 asyncio.Queue + 启动 _worker task
  → 消息入队

_worker(key)
  → 无限循环，asyncio.wait_for(queue.get(), timeout=idle_timeout)
  → 空闲超时自动退出，释放内存
  → 同一 session 串行处理，不同 session 并行
```

### 5.2 消息处理流程 `_handle(inbound)`

```
1. Slash 命令拦截
   ├─ /new     → 创建新会话，send() 回复确认
   ├─ /verbose on|off → 切换详细模式
   ├─ /help    → 返回帮助文本
   └─ /status  → 返回当前 session 信息
   命中后直接回复，不进入 Agent，不写历史

2. 获取 session
   → session_mgr.get_or_create(routing_key)

3. 附件下载（如果有）
   → downloader.download(msg_id, attachment, session_id)
   → 保存到 workspace/sessions/{sid}/uploads/{filename}
   → 构造模板消息：
     "用户发来了文件，已自动保存至沙盒路径：
     `/workspace/sessions/{sid}/uploads/{filename}`
     请根据文件内容和用户意图完成相应处理。"

4. 加载对话历史
   → session_mgr.load_history(session.id, max_turns=0)  # 完整历史

5. 发送 Loading 卡片
   → sender.send_thinking(routing_key, root_id) → card_msg_id
   → 用户看到 "思考中..." 卡片

6. 执行 Agent
   → agent_fn(user_content, history, session_id, routing_key, root_id, verbose)

7. 写入 session 历史
   → session_mgr.append(session_id, user=内容, assistant=回复)

8. 发送回复
   → 优先 update_card(card_msg_id, reply)  # PATCH 更新 Loading 卡片
   → 失败时降级为 send(routing_key, reply, root_id)
```

---

## 六、Session 管理 (`evopaw/session/manager.py`)

### 存储结构

```
data/sessions/
├── index.json              # routing_key → {active_session_id, sessions[]}
├── s-abc123.jsonl          # 第一行 meta，后续行 user/assistant 消息对
└── s-def456.jsonl
```

### 并发安全

- `index.json`：`asyncio.Lock` + write-then-rename 原子写入
- JSONL：per-session `asyncio.Lock` + `flush + fsync`

### 关键方法

| 方法 | 说明 |
|------|------|
| `get_or_create(routing_key)` | 有则返回，无则创建新 session 并写 JSONL meta 行 |
| `create_new_session(routing_key)` | `/new` 命令触发，创建新 session 切为 active |
| `load_history(session_id, max_turns)` | 解析 JSONL，跳过 meta，max_turns=0 不截断 |
| `append(session_id, user, assistant)` | 追加消息并更新 index.json 的 message_count |

---

## 七、主 Agent (`evopaw/agents/main_agent.py`)

`build_agent_fn()` 工厂函数返回闭包 `agent_fn`，这是系统最核心的函数：

### 7.1 执行步骤

```
agent_fn(user_message, history, session_id, routing_key, root_id, verbose):

  ① 构建 system prompt
     → build_bootstrap_prompt(workspace_dir)
     → 读取 soul.md / user.md / agent.md / memory.md（前200行）
     → 拼成 XML 格式 backstory

  ② 加载 ctx.json 摘要
     → load_session_ctx(session_id, ctx_dir)
     → 提取 <context_summary> 标签内容

  ③ 拼接完整 prompt
     <long_term_context>
       ctx 压缩摘要
     </long_term_context>

     <conversation_history>
       用户: 之前的消息
       助手: 之前的回复
       （已省略更早的 N 条消息。如需查阅，可通过 history_reader Skill 按页读取完整历史。）
     </conversation_history>

     用户当前消息

  ④ 图片多模态检测
     → extract_image_path(user_message) 正则匹配图片路径
     → load_image_for_claude() 将图片 base64 编码为 Claude image block
     → prompt 变为 [{"type": "text", ...}, {"type": "image", ...}]

  ⑤ 构建 session workspace
     → workspace/sessions/{session_id}/（Agent 的 cwd）

  ⑥ 构建 skill_loader MCP server
     → build_skill_loader_server(session_id, routing_key, history_all)
     → 解析 load_skills.yaml，注册 18 个 Skill
     → 从每个 SKILL.md frontmatter 提取 description，构建 XML 目录

  ⑦ 构建 Agent options
     → verbose 模式时构建 PreToolUse/PostToolUse hooks（推送到飞书）
     → thread 场景不推送 verbose
     → build_main_agent_options(model, system_prompt, cwd, max_turns, hooks, mcp_servers)

  ⑧ 调用 Claude Agent SDK
     → async for message in query(prompt, options)
     → 只取 ResultMessage.result 作为最终回复

  ⑨ 持久化
     → maybe_compress(updated_ctx)  # 超阈值时 Qwen 压缩旧消息
     → save_session_ctx()           # 覆盖写 ctx.json
     → append_session_raw()         # 追写 raw.jsonl 审计日志

  ⑩ 异步 pgvector 索引
     → asyncio.create_task(async_index_turn(...))  # 后台执行，不阻塞返回
```

### 7.2 Claude Agent SDK 调用配置

| 参数 | Main Agent | Sub-Agent |
|------|-----------|-----------|
| model | claude-sonnet-4-6 | claude-haiku-4-5 |
| max_turns | 50 | 20 |
| permission_mode | bypassPermissions | bypassPermissions |
| tools | skill_loader (MCP) | Bash, Read, Write, Edit, Grep, Glob |
| hooks | PreToolUse / PostToolUse (verbose) | 无 |

---

## 八、渐进式 Skill 披露 (`evopaw/tools/skill_loader.py`)

这是整个系统最关键的设计——**Main Agent 只有一个 MCP 工具 `skill_loader`**，所有能力通过 Skill 暴露。

### 8.1 初始化：构建 Skill 目录

```
build_skill_loader_server():
  1. 解析 load_skills.yaml → 18 个 Skill 注册表
  2. 路径穿越防护（resolve() 后检查是否在 skills_root 下）
  3. 渐进式披露第一阶段：
     从每个 SKILL.md 的 YAML frontmatter 提取 description（≤200字符）
     构建 XML 列表注入工具 description：

     <available_skills>
       <skill name="tavily_search" type="task">联网搜索（Tavily API）</skill>
       <skill name="feishu_ops" type="task">飞书文档/表格/日历操作集合</skill>
       <skill name="memory-save" type="task">将重要信息持久化到记忆文件</skill>
       ... 共 18 个
     </available_skills>

  4. @tool 装饰器 + create_sdk_mcp_server 创建 MCP server
```

### 8.2 调用时：三种分发路径

```
skill_loader(skill_name, task_context) 被 Main Agent 调用：

  ├─ 未知 Skill
  │    → 返回错误提示 + 可用 Skill 列表
  │
  ├─ history_reader（内联处理）
  │    → 直接从 history_all 分页读取
  │    → 支持 page 和 page_size 参数
  │    → 不创建 Sub-Agent
  │
  ├─ reference 类型（渐进式披露第二阶段）
  │    → 读取完整 SKILL.md 正文（剥离 frontmatter）
  │    → 替换路径占位符：{skill_base}, {session_dir}, {session_id}
  │    → 拼接 <execution_directive>
  │    → 返回给 Main Agent 自行消化推理
  │
  └─ task 类型
       → 读取完整 SKILL.md 正文
       → 替换路径占位符
       → 调用 run_skill_agent() 创建 Sub-Agent 执行
       → 返回 Sub-Agent 的执行结果
```

### 8.3 路径占位符替换

| 占位符 | 替换值 |
|--------|--------|
| `{skill_base}` | `/mnt/skills/{name}` |
| `{session_dir}` | `/workspace/sessions/{sid}` |
| `{session_id}` | 当前 session ID |

---

## 九、Sub-Agent 执行 (`evopaw/agents/skill_agent.py`)

```
run_skill_agent(skill_name, skill_instructions, task_context, session_cwd):

  1. build_sub_agent_options():
     - model: Claude Haiku 4.5（低成本）
     - allowed_tools: [Bash, Read, Write, Edit, Grep, Glob]
     - cwd: session workspace 路径
     - max_turns: 20
     - permission_mode: bypassPermissions

  2. system_prompt = SKILL.md 正文 + execution_directive
     task_context = Main Agent 传入的任务描述

  3. async for message in query(prompt=task_context, options=options)

  4. 每次调用独立 query() session → 防止状态污染
```

### Sub-Agent 执行示例（tavily_search）

```
Sub-Agent (Haiku) 收到：
  system_prompt: tavily_search SKILL.md 正文（含脚本路径、参数说明）
  prompt: '{"query": "Python 异步编程最佳实践"}'

Sub-Agent 执行：
  → Bash: python /mnt/skills/tavily_search/scripts/search.py \
          --query "Python 异步编程最佳实践"
  → 脚本自动从 /workspace/.config/tavily.json 读取 API Key
  → 返回搜索结果 JSON
  → Sub-Agent 整理结果，返回 ResultMessage
```

---

## 十、Verbose 模式 (`evopaw/agents/hooks.py`)

```
build_verbose_hooks(callback):
  → PreToolUse hook:  工具调用前发送 "🔧 即将调用工具 {tool_name}"
  → PostToolUse hook: 工具调用后发送 "✅ 工具 {tool_name} 完成"

控制逻辑（main_agent.py）：
  - verbose=True 且非 thread 场景 → 通过 sender.send_text() 推送到飞书
  - verbose=True 且 thread 场景 → callback=None，仅打印日志
  - verbose=False → 不构建 hooks

异常处理：hook 内部异常被吞没，不影响主流程
```

---

## 十一、三层记忆体系

### L1 Bootstrap — 启动时注入 system prompt

```
evopaw/memory/bootstrap.py

build_bootstrap_prompt(workspace_dir):
  workspace/soul.md    → <soul>       Agent 身份人设（稳定不变）
  workspace/user.md    → <user_profile> 用户画像（memory-save Skill 持续更新）
  workspace/agent.md   → <agent_rules>  工具使用规范
  workspace/memory.md  → <memory_index> 记忆索引前 200 行（导航骨架）
```

### L2 Context — 每轮更新

```
evopaw/memory/context_mgmt.py

存储：
  data/ctx/{sid}_ctx.json   → 压缩快照
  data/ctx/{sid}_raw.jsonl  → 完整审计日志（永不删除）

核心函数：
  load_session_ctx()   → 读取 ctx.json
  save_session_ctx()   → 覆盖写 ctx.json
  append_session_raw() → 追写 raw.jsonl
  maybe_compress()     → 超阈值时压缩：
    触发条件: approx_tokens / model_ctx_limit > 0.45
    策略: 保留 system 消息 + 最近 10 轮原文
          旧消息分块调 Qwen 生成摘要 → <context_summary> 标签
```

### L3 Vector — 异步后台索引

```
evopaw/memory/indexer.py

async_index_turn() → run_in_executor 线程池执行：
  1. 生成幂等 ID: sha256(session_id + turn_ts + user_message[:32])[:16]
  2. extract_summary_and_tags(): 调 Qwen(qwen3-max) 提取一句话摘要 + 领域标签
  3. embed_texts(): 调通义 text-embedding-v3 生成 1024 维向量
  4. upsert_memory(): INSERT ... ON CONFLICT (id) DO NOTHING 幂等写入 pgvector

db_dsn 为空时静默跳过，DB 异常只 log warning 不传播
```

### 三层记忆关系图

```
┌─ L1 Bootstrap（启动时加载）─────────────────────────┐
│  workspace/soul.md     → Agent 身份人设              │
│  workspace/user.md     → 用户画像                    │
│  workspace/agent.md    → 工具使用规范                │
│  workspace/memory.md   → 记忆索引（前200行）         │
│  → 注入 system_prompt                                │
└──────────────────────────────────────────────────────┘
         ↑ memory-save Skill 更新这些文件
         │
┌─ L2 Context（每轮更新）─────────────────────────────┐
│  ctx/{sid}_ctx.json    → 压缩快照（maybe_compress）  │
│  ctx/{sid}_raw.jsonl   → 完整审计日志（永不删除）    │
│  → ctx.json 的 <context_summary> 注入 prompt         │
└──────────────────────────────────────────────────────┘
         │ 每轮对话后异步索引
         ↓
┌─ L3 Vector（异步后台）──────────────────────────────┐
│  pgvector 表           → 摘要 + 标签 + 1024维向量   │
│  → search_memory Skill 混合搜索（向量+标签+时间）    │
│  → 不阻塞主流程                                      │
└──────────────────────────────────────────────────────┘
```

---

## 十二、消息发送 (`evopaw/feishu/sender.py`)

### FeishuSender 方法

| 方法 | 用途 | 消息格式 |
|------|------|----------|
| `send()` | Agent 回复 / Slash 命令回复 | interactive 卡片（lark_md Markdown） |
| `send_thinking()` | 发送 Loading 状态 | interactive 卡片（"思考中..."），返回 message_id |
| `update_card()` | PATCH 更新卡片内容 | 用 Agent 结果替换 Loading 文字 |
| `send_text()` | Verbose 推送 | 纯文本 |

### 路由分发

```
routing_key 前缀决定 API 调用方式：
  p2p:     → CreateMessage (receive_id_type="open_id")
  group:   → CreateMessage (receive_id_type="chat_id")
  thread:  → ReplyMessage  (reply_in_thread=True)
```

### 重试机制

```
max_retries=3, retry_backoff=(1, 2, 4) 秒
重试耗尽后记录 ERROR 日志，不抛异常（不阻断主流程）
```

---

## 十三、附件处理

### 下载 (`evopaw/feishu/downloader.py`)

```
download(msg_id, attachment, session_id):
  → 目标路径: workspace/sessions/{sid}/uploads/{filename}
  → 飞书 GetMessageResourceRequest API 下载
  → write_bytes 写入本地
  → 返回本地绝对路径，失败返回 None
```

### 图片多模态 (`evopaw/tools/add_image_tool_local.py`)

```
extract_image_path(user_message):
  → 正则匹配附件路径中的图片文件 (.jpg/.png/.gif/.webp/.bmp)

load_image_for_claude(image_path, workspace_root):
  → 路径遍历保护（必须在 workspace_root 下）
  → 文件大小限制（20MB）
  → base64 编码为 Claude 原生 image content block:
    {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
```

---

## 十四、定时任务 (`evopaw/cron/service.py`)

### 三种调度模式

| 模式 | 说明 | 示例 |
|------|------|------|
| `at` | 一次性，指定时间点 | `"at_ms": 1700000000000` |
| `every` | 固定间隔 | `"every_ms": 3600000`（每小时） |
| `cron` | cron 表达式 | `"expr": "0 9 * * 1-5"`（工作日9点） |

### 执行流程

```
主循环（50ms tick）：
  → 检测 tasks.json mtime 热重载
  → 检查到期 job
  → _fire(job):
     构造 InboundMessage(is_cron=True)
     → dispatch_fn (即 runner.dispatch)
     → 进入与普通消息相同的处理管道
  → 成功时 _post_fire: 删除 at 任务 / 推进 every/cron 下次运行
  → 失败时不推进状态（防止执行机会永久丢失）
```

---

## 十五、存储清理 (`evopaw/cleanup/service.py`)

### 清理策略

| 路径模式 | 保留天数 |
|----------|----------|
| `workspace/sessions/*/tmp/` | 1 天 |
| `workspace/sessions/*/uploads/` | 7 天 |
| `workspace/sessions/*/outputs/` | 30 天 |
| `traces/` | 30 天 |
| `sessions/*.jsonl` | 365 天 |

### 凭证注入

```
write_feishu_credentials(app_id, app_secret):
  → workspace/.config/feishu.json (权限 0o600)
  → 原子写入: write tmp → chmod → os.replace

write_tavily_credentials(api_key):
  → workspace/.config/tavily.json (权限 0o600)
  → api_key 为空时跳过
```

---

## 十六、Skill 体系

### 18 个内置 Skill

| Skill | 类型 | 说明 |
|-------|------|------|
| tavily_search | task | 联网搜索（Tavily API） |
| arxiv_search | task | 论文检索（arXiv API） |
| web_browse | reference | 网页浏览指导 |
| feishu_ops | task | 飞书操作集合（18个脚本） |
| pdf | task | PDF 读取/生成 |
| docx | task | Word 文档操作 |
| pptx | task | PPT 操作 |
| xlsx | task | Excel 操作 |
| scheduler_mgr | task | 定时任务管理（CRUD） |
| memory-save | task | 将重要信息持久化到记忆文件 |
| search_memory | task | 语义搜索历史记忆（pgvector） |
| memory-governance | reference | 记忆治理规范 |
| history_reader | reference | 分页读取完整对话历史（内联处理） |
| skill-creator | task | 创建新 Skill |
| daily-summary | reference | 每日总结模板 |
| investment-report | task | 投资报告生成 |
| investment-review | reference | 投资回顾模板 |
| investment-consult | reference | 投资咨询模板 |

### SKILL.md 格式

```yaml
---
name: tavily_search
description: 联网搜索（Tavily API），支持关键词查询和结果摘要
type: task
version: "1.0"
---

# Tavily 搜索

## 使用方法
运行搜索脚本：
```bash
python {skill_base}/scripts/search.py --query "搜索关键词"
```

## 凭证
脚本自动从 `/workspace/.config/tavily.json` 读取 API Key。

## 输出格式
JSON 格式，包含 title、url、content 字段。
```

### Skill 类型对比

| | reference | task |
|---|-----------|------|
| 执行方式 | SKILL.md 内容返回给 Main Agent 推理 | 创建 Sub-Agent (Haiku) 执行 |
| 工具能力 | 无（Main Agent 自身能力） | Bash, Read, Write, Edit, Grep, Glob |
| 成本 | 低（不额外调用模型） | 中（调用 Haiku） |
| 适用场景 | 指导性内容、模板、规范 | 需要执行脚本、操作文件 |

---

## 十七、完整时序图

以用户发送 **"帮我搜索 Python 异步编程最佳实践"** 为例：

```
[用户] ──发送消息──→ [飞书服务器]
                         │
                         │ WebSocket 推送
                         ↓
[FeishuListener]  ← 独立线程
  │ 解析 payload
  │ resolve_routing_key() → "p2p:ou_xxx"
  │ 构造 InboundMessage
  │ run_coroutine_threadsafe → 主循环
  ↓
[Runner.dispatch]
  │ 创建/复用队列 + worker
  │ 消息入队
  ↓
[Runner._handle]
  │ ① _handle_slash() → None（非 slash）
  │ ② get_or_create("p2p:ou_xxx") → session
  │ ③ 无附件
  │ ④ load_history(session.id, max_turns=0) → 完整历史
  │ ⑤ send_thinking() → card_msg_id（用户看到 "思考中..."）
  ↓
[agent_fn]
  │ ⑥ build_bootstrap_prompt() → system_prompt
  │ ⑦ load_session_ctx() → 加载压缩摘要
  │ ⑧ 拼接 prompt: ctx + history + 用户消息
  │ ⑨ build_skill_loader_server() → MCP server（18 个 Skill 目录）
  │ ⑩ query(prompt, options) → Claude Sonnet 4.6
  ↓
[Claude Sonnet 4.6]  ← Main Agent
  │ 看到 <available_skills> 中有 tavily_search
  │ 决定调用 skill_loader("tavily_search", '{"query":"Python 异步编程"}')
  ↓
[skill_loader MCP handler]
  │ tavily_search 是 task 类型
  │ 读取 SKILL.md → 替换路径占位符
  │ run_skill_agent()
  ↓
[Claude Haiku 4.5]  ← Sub-Agent
  │ system_prompt = SKILL.md 正文
  │ 执行: Bash → python /mnt/skills/tavily_search/scripts/search.py
  │ 脚本从 /workspace/.config/tavily.json 读 API Key
  │ 返回搜索结果
  ↓
[回到 skill_loader → 回到 Main Agent]
  │ Main Agent 整合搜索结果
  │ 生成最终回复 → ResultMessage
  ↓
[agent_fn 后续]
  │ maybe_compress() + save_session_ctx()
  │ append_session_raw()
  │ asyncio.create_task(async_index_turn()) → 后台 pgvector 索引
  ↓
[Runner._handle 继续]
  │ session_mgr.append() → 写入 JSONL 历史
  │ update_card(card_msg_id, reply) → PATCH 更新卡片
  ↓
[飞书服务器] ──推送更新──→ [用户]
  "思考中..." 卡片内容被替换为搜索结果回复
```

---

## 十八、关键设计要点

| 设计 | 说明 |
|------|------|
| **渐进式披露** | Main Agent 只看 Skill 目录摘要（~200字/个），调用时才加载完整 SKILL.md，节省 context window |
| **双层 Agent** | Sonnet 理解意图 + 选 Skill；Haiku 执行脚本。成本可控，能力分层 |
| **单 MCP 工具** | Main Agent 只有 `skill_loader` 一个工具，所有能力通过 Skill 暴露 |
| **per-routing_key 队列** | 同 session 串行防写冲突，不同 session 并行不阻塞，worker 空闲超时退出 |
| **凭证不进 LLM** | 写到 workspace/.config/ 文件，Skill 脚本直接读，防止 API Key 泄露 |
| **Loading 卡片 + PATCH** | 先发 "思考中..." 拿到 msg_id，完成后 PATCH 更新，用户体验流畅 |
| **Sub-Agent 短生命周期** | 每次 Skill 调用创建独立 query() session，执行完销毁，防止状态污染 |
| **write-then-rename** | index.json、tasks.json、ctx.json 都用原子写入，防止崩溃时数据损坏 |
| **三层记忆** | L1 Bootstrap（人设+画像）→ L2 Context（压缩快照）→ L3 Vector（语义搜索），各层独立演进 |
| **热重载** | CronService 检测 tasks.json mtime 变化自动重载，无需重启进程 |

---

## 十九、目录结构速查

```
evopaw/
├── main.py                          # 进程入口，组装所有模块
├── models.py                        # InboundMessage / Attachment / SenderProtocol
├── runner.py                        # 执行引擎（队列 + slash + agent 调度）
├── llm/
│   └── claude_client.py             # Claude Agent SDK 配置工厂
├── feishu/
│   ├── listener.py                  # WebSocket 事件 → InboundMessage
│   ├── sender.py                    # 消息发送（卡片 + Loading + 重试）
│   ├── downloader.py                # 附件下载到 session workspace
│   └── session_key.py               # routing_key 解析
├── agents/
│   ├── main_agent.py                # 主 Agent（三层记忆 + 图片多模态 + verbose hooks）
│   ├── skill_agent.py               # Sub-Agent（SKILL.md 作为 system_prompt）
│   └── hooks.py                     # Verbose 模式 PreToolUse/PostToolUse hooks
├── tools/
│   ├── skill_loader.py              # SkillLoaderTool MCP Server（渐进式披露核心）
│   └── add_image_tool_local.py      # 图片 base64 编码（Claude image block）
├── memory/
│   ├── bootstrap.py                 # soul/user/agent/memory.md → system prompt
│   ├── context_mgmt.py              # ctx.json 压缩 + raw.jsonl 审计
│   └── indexer.py                   # pgvector 异步写入（摘要+标签+向量）
├── session/
│   ├── manager.py                   # SessionManager（index.json + JSONL）
│   └── models.py                    # SessionEntry / MessageEntry
├── cron/
│   └── service.py                   # CronService（at/every/cron + 热重载）
├── cleanup/
│   └── service.py                   # 存储清理 + 凭证注入
├── api/
│   ├── test_server.py               # TestAPI（aiohttp HTTP 服务）
│   └── capture_sender.py            # CaptureSender（测试用 Future 捕获）
├── observability/
│   ├── logging_config.py            # 日志配置
│   ├── metrics.py                   # Prometheus 指标定义
│   └── metrics_server.py            # Metrics HTTP 端点
└── skills/                          # 18 个内置 Skill
    ├── load_skills.yaml             # Skill 注册表
    ├── tavily_search/SKILL.md       # 联网搜索
    ├── feishu_ops/SKILL.md          # 飞书操作集合
    ├── memory-save/SKILL.md         # 记忆持久化
    ├── search_memory/SKILL.md       # 语义搜索记忆
    └── ...
```
