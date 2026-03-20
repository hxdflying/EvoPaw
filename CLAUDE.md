# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**XiaoPaw (小爪子)** is a Feishu (Lark) local work assistant that uses a Skills ecosystem to give an AI agent extensible tool capabilities, with all execution isolated in AIO-Sandbox (Docker). It connects via Feishu WebSocket (no public IP needed), making it suitable for local/intranet deployment.

The design document is in `DESIGN.md` (Chinese, compressed overview). Detailed sub-module docs are in `docs/`:
- `docs/design-modules.md` — §4 模块设计（FeishuListener, Runner, Main Agent, Sub-Crew, CronService, TestAPI 等）
- `docs/design-data.md` — §5 数据设计（Session 存储、Trace、CronJob、Skill 定义、SkillLoaderTool I/O）
- `docs/design-api.md` — §6 接口设计（飞书消息收发、文件下载接口）
- `docs/design-observability.md` — §13 可观测性（日志规范、Prometheus 指标、/metrics 接口）

All code and comments should be written in Chinese where user-facing, English for code identifiers.

## Tech Stack

- **Python** (async, asyncio-based)
- **CrewAI** — Agent orchestration (Main Agent + Sub-Crew pattern)
- **lark-oapi** — Feishu SDK (WebSocket client + REST API)
- **Qwen3-max** — LLM model for agents
- **AIO-Sandbox** — Docker-based MCP server for isolated code execution
- **croniter** — Cron expression parsing for scheduled tasks
 - **prometheus_client** — Metrics export for observability
 - **AliyunLLM adapter** — Custom CrewAI `BaseLLM` implementation in `xiaopaw/llm/aliyun_llm.py` for calling Qwen via DashScope-compatible API (supports retries, function calling, multimodal image inputs)

## Architecture (Key Concepts)

**Message flow**: Feishu WebSocket → FeishuListener → SessionRouter (routing_key) → Runner → Main Agent → SkillLoaderTool → Sub-Crew (sandbox) → FeishuSender

**Three routing key types**: `p2p:{open_id}` (DM), `group:{chat_id}` (group chat), `thread:{chat_id}:{thread_id}` (topic thread)

**Two Skill types**:
- **reference** — SKILL.md content returned to Main Agent for self-reasoning
- **task** — Spawns an isolated Sub-Crew with sandbox MCP tools (4-tool whitelist: bash, code, file_ops, editor)

**Main Agent has exactly one tool**: `SkillLoaderTool` (progressive disclosure pattern). All other capabilities come through Skills.

**Credentials never enter the LLM** — written to sandbox `.config/feishu.json` at startup, read directly by Skill scripts.

## Module Layout

```
xiaopaw/
├── main.py                  # Entry: starts Listener + CronService + CleanupService + TestAPI
├── config.yaml              # Workspace config (feishu creds via env vars)
├── llm/
│   └── aliyun_llm.py        # AliyunLLM: CrewAI BaseLLM adapter for Aliyun Qwen
├── feishu/
│   ├── listener.py          # WebSocket event → InboundMessage
│   ├── downloader.py        # File/image download to session uploads/
│   ├── sender.py            # Send messages (create/reply), routing_key-aware
│   └── session_key.py       # routing_key resolution
├── api/
│   └── test_server.py       # Test API (aiohttp): simulate Feishu events, debug mode only
├── runner.py                # Core orchestrator: session → slash cmd → agent → store → send
├── agents/
│   ├── main_crew.py         # Main Crew (single SkillLoaderTool)
│   └── skill_crew.py        # Sub-Crew factory (build_skill_crew)
├── tools/
│   ├── skill_loader.py          # SkillLoaderTool (progressive disclosure + Sub-Crew trigger)
│   ├── add_image_tool_local.py  # AddImageToolLocal: local image → base64 data URL for multimodal LLM
│   ├── baidu_search_tool.py     # BaiduSearchTool: Baidu Qianfan web_search wrapper
│   └── intermediate_tool.py     # IntermediateTool: save intermediate thinking products
├── observability/
│   ├── logging_config.py        # Logging setup: console + JSON log file in data/logs/xiaopaw.log
│   ├── metrics.py               # Prometheus metrics definitions and helper functions
│   └── metrics_server.py        # Lightweight aiohttp server exposing /metrics for Prometheus
├── session/
│   ├── manager.py           # index.json + JSONL read/write
│   └── models.py            # Session / SessionEntry dataclasses
├── cron/
│   ├── service.py           # asyncio timer + mtime+size hot-reload
│   └── models.py            # CronJob / CronSchedule / CronPayload
├── cleanup/
│   └── service.py           # Storage cleanup by policy (sweep + ensure_workspace_dirs + write_feishu/baidu_credentials)
├── skills/                  # SKILL.md + scripts per skill
│   ├── pdf/                 # PDF parsing and text extraction
│   ├── docx/                # Word document processing
│   ├── pptx/                # PowerPoint processing
│   ├── xlsx/                # Excel spreadsheet processing
│   ├── feishu_ops/          # Read docs, send messages via Feishu API
│   ├── scheduler_mgr/       # Create/list/delete cron jobs (config only, not execution)
│   ├── baidu_search/        # Baidu Qianfan web search (credentials: .config/baidu.json)
│   ├── web_browse/          # Web content extraction + browser automation (browser_* tools)
│   └── history_reader/      # Paginated conversation history reader (reference skill)
└── data/                    # Runtime data (.gitignore)
    ├── sessions/            # index.json + {sid}.jsonl
    ├── traces/              # Full LLM execution traces
    ├── cron/tasks.json      # Scheduled task configs
    └── workspace/           # Per-session file workspace (mounted into sandbox)
```

