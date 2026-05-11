# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**EvoPaw (小爪子)** — 基于 Skills 生态的飞书工作助手。Main Agent + Sub-Agent 双层架构，通过 SkillLoader 渐进式披露能力，所有执行在容器内隔离。飞书 WebSocket 长连接，无需公网 IP。

设计文档在 `docs/` 目录。面向用户的文案与注释用中文，代码标识符用英文。

## Tech Stack

- **多 Provider Agent 后端** — 三族 backend 共用 `AgentBackend.run_turn()` 协议
  - `claude_sdk_compat`：Claude Agent SDK（CLI OAuth 路径）
  - `anthropic_messages`：Anthropic `/v1/messages` httpx 直连
  - `openai_chat`：OpenAI 兼容 `chat/completions` httpx 直连
- **默认模型**：主 Agent = Claude Sonnet 4.6，Sub-Agent = Claude Haiku 4.5（成本优化）
- **可选 Provider**（OpenAI 兼容族）：DashScope/Qwen、Moonshot、DeepSeek、OpenRouter、本地 vLLM 等
- **lark-oapi** — 飞书 SDK（WebSocket 事件 + REST 调用）
- **DashScope/Qwen** — 记忆摘要压缩、向量化、记忆抽取（默认 memory 角色后端）
- **DashScope Fun-ASR** — 飞书语音消息实时转写（WebSocket）
- **PostgreSQL 16 + pgvector** — 语义搜索记忆
- **aiohttp** — TestAPI HTTP 服务（联调用）
- **prometheus_client** — 指标暴露

## Architecture

**主消息流**：

```
Feishu WebSocket
  → FeishuListener (事件解析)
  → InboundMessage
  → Runner (per-routing_key 队列 + Slash 命令拦截 + ASR 转写)
  → Main Agent (build_agent_fn)
       ├─ memory.bootstrap   注入 soul/user/agent/memory.md
       ├─ memory.context_mgmt 加载 ctx.json 摘要
       ├─ content_builders   按 provider 构造多模态 user_content
       └─ AgentBackend.run_turn(TurnRequest)
            └─ SkillDispatcher.dispatch(skill_name, task_context)
                  ├─ history_reader：内联分页返回
                  ├─ reference 型：返回 SKILL.md 包裹文本
                  └─ task 型：run_skill_agent → Sub-Agent (Bash/Read/Write/Edit/Grep/Glob)
  → FeishuSender (卡片消息 + Loading 效果)
```

**三种路由**：`p2p:{open_id}` / `group:{chat_id}` / `thread:{chat_id}:{thread_id}`。

**两种 Skill 类型**：
- **reference** — SKILL.md 内容直接回填给 Main Agent 自行推理（不创建 Sub-Agent）
- **task** — 触发 Sub-Agent 执行；可在 frontmatter 声明 `execution.mode: background` 走「立即返回 + 后台执行 + 完成回推」

**Main Agent 工具表面**：仅 `skill_loader` 一个工具入口（在 SDK 路径下走 MCP，在 HTTP 路径下走 SkillDispatcher tool_schema）。所有外部能力通过 Skills 暴露；system prompt 内显式禁用 Claude CLI 内置 skill。

**凭证不进入 LLM** — 写到 workspace `.config/feishu.json`，Skill 脚本直接读文件。

**三层记忆**：
- L1 Bootstrap：`workspace/soul|user|agent/memory.md` → 注入 system prompt
- L2 Context：`ctx.json` 压缩快照（轮次前加载、轮次后保存）+ `raw.jsonl` 完整审计
- L3 Vector：pgvector 异步索引（`asyncio.create_task`），`search_memory` Skill 混合搜索

## Module Layout

