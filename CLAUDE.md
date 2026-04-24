# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**EvoPaw (小爪子)** — 基于 Claude Agent SDK 的飞书工作助手。通过 Skills 生态实现可扩展的工具调用，所有执行在容器内隔离。飞书 WebSocket 长连接，无需公网 IP。

设计文档在 `docs/` 目录。代码和注释用户面用中文，代码标识符用英文。

## Tech Stack

- **Claude Agent SDK** (`claude-agent-sdk`) — Agent 编排（Main Agent + Sub-Agent）
- **Claude Sonnet 4.6** — 主 Agent 模型
- **Claude Haiku 4.5** — Sub-Agent 模型（低成本）
- **lark-oapi** — 飞书 SDK（WebSocket + REST）
- **Qwen** (via OpenAI 兼容 API) — 记忆摘要压缩 + 向量化（辅助功能）
- **PostgreSQL 16 + pgvector** — 语义搜索记忆
- **aiohttp** — TestAPI HTTP 服务
- **prometheus_client** — 可观测性

## Architecture

**消息流**: Feishu WebSocket → FeishuListener → SessionRouter (routing_key) → Runner → Main Agent → SkillLoaderTool (MCP) → Sub-Agent → FeishuSender

**三种路由**: `p2p:{open_id}` (单聊), `group:{chat_id}` (群聊), `thread:{chat_id}:{thread_id}` (话题)

**两种 Skill 类型**:
- **reference** — SKILL.md 内容返回给 Main Agent 自行推理
- **task** — 创建 Sub-Agent（Claude Haiku），有 Bash/Read/Write/Edit/Grep/Glob 工具

**Main Agent 只有一个 MCP 工具**: `skill_loader`（渐进式披露）。所有能力通过 Skills 暴露。

**凭证不进入 LLM** — 写到 workspace `.config/feishu.json`，Skill 脚本直接读文件。

**三层记忆**:
- L1 Bootstrap: `soul/user/agent/memory.md` → 注入 system prompt
- L2 Context: `ctx.json` 压缩快照 + `raw.jsonl` 审计日志
- L3 Vector: pgvector 异步索引，`search_memory` Skill 混合搜索

## Module Layout

```
evopaw/
├── main.py                     # 进程入口
├── models.py                   # InboundMessage / Attachment / SenderProtocol
├── runner.py                   # 执行引擎（per-routing_key 队列、Slash 命令）
├── llm/
│   └── claude_client.py        # Claude Agent SDK 配置构建
├── feishu/
│   ├── listener.py             # WebSocket 事件 → InboundMessage
│   ├── sender.py               # 消息发送（卡片 + Loading 效果）
│   ├── downloader.py           # 附件下载到 session workspace
│   └── session_key.py          # routing_key 解析
├── agents/
│   ├── main_agent.py           # 主 Agent（build_agent_fn 工厂）
│   ├── skill_agent.py          # Sub-Agent（run_skill_agent）
│   └── hooks.py                # Verbose 模式 PreToolUse/PostToolUse hooks
├── tools/
│   ├── skill_loader.py         # SkillLoaderTool（MCP Server，渐进式披露）
│   └── add_image_tool_local.py # 图片加载（Claude 原生 image block）
├── memory/
│   ├── bootstrap.py            # soul/user/agent/memory.md 注入
│   ├── context_mgmt.py         # ctx.json 压缩 + raw.jsonl 审计
│   └── indexer.py              # pgvector 异步写入
├── session/                    # SessionManager（index.json + JSONL）
├── cron/                       # CronService（asyncio timer + 热加载）
├── cleanup/                    # CleanupService（过期文件清理 + 凭证注入）
├── observability/              # 日志 + Prometheus Metrics
├── api/                        # TestAPI（aiohttp HTTP 服务）+ CaptureSender
└── skills/                     # SKILL.md + 执行脚本（18 个 Skill）
```

## Key Design Decisions

- **Slash 命令** (`/new`, `/verbose`, `/help`, `/status`) 在 Runner 层拦截，不进入 Agent
- **Per-routing_key 队列** — 同 session 串行（`asyncio.Queue`），不同 session 并行，worker 空闲超时自动退出
- **文件并发** — `asyncio.Lock` 进程内互斥；`write-then-rename` 原子 JSON；`flush + fsync` JSONL 追写
- **CronService** 使用 asyncio 精确 timer，mtime 热加载 `tasks.json`
- **Sub-Agent 短生命周期** — 每次 Skill 调用创建独立 `query()` session，防止状态污染
- **Verbose 模式** — `PreToolUse`/`PostToolUse` hooks 推送到飞书；thread 场景和 Sub-Agent 不推送
- **Session workspace** — `workspace/sessions/{sid}/`，Sub-Agent 的 cwd
- **图片多模态** — 检测附件图片路径，构建 Claude 原生 image content block
- **ctx.json 长期上下文** — query 前加载摘要注入 prompt，query 后保存快照 + 追写 raw.jsonl
- **pgvector 异步索引** — 每轮对话后 `asyncio.create_task(async_index_turn(...))`，不阻塞主流程
- **history_reader 内联** — 直接在 SkillLoaderTool 内分页返回，不创建 Sub-Agent
- **TestAPI** — `debug.enable_test_api` 开启 HTTP 端点，`CaptureSender` 拦截回复同步返回
- **session_id 安全** — session_id 不进入 LLM context，SkillLoaderTool 仅注入路径字符串

## Data Formats

- **Session index**: `data/sessions/index.json` — routing_key → active_session_id + metadata
- **对话历史**: `data/sessions/{sid}.jsonl` — meta + user/assistant 消息对
- **Context 快照**: `data/ctx/{sid}_ctx.json` — 压缩后上下文
- **审计日志**: `data/ctx/{sid}_raw.jsonl` — 完整对话记录
- **Cron jobs**: `data/cron/tasks.json` — at/every/cron 三种调度
- **Skill 定义**: `skills/{name}/SKILL.md` — YAML frontmatter (name, description, type, version)

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

# Docker 一键启动
docker compose up -d
```

## Development Progress

**当前状态**: Phase 0-9 迁移完成（CrewAI → Claude Agent SDK），496 单元测试，0 失败。

**核心模块**:
- `evopaw/agents/main_agent.py` — Main Agent（三层记忆集成 + 图片多模态 + verbose hooks）
- `evopaw/agents/skill_agent.py` — Sub-Agent（SKILL.md 作为 system_prompt）
- `evopaw/agents/hooks.py` — Verbose 模式 hooks（飞书推送 + 异常吞没）
- `evopaw/llm/claude_client.py` — Claude SDK 配置工厂
- `evopaw/tools/skill_loader.py` — SkillLoaderTool MCP Server（渐进式披露 + Sub-Agent 触发）
- `evopaw/tools/add_image_tool_local.py` — 图片加载（Claude 原生 image block）
- `evopaw/memory/` — 三层记忆（bootstrap + context_mgmt + indexer）
- `evopaw/runner.py` — 执行引擎（队列 + slash 命令 + 卡片消息）
- `evopaw/feishu/` — 飞书接入层（listener + sender + downloader）
- `evopaw/session/` — SessionManager（并发安全）
- `evopaw/cron/` — CronService（精确 timer + 热加载）
- `evopaw/api/` — TestAPI + CaptureSender

**18 个内置 Skills**: pdf, docx, pptx, xlsx, feishu_ops, scheduler_mgr, tavily_search, arxiv_search, web_browse, history_reader, memory-save, search_memory, memory-governance, skill-creator, daily-summary, investment-report, investment-review, investment-consult
