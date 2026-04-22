## EvoPaw（小爪子）

基于 Claude Agent SDK 的飞书工作助手。通过 Skills 生态实现可扩展的工具调用，所有执行在容器内隔离。支持飞书 WebSocket 长连接，无需公网 IP，适合本地/内网部署。

### 核心功能

- **Claude Agent SDK 驱动**：主 Agent 使用 Claude Sonnet 4.6，Sub-Agent 使用 Claude Haiku 4.5
- **飞书全场景接入**：单聊（p2p）、群聊（group）、话题群（thread）
- **19 个内置 Skills**：文件处理、网页搜索/浏览、飞书操作、定时任务、记忆管理、投资分析等
- **三层记忆架构**：Bootstrap 文件注入 + ctx.json 上下文压缩 + pgvector 语义搜索
- **图片多模态**：Claude 原生 image block，直接理解用户发送的图片
- **Verbose 详细模式**：实时推送工具调用过程到飞书
- **定时任务**：支持一次性（at）、固定间隔（every）、Cron 表达式三种模式
- **TestAPI**：HTTP 接口本地调试，无需真实飞书环境
- **卡片消息 + Loading 效果**：发送交互式卡片，Agent 思考时展示加载状态

### 架构概览

```
飞书 WebSocket
    │
    ▼
FeishuListener → Runner → Main Agent (Claude Sonnet 4.6)
                              │
                         skill_loader (MCP Server)
                              │
                ┌─────────────┼───────────────┐
                ▼             ▼               ▼
          reference型     task型 Skill     history_reader
        (返回指令给      (Sub-Agent        (内联分页)
         Main Agent)    Claude Haiku 4.5)
                              │
                      Bash/Read/Write/Edit
                      (容器内执行)
```

**消息流**：飞书 → FeishuListener → SessionRouter (routing_key) → Runner → Main Agent → SkillLoaderTool → Sub-Agent → FeishuSender

**三种路由类型**：`p2p:{open_id}`（单聊）、`group:{chat_id}`（群聊）、`thread:{chat_id}:{thread_id}`（话题）

### 内置 Skills

| Skill | 类型 | 能力 |
|-------|------|------|
| `pdf` | 任务型 | PDF 解析、文本提取 |
| `docx` | 任务型 | Word 文档处理 |
| `pptx` | 任务型 | PowerPoint 处理 |
| `xlsx` | 任务型 | Excel 处理 |
| `feishu_ops` | 任务型 | 读写飞书云文档、向群/用户发消息 |
| `scheduler_mgr` | 任务型 | 定时任务创建/查看/删除 |
| `tavily_search` | 任务型 | 互联网搜索（Tavily API） |
| `arxiv_search` | 任务型 | 论文搜索与 PDF 读取 |
| `web_browse` | 任务型 | 网页内容提取（Markdown 转换） |
| `memory-save` | 任务型 | 持久化重要信息到记忆文件 |
| `search_memory` | 任务型 | pgvector 语义搜索历史对话 |
| `memory-governance` | 任务型 | 记忆清理与整理 |
| `skill-creator` | 任务型 | 将重复操作固化为新 Skill |
| `daily-summary` | 任务型 | 每日工作总结 |
| `investment-report` | 任务型 | 生成投资研究报告 |
| `investment-review` | 任务型 | 投资组合复盘与评估 |
| `investment-consult` | 任务型 | 投资咨询对话 |
| `hk-investment-morning-report` | 任务型 | 港股每日早报 |
| `history_reader` | 参考型 | 分页读取历史对话（内联处理，无需 Sub-Agent） |

> Skill 清单以 `evopaw/skills/load_skills.yaml` 为准；上表失同步时以 yaml 为权威来源。

### 目录结构

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
│   ├── skill_agent.py          # Sub-Agent 工厂（run_skill_agent）
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
├── cleanup/                    # CleanupService（过期文件清理）
├── observability/              # 日志 + Prometheus Metrics
├── api/                        # TestAPI（aiohttp HTTP 服务）
└── skills/                     # SKILL.md + 执行脚本（见 skills/load_skills.yaml）
```

### 环境准备

**系统依赖**：Python 3.11+、Node.js 22+、Docker

```bash
pip install -r requirements.txt
```

**环境变量**：

```bash
# 必填
export ANTHROPIC_API_KEY=<Anthropic API Key>       # Claude Agent SDK
export FEISHU_APP_ID=<飞书应用 App ID>              # 飞书开放平台
export FEISHU_APP_SECRET=<飞书应用 App Secret>      # 飞书开放平台

# 可选
export QWEN_API_KEY=<通义千问 API Key>             # 记忆摘要压缩 + 向量化
export TAVILY_API_KEY=<Tavily API Key>             # 互联网搜索
export MEMORY_DB_DSN=postgresql://evopaw:evopaw123@localhost:5432/evopaw_memory
```

### 配置

复制模板并填写凭证：

```bash
cp config.yaml.template config.yaml
```

核心配置项：

```yaml
feishu:
  app_id: "${FEISHU_APP_ID}"
  app_secret: "${FEISHU_APP_SECRET}"