```
evopaw/
├── main.py                     # 进程入口（resolve_runtime + 启动 listener/cron/cleanup/metrics/TestAPI）
├── models.py                   # InboundMessage / Attachment / SenderProtocol
├── runner.py                   # 执行引擎（per-routing_key 队列、Slash 命令、ASR 集成）
├── llm/
│   └── claude_client.py        # Claude SDK 配置工厂 + CLI 检测
├── provider_runtime/           # 多 Provider 抽象层
│   ├── capabilities.py         # RuntimeFamily / supports_streaming / supports_tool_calls
│   ├── models.py               # ProviderSpec / ResolvedRuntime / RoleConfig
│   ├── registry.py             # 内置 provider 表（claude_sdk / anthropic / dashscope）
│   └── resolve.py              # roles.<role> → ResolvedRuntime
├── agent_backends/             # AgentBackend 协议族（懒加载单例）
│   ├── base.py                 # AgentBackend / TurnRequest / TurnResult / StreamSink / ToolGate
│   ├── claude_sdk.py           # ClaudeSDKCompatBackend（封装 query()）
│   ├── anthropic_messages.py   # AnthropicMessagesBackend（httpx 直连）
│   ├── openai_chat.py          # OpenAIChatBackend（httpx 直连）
│   └── _http_chat_base.py      # HTTP backend 共用 tool-call 循环
├── content_builders/           # 多模态 user_content 跨 provider 构造
│   ├── claude_blocks.py        # Claude/Anthropic 原生 image block
│   └── openai_blocks.py        # OpenAI image_url data: scheme
├── skills_runtime/             # 跨 backend 共享的 Skill registry/dispatcher
│   ├── registry.py             # 扫描 SKILL.md frontmatter
│   ├── instructions.py         # 构造 description XML / SKILL.md 注入文本
│   ├── placeholders.py         # 占位符渲染（路径、凭证名等）
│   ├── dispatcher.py           # SkillDispatcher（含 history_reader 内联 + 后台 task）
│   ├── tool_schema.py          # 通用 tool schema 生成
│   └── adapters/
│       ├── claude_mcp.py       # Claude SDK MCP server adapter
│       ├── anthropic_tools.py  # Anthropic tools 适配
│       └── openai_tools.py     # OpenAI tools 适配
├── tools/
│   └── add_image_tool_local.py # 检测附件图片路径 + 加载为 base64
├── feishu/
│   ├── listener.py             # WebSocket 事件 → InboundMessage
│   ├── sender.py               # 消息发送（卡片 + Loading）
│   ├── downloader.py           # 附件下载到 session workspace
│   └── session_key.py          # routing_key 解析
├── asr/
│   ├── service.py              # SpeechRecognitionService（one-shot 转写）
│   ├── funasr_realtime_client.py # Fun-ASR WebSocket 客户端
│   └── models.py               # AsrResult / AsrFailure
├── agents/
│   ├── main_agent.py           # build_agent_fn 工厂
│   ├── skill_agent.py          # run_skill_agent（Sub-Agent）
│   ├── sub_agent_registry.py   # Sub-Agent 注册表
│   ├── response_finalizer.py   # 最终回复改写器（Composite）
│   ├── hooks.py                # FeishuStreamSink（verbose 模式事件推送）
│   └── config/                 # 角色级提示词配置
├── memory/
│   ├── bootstrap.py            # soul/user/agent/memory.md 注入
│   ├── context_mgmt.py         # ctx.json 压缩 + raw.jsonl 审计
│   ├── indexer.py              # pgvector 异步写入
│   └── _dashscope_clients.py   # DashScope embedding / chat 客户端
├── session/                    # SessionManager（index.json + JSONL，asyncio.Lock）
├── cron/                       # CronService（asyncio timer + tasks.json mtime 热加载）
├── cleanup/                    # CleanupService（过期文件清理 + 凭证写入）
├── observability/              # logging_config + Prometheus metrics
├── api/                        # TestAPI（aiohttp）+ CaptureSender
└── skills/                     # 19 个内置 SKILL.md + 执行脚本（load_skills.yaml 索引）
```

## Key Design Decisions

- **AgentBackend 协议** — 三族 backend 共用 `run_turn(TurnRequest) -> TurnResult`；`backend_hints` 字段做私有透传（SDK 路径携带 MCP server，HTTP 路径携带 SkillDispatcher）
- **Provider 解析** — `resolve_runtime(role)` 把 `roles.main / subagent / memory_summary / memory_embedding / memory_extract` 解析为 `ResolvedRuntime`（含 runtime_family、provider_id、model、api_key、api_base、supports_vision 等）
- **ToolGate** — HTTP backend 在 `dispatcher.dispatch` 前可拦截/改写工具调用；SDK 路径由 SDK 自管（不接 ToolGate）
- **多模态降级** — `pick_content_builder(runtime_family)` 按 provider 构图；`supports_vision=False` 的 runtime 自动降级为纯文本提示，附件图片不会构成非法 block
- **Slash 命令**（`/new` `/stop` `/verbose` `/help` `/status`）在 Runner 层拦截，不进入 Agent。`/stop` 走快路径不进队列
- **Per-routing_key 队列** — 同 session 串行（`asyncio.Queue`），不同 session 并行；worker 空闲超时（默认 300s）自动退出
- **ASR 集成** — Runner 检测语音附件即调用 `SpeechRecognitionService.transcribe_file`，长音频先发 ack 文案，转写文本与回答按 `display_transcript` 控制是否同时展示
- **文件并发** — `asyncio.Lock` 进程内互斥；`write-then-rename` 原子 JSON；`flush + fsync` JSONL 追写
- **CronService** 使用 asyncio 精确 timer；`tasks.json` mtime 变更触发热加载
- **Sub-Agent 短生命周期** — 每次 Skill 调用创建独立 session 防状态污染；`execution.mode: background` 走异步路径
- **Verbose 模式** — `FeishuStreamSink` 在工具调用前后推送事件；thread 场景和 Sub-Agent 不推送（避免噪声）
- **Session workspace** — `workspace/sessions/{sid}/`，Sub-Agent 的 cwd
- **ctx.json 长期上下文** — 轮次前加载摘要注入 prompt，轮次后保存压缩快照 + 追写 raw.jsonl
- **pgvector 异步索引** — 每轮对话后 `asyncio.create_task(async_index_turn(...))`，不阻塞主流程
- **history_reader 内联** — 直接在 SkillDispatcher 内分页返回，不创建 Sub-Agent
- **TestAPI** — `debug.enable_test_api` 开启 HTTP 端点，`CaptureSender` 拦截回复同步返回（联调用）
- **session_id 安全** — session_id 不进入 LLM context，dispatcher 仅注入沙箱路径字符串
- **工具约束** — Main Agent system prompt 显式禁用 Claude CLI 内置 skill（schedule/loop/init 等），所有能力必须经 `skill_loader`