## Key Design Decisions

- **Slash commands** (`/new`, `/verbose`, `/help`, `/status`) are intercepted in Runner *before* entering the Agent
- **Per-routing_key message queue** — same session messages are processed serially via `asyncio.Queue`; different sessions run in parallel. Worker auto-exits after idle timeout.
- **File concurrency** — `asyncio.Lock` for in-process mutual exclusion; `write-then-rename` for atomic JSON writes; `flush + fsync` for JSONL appends. No cross-process file locks (single-process architecture).
- **CronService** uses asyncio precise timers (not polling), with mtime-based hot-reload of `tasks.json`
- **Sub-Crew instances are ephemeral** — new instance per Skill invocation to prevent state contamination
- **Verbose mode** streams Main Agent's Thought via `step_callback` to Feishu (disabled for thread chats and Sub-Crews)
- **Session workspace** is mounted into Docker: `./data/workspace:/workspace` — Sub-Crew accesses files at `/workspace/sessions/{sid}/`
- **SkillLoaderTool I/O** uses Pydantic models (`SkillLoaderInput` / `SkillResult`) with `errcode=0` for success
- **All timeouts and limits are configurable** in `config.yaml` (agent max_iter, sandbox timeout, queue size, sender retries, etc.)
- **Test API** (`debug.enable_test_api`) — HTTP endpoint simulates Feishu events via `POST /api/test/message`, injects into Runner with a `CaptureSender` that returns bot replies synchronously. Runner accepts a `SenderProtocol` (production: `FeishuSender`, test: `CaptureSender`). Supports file attachment upload via `attachment.file_path` field.
- **session_id security** — session_id never enters the LLM context (not in `tasks.yaml` template, not in `akickoff(inputs={})`). SkillLoaderTool holds it as `_session_id` PrivateAttr and injects only the actual path strings into tool descriptions. LLM cannot read or manipulate session_id.
- **history_reader inline** — `history_reader` Skill is handled inline by SkillLoaderTool (no Sub-Crew, no sandbox). SkillLoaderTool receives the full history list via `history_all` constructor param and paginates from memory. LLM passes only `page` / `page_size`.
- **Sub-Crew MCP tools** — All AIO-Sandbox MCP tools are exposed (no whitelist filter). Tool constraints are enforced via Agent backstory behavioral rules instead of `create_static_tool_filter`. This enables `browser_*` tools for `web_browse` Skill.
- **SKILL.md template variable safety** — `_get_skill_instructions()` escapes `{var}` → `{{var}}` in SKILL.md content. `_CREWAI_VAR_PATTERN` then collects all variables CrewAI's regex would still find (inside `{{var}}`), injecting them as self-mapping inputs to `akickoff()` to prevent "Template variable not found" errors.
- **feishu_ops script architecture** — feishu_ops Skill uses standalone Python scripts under `skills/feishu_ops/scripts/`. Each operation (send_text, send_image, send_file, read_doc, read_sheet, get_chat_members, list_events, create_event) is a separate script sharing `_feishu_auth.py`. All output JSON to stdout, exit 0. Credentials read from `/workspace/.config/feishu.json`.
- **baidu_search credentials** — `CleanupService.write_baidu_credentials()` writes BAIDU_API_KEY to sandbox `.config/baidu.json` at startup (mode 0600). If key is empty, skipped silently (Skill unavailable but main flow unaffected).