agent:
  planner_model: "claude-sonnet-4-6"     # 主 Agent 模型
  sub_agent_model: "claude-haiku-4-5"    # ⚠️ 当前未接入，改值不生效（见 docs/redundancy-audit-2026-04-21.md #7）
  max_turns: 50
  sub_agent_max_turns: 20                # ⚠️ 当前未接入，改值不生效（见 docs/redundancy-audit-2026-04-21.md #7）
  timeout_s: 300

memory:
  workspace_dir: "./data/workspace"
  ctx_dir: "./data/ctx"
  db_dsn: "${MEMORY_DB_DSN}"             # 可选，留空则跳过向量索引

debug:
  enable_test_api: true                  # 本地调试时开启
  test_api_port: 9090
```

完整配置项见 `config.yaml.template`。

### 初始化 Workspace

`workspace-init/` 目录提供了初始模板：

```bash
mkdir -p data/workspace
cp workspace-init/soul.md   data/workspace/soul.md    # Agent 性格与身份
cp workspace-init/user.md   data/workspace/user.md    # 用户档案
cp workspace-init/agent.md  data/workspace/agent.md   # 工具规范与能力边界
cp workspace-init/memory.md data/workspace/memory.md  # 长期记忆索引（初始为空）
```

Bootstrap 阶段（每轮对话开始前）会读取这四个文件注入 system prompt：
- `soul.md`：性格、原则（完整注入）
- `user.md`：用户画像、偏好（完整注入）
- `agent.md`：工具清单与 SOP（完整注入）
- `memory.md`：记忆索引导航（前 200 行，指向详细记忆文件）

### 启动

#### 方式一：Docker Compose（推荐）

```bash
docker compose up -d
```

启动两个服务：
- `evopaw-main`：主应用（Python + Claude Code CLI）
- `pgvector`：PostgreSQL 16 + pgvector（`schema.sql` 首次启动自动建表）

#### 方式二：手动启动

先启动 pgvector（可选，不启动则跳过语义记忆）：

```bash
docker compose -f pgvector-docker-compose.yaml up -d
```

再启动主程序：

```bash
python3 -m evopaw.main
```

启动后：
- 飞书 WebSocket 开始监听消息
- Prometheus 指标：`http://127.0.0.1:9100/metrics`
- JSON 行日志：`data/logs/evopaw.log`
- TestAPI（如已启用）：`http://127.0.0.1:9090/api/test/message`

### 本地调试（TestAPI）

在 `config.yaml` 中设置 `debug.enable_test_api: true`，无需真实飞书环境：

```bash
# 发送消息
curl -X POST http://127.0.0.1:9090/api/test/message \
  -H "Content-Type: application/json" \
  -d '{"routing_key": "p2p:ou_test001", "content": "你好"}'

# 响应示例
{
  "msg_id": "test_xxx",
  "reply": "你好！我是 EvoPaw 工作助手。有什么可以帮助你的吗？",
  "session_id": "s-uuid-001",
  "duration_ms": 2345,
  "skills_called": []
}

# 清空会话
curl -X DELETE http://127.0.0.1:9090/api/test/sessions
```

### Slash 命令

| 命令 | 功能 |
|------|------|
| `/new` | 创建新会话，之前历史不带入 |
| `/verbose on/off` | 开启/关闭工具调用过程实时推送 |
| `/verbose` | 查询详细模式当前状态 |
| `/status` | 查看当前会话信息 |
| `/help` | 显示命令帮助 |

### 三层记忆架构

| 层级 | 存储 | 职责 | 查询方式 |
|------|------|------|---------|
| L1 Bootstrap | `/workspace/*.md` | Agent 身份、用户画像、工具规范、记忆索引 | 每轮自动注入 system prompt |
| L2 Context | `ctx.json` + `raw.jsonl` | 压缩后的对话快照 + 完整审计日志 | 自动恢复到当前 session |
| L3 Vector | pgvector DB | 历史对话的语义向量 + 全文索引 | `search_memory` Skill 混合搜索 |

### 运行测试

```bash
# 全量单元测试
python3 -m pytest tests/unit/ -v --cov=evopaw --cov-report=term-missing

# 单个测试文件
python3 -m pytest tests/unit/test_main_agent.py -v

# 集成测试（无 LLM）
python3 -m pytest tests/integration/ -m "not llm" -v
```

**测试统计**（2026-04-17）：496 单元测试，0 失败

### 技术栈

| 组件 | 技术 |
|------|------|
| Agent 框架 | Claude Agent SDK (`claude-agent-sdk`) |
| 主 Agent 模型 | Claude Sonnet 4.6 |
| Sub-Agent 模型 | Claude Haiku 4.5 |
| 飞书 SDK | `lark-oapi`（WebSocket + REST） |
| 记忆摘要/向量化 | 通义千问（OpenAI 兼容格式，DashScope） |
| 向量数据库 | PostgreSQL 16 + pgvector |
| HTTP 框架 | aiohttp |
| 监控 | Prometheus + `/metrics` 端点 |
| 容器化 | Docker Compose（evopaw-main + pgvector） |

更多设计细节见 `docs/` 目录。