## Data Formats

- **Session index**：`data/sessions/index.json` — routing_key → active_session_id + metadata
- **对话历史**：`data/sessions/{sid}.jsonl` — meta 头 + user/assistant 消息对
- **Context 快照**：`data/ctx/{sid}_ctx.json` — 压缩摘要
- **审计日志**：`data/ctx/{sid}_raw.jsonl` — 完整对话记录
- **Cron jobs**：`data/cron/tasks.json` — at / every / cron 三种调度
- **Skill 定义**：`evopaw/skills/{name}/SKILL.md` — YAML frontmatter（name / description / type / version / 可选 execution.mode）
- **Skill 索引**：`evopaw/skills/load_skills.yaml` — 启用清单（name / type / enabled）

## Configuration

- `config.yaml`（实际）/ `config.yaml.template`（样板）控制：飞书凭证、`agent.*` 通用参数、`providers + roles` 多 provider 绑定、`memory.db_dsn`、`asr.*` Fun-ASR 参数、`runner.*` 队列与去重、`debug.enable_test_api` 等
- 内置 provider（无需声明）：`claude_sdk` / `anthropic` / `dashscope`
- 自定义 provider 必须声明 `runtime_family + api_key_env + default_api_base`（样板见 template 内 moonshot 示例）
- 内置角色：`main` / `subagent` / `memory_summary` / `memory_embedding` / `memory_extract`

## Commands

```bash
# 全量单元测试（含覆盖率）
python3 -m pytest tests/unit/ -v --cov=evopaw --cov-report=term-missing

# 单个测试文件
python3 -m pytest tests/unit/test_main_agent.py -v

# 集成测试（不需要 LLM）
python3 -m pytest tests/integration/ -m "not llm" -v

# 集成测试（需要 Anthropic API Key）
ANTHROPIC_API_KEY=sk-ant-xxx python3 -m pytest tests/integration/ -m "llm" -v -s

# 启动 pgvector（语义记忆依赖）
docker compose -f pgvector-docker-compose.yaml up -d

# 一键启动（Bot + pgvector）
docker compose up -d

# 本地直跑（需要 config.yaml 的 db_dsn 指向 localhost）
python3 -m evopaw.main
```

## Development Status

**主要模块**：
- `evopaw/agents/main_agent.py` — Main Agent 装配（system prompt + 三层记忆 + 多模态 + verbose hooks + backend 调度）
- `evopaw/agents/skill_agent.py` — Sub-Agent（SKILL.md 作为 system prompt，工具集 = Bash/Read/Write/Edit/Grep/Glob）
- `evopaw/agent_backends/` — 三族 backend 实现 + ToolGate / StreamSink 协议
- `evopaw/provider_runtime/` — Provider/Role 解析层
- `evopaw/skills_runtime/` — Skill 注册表 + 跨 backend 分发器 + adapter
- `evopaw/content_builders/` — 多模态 user_content 跨 provider 构造
- `evopaw/runner.py` — 执行引擎（队列 + Slash + ASR + 卡片消息）
- `evopaw/feishu/` — 飞书接入层
- `evopaw/asr/` — Fun-ASR 实时转写
- `evopaw/memory/` — 三层记忆（bootstrap + context_mgmt + indexer）
- `evopaw/session/` `evopaw/cron/` `evopaw/cleanup/` `evopaw/observability/` `evopaw/api/`

**19 个内置 Skills**（`evopaw/skills/load_skills.yaml`）：
- 文件处理：`pdf` / `docx` / `pptx` / `xlsx`
- 飞书与调度：`feishu_ops` / `scheduler_mgr`
- 信息检索：`tavily_search` / `arxiv_search` / `web_browse`
- 历史与记忆：`history_reader`（reference）/ `memory-save` / `search_memory` / `memory-governance`
- 元能力：`skill-creator`
- 业务模板：`daily-summary` / `investment-report` / `investment-review` / `investment-consult` / `hk-investment-morning-report`