## Data Formats

- **Session index**: `data/sessions/index.json` — maps routing_key → active_session_id + session metadata
- **Conversation history**: `data/sessions/{sid}.jsonl` — clean message log (meta line + user/assistant pairs)
- **Traces**: `data/traces/{sid}/{ts}_{msg_id}/` — meta.json + main.jsonl + skills/*.jsonl
- **Cron jobs**: `data/cron/tasks.json` — three schedule kinds: `at` (one-shot), `every` (interval), `cron` (cron expr)
- **Skill definitions**: `skills/{name}/SKILL.md` with YAML frontmatter (name, description, type, version)

## Sandbox Tool Whitelist

Sub-Crews connect to AIO-Sandbox MCP with all tools exposed (no whitelist filter since web_browse Skill requires browser_* tools).
Core tools: `sandbox_execute_bash`, `sandbox_execute_code`, `sandbox_file_operations`, `sandbox_str_replace_editor`, `sandbox_convert_to_markdown`
Browser tools: `browser_navigate`, `browser_get_markdown`, `browser_screenshot`, `browser_get_clickable_elements`, and other `browser_*` tools.
Tool constraints are enforced via Agent backstory behavioral rules.

## Commands

```bash
# Run all unit tests with coverage
python3 -m pytest tests/unit/ -v --cov=xiaopaw --cov-report=term-missing

# Run a single test file
python3 -m pytest tests/unit/test_cron_service.py -v

# Run a specific test
python3 -m pytest tests/unit/test_runner.py::TestSlashNew::test_creates_new_session -v

# Run integration tests (no LLM)
python3 -m pytest tests/integration/ -m "not llm" -v

# Run integration tests (with LLM)
export QWEN_API_KEY=<your_key>
python3 -m pytest tests/integration/test_e2e_conversation.py -m "llm and not sandbox" -v -s
```

## Development Progress

**All modules implemented** (with tests):
- `xiaopaw/models.py` — InboundMessage, Attachment, SenderProtocol (send/send_thinking/update_card/send_text)
- `xiaopaw/session/models.py` — SessionEntry, RoutingEntry, MessageEntry
- `xiaopaw/session/manager.py` — SessionManager (index.json + JSONL, concurrent-safe)
- `xiaopaw/api/capture_sender.py` — CaptureSender (future-based reply capture，支持 send_thinking/update_card/send_text)
- `xiaopaw/api/schemas.py` — TestRequest, TestResponse (Pydantic)
- `xiaopaw/api/test_server.py` — TestAPI (aiohttp, wired with Runner + SessionManager)
- `xiaopaw/runner.py` — Runner (per-routing_key queue, slash commands, send_thinking Loading 卡片 + update_card 替换)
- `xiaopaw/feishu/session_key.py` — resolve_routing_key (pure function)
- `xiaopaw/cron/models.py` — CronJob, CronSchedule, CronPayload, CronState
- `xiaopaw/cron/service.py` — CronService (tick-based scheduler, mtime+size hot-reload)
- `xiaopaw/feishu/listener.py` — FeishuListener (im.message.receive_v1 + im.chat.member.bot.added_v1，post 富文本解析，allowed_chats 白名单)
- `xiaopaw/feishu/sender.py` — FeishuSender (send: interactive 卡片 lark_md 格式；send_thinking/update_card 加载效果；send_text 纯文本)
- `xiaopaw/feishu/downloader.py` — FeishuDownloader: 附件下载到 workspace/sessions/{sid}/uploads/
- `xiaopaw/main.py` — Full entry point: load config.yaml, start all services (Listener + CronService + CleanupService + TestAPI + metrics)
- `xiaopaw/llm/aliyun_llm.py` — AliyunLLM: CrewAI `BaseLLM` adapter for Qwen (sync/async, retries, function calling, multimodal)
- `xiaopaw/tools/add_image_tool_local.py` — AddImageToolLocal: 本地图片 → base64 data URL，含路径遍历防护
- `xiaopaw/tools/baidu_search_tool.py` — BaiduSearchTool: 百度千帆 web_search 封装
- `xiaopaw/tools/intermediate_tool.py` — IntermediateTool: 中间思考产物保存
- `xiaopaw/tools/skill_loader.py` — SkillLoaderTool: 渐进式披露 + Sub-Crew 触发
- `xiaopaw/observability/metrics.py` — Prometheus metrics 定义与 helper 函数
- `xiaopaw/observability/metrics_server.py` — /metrics aiohttp 服务（含 AppRunner 清理）
- `xiaopaw/agents/main_crew.py` — MainCrew: build_agent_fn() 工厂，YAML prompts，verbose step_callback，历史截断
- `xiaopaw/agents/skill_crew.py` — SubCrew: build_skill_crew() 工厂，AIO-Sandbox MCP 接入，4工具白名单
- `xiaopaw/agents/models.py` — MainTaskOutput Pydantic 输出模型
- `xiaopaw/agents/config/agents.yaml` — Orchestrator Agent 配置（role/goal/backstory/max_iter）
- `xiaopaw/agents/config/tasks.yaml` — 主任务配置（description/expected_output）
- `xiaopaw/cleanup/service.py` — CleanupService: 启动时 sweep + ensure_workspace_dirs + write_feishu_credentials + write_baidu_credentials
- `xiaopaw/skills/history_reader/SKILL.md` — history_reader Skill（内联分页读取历史，无需沙盒，v2.0）
- `xiaopaw/skills/feishu_ops/SKILL.md` + `scripts/` — feishu_ops Skill（10个独立脚本：send_text/post/image/file, read_doc/sheet, get_chat_members, list/create_events；共享 _feishu_auth.py）
- `xiaopaw/skills/scheduler_mgr/SKILL.md` — scheduler_mgr Skill（定时任务创建/查看/删除，task 型）
- `xiaopaw/skills/baidu_search/SKILL.md` + `scripts/search.py` — baidu_search Skill（百度千帆搜索，凭证注入 .config/baidu.json，task 型）
- `xiaopaw/skills/web_browse/SKILL.md` — web_browse Skill（Markdown 快速提取 + browser_* 浏览器自动化，task 型）
- `xiaopaw/skills/pdf/` `docx/` `pptx/` `xlsx/` — 文件处理 Skills（task 型）
- `tests/integration/test_file_pipeline.py` — 文件处理全链路集成测试（P1附件复制、P2文件意图识别、P3全链路）
- `tests/unit/test_feishu_ops_scripts.py` — feishu_ops 脚本单元测试（30个，覆盖所有脚本）

**Test stats**: 562 unit tests, 86% coverage ✅ | 29 integration tests (no-llm)

## Known Issues / Code Quality

Full report: run `/everything-claude-code:python-review` to re-evaluate.

Last review: 2026-03-06. All CRITICAL and HIGH issues fixed. Remaining MEDIUM:

| # | File | Issue | Status |
|---|------|-------|--------|
| M1 | `aliyun_llm.py:110-139` | `_normalize_multimodal_tool_result` 用脆弱字符串匹配检测图片 URL，易被注入破坏 | open |

**Recently added features** (2026-03-10):
- baidu_search Skill：百度千帆搜索，BAIDU_API_KEY 写入 .config/baidu.json
- web_browse Skill：sandbox_convert_to_markdown + browser_* 全工具浏览器自动化
- Sub-Crew 移除 MCP 白名单（create_static_tool_filter），改用 backstory 行为约束
- logging_config.py 修复：新增 console handler，root logger 显式设为 INFO 级别
- QWEN_DEBUG_PAYLOAD 环境变量：控制是否输出完整 LLM 请求 payload
- SkillLoaderTool：_CREWAI_VAR_PATTERN 修复 CrewAI "Template variable not found" 报错
- sandbox_directive 扩展到 21 个工具（含 browser_* 系列和环境探查工具）

**Recently added features** (2026-03-09):
- Thinking/Loading UI：send_thinking() 发起"⏳ 思考中..."卡片，返回 card_msg_id；update_card() PATCH 更新卡片展示 Agent 最终回复
- Interactive 卡片 + Markdown 渲染：send() 现在发送 lark_md 格式的交互式卡片（不再纯文本）
- Post 富文本解析：FeishuListener 新增 _extract_post_text() 静态方法，正确处理 msg_type="post" 消息
- Bot 入群事件：FeishuListener 监听 im.chat.member.bot.added_v1，通过可选回调 on_bot_added 解耦
- Allowed chats 白名单：FeishuListener 支持可选参数 allowed_chats，p2p 始终放行；群消息和入群事件检查白名单
- CaptureSender 扩展：新增对应 stub 方法 send_thinking/update_card/send_text，TestAPI 完全支持新 UI
