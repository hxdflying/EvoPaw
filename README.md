## XiaoPaw（小爪子）

基于飞书的本地工作助手，通过 Skills 生态 + AIO-Sandbox（Docker）实现安全可扩展的工具调用。支持飞书 WebSocket 长连接，无需公网 IP，适合本地/内网部署。

> **第22课·记忆篇**：本版本新增三层记忆架构——Bootstrap 上下文注入、ctx.json 跨 session 压缩、pgvector 搜索记忆。

### 核心功能

- **飞书全场景接入**：单聊（p2p）、群聊（group）、话题群（thread）
- **Skills 生态**：9 个内置 Skill，覆盖文件处理、网页搜索/浏览、飞书操作、定时任务、历史查询
- **AIO-Sandbox 隔离**：所有代码执行在 Docker 沙盒中运行，凭证不经过 LLM
- **三层记忆架构**（第22课新增）：Bootstrap 文件注入 + ctx.json 上下文压缩 + pgvector 语义搜索
- **Verbose 详细模式**：实时推送 Agent 推理过程，可随时开关
- **定时任务**：支持一次性（at）、固定间隔（every）、Cron 表达式三种模式
- **TestAPI**：HTTP 接口本地调试，无需真实飞书环境
- **卡片消息 + Loading 效果**：发送交互式卡片，Loading 状态实时更新
- **Markdown 富文本渲染**：支持 lark_md 格式，Agent 回复支持加粗、斜体、链接等

### 内置 Skills

| Skill | 类型 | 能力 |
|-------|------|------|
| `pdf` | 任务型 | PDF 解析、文本提取、格式转换 |
| `docx` | 任务型 | Word 文档读取与处理 |
| `pptx` | 任务型 | PPT 文档读取与处理 |
| `xlsx` | 任务型 | Excel 表格读取与处理 |
| `feishu_ops` | 任务型 | 通过 `scripts/*.py` 脚本读取飞书云文档、向指定群/用户发消息 |
| `scheduler_mgr` | 任务型 | 通过 `scheduler_mgr/scripts/*.py` 创建/查看/更新/删除定时任务 |
| `baidu_search` | 任务型 | 百度千帆网络搜索，支持时间过滤与站点限定 |
| `web_browse` | 任务型 | 网页内容提取（Markdown 转换）与浏览器自动化（截图/表单/JS） |
| `history_reader` | 参考型 | 分页读取历史对话记录 |

### 目录结构

```
xiaopaw/
├── main.py                  # 进程入口
├── models.py                # InboundMessage / Attachment / SenderProtocol
├── runner.py                # 执行引擎（per-routing_key 队列、Slash 命令、Agent 调用）
├── llm/aliyun_llm.py        # AliyunLLM 适配器（通义千问，支持多模态+Function Calling）
├── feishu/
│   ├── listener.py          # WebSocket 事件 → InboundMessage
│   ├── sender.py            # 消息发送（p2p/group/thread），含重试
│   ├── downloader.py        # 附件下载到 session workspace
│   └── session_key.py       # routing_key 解析
├── agents/
│   ├── main_crew.py         # 主 Crew（build_agent_fn 工厂）
│   └── skill_crew.py        # Sub-Crew 工厂（build_skill_crew）
├── tools/
│   ├── skill_loader.py      # SkillLoaderTool（渐进式披露 + Sub-Crew 触发）
│   ├── add_image_tool_local.py
│   ├── baidu_search_tool.py
│   └── intermediate_tool.py
├── session/                 # SessionManager（index.json + JSONL）
├── cron/                    # CronService（asyncio 精确 timer）
├── cleanup/                 # CleanupService（按策略清理过期文件）
├── observability/           # 日志 + Prometheus Metrics
├── api/                     # TestAPI（aiohttp HTTP 服务）
└── skills/                  # SKILL.md + 执行脚本，每个 Skill 独立目录
    ├── pdf/ docx/ pptx/ xlsx/
    ├── feishu_ops/
    ├── scheduler_mgr/
    ├── baidu_search/
    ├── web_browse/
    └── history_reader/
```

### 环境准备

**依赖**：Python 3.11+、Docker（运行 AIO-Sandbox + pgvector）

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

**环境变量**：

```bash
export QWEN_API_KEY=<阿里云千问 API Key>       # 必填：LLM + Embedding 调用
export FEISHU_APP_ID=<飞书应用 App ID>         # 必填：飞书开放平台
export FEISHU_APP_SECRET=<飞书应用 App Secret>  # 必填：飞书开放平台
export BAIDU_API_KEY=<百度千帆 API Key>        # 可选：baidu_search Skill
export MEMORY_DB_DSN=postgresql://xiaopaw:xiaopaw123@localhost:5432/xiaopaw_memory  # 可选：pgvector 搜索记忆

# 调试时可选开启完整请求 payload 日志
export QWEN_DEBUG_PAYLOAD=1
```

### 配置 `config.yaml`

复制模板并填写飞书凭证：

```bash
cp config.yaml.template config.yaml
```

核心配置项：

```yaml
feishu:
  app_id: "${FEISHU_APP_ID}"
  app_secret: "${FEISHU_APP_SECRET}"

memory:
  workspace_dir: "./data/workspace"   # Bootstrap 读取 soul/user/agent/memory.md
  ctx_dir: "./data/ctx"              # ctx.json 跨 session 压缩快照
  db_dsn: "postgresql://xiaopaw:xiaopaw123@localhost:5432/xiaopaw_memory"

sandbox:
  url: "http://localhost:8022/mcp"

debug:
  enable_test_api: true             # 本地调试时开启
  test_api_port: 9090
```

完整配置项见 `config.yaml.template`。

### 初始化 Workspace 文件（第22课记忆篇）

`workspace-init/` 目录提供了初始模板，复制后按实际情况修改：

```bash
mkdir -p data/workspace
cp workspace-init/soul.md   data/workspace/soul.md    # XiaoPaw 性格/身份
cp workspace-init/user.md   data/workspace/user.md    # 用户档案（按需填写）
cp workspace-init/agent.md  data/workspace/agent.md   # Agent 能力边界
cp workspace-init/memory.md data/workspace/memory.md  # 长期记忆索引（初始为空）
```

Bootstrap 阶段（每轮对话开始前）XiaoPaw 会读取这四个文件构建 Agent 背景知识：
- `soul.md`：不变的性格与原则
- `user.md`：用户档案（可手动更新，也可由 XiaoPaw 自动追加）
- `agent.md`：工具清单与能力边界
- `memory.md`：跨 session 重要信息索引（200行上限，超出自动截断）

### 启动 Docker 服务

**AIO-Sandbox**（代码执行沙盒）：

```bash
docker compose -f sandbox-docker-compose.yaml up -d
```

Sandbox MCP 端点：`http://localhost:8022/mcp`

**pgvector**（搜索记忆数据库，第22课新增）：

```bash
docker compose -f pgvector-docker-compose.yaml up -d
```

pgvector 连接串：`postgresql://xiaopaw:xiaopaw123@localhost:5432/xiaopaw_memory`

> `schema.sql` 在容器首次启动时自动执行，无需手动建表。

### 启动 XiaoPaw

```bash
python3 -m xiaopaw.main
```

启动后：
- 飞书 WebSocket 开始监听消息
- Prometheus 指标：`http://127.0.0.1:9100/metrics`
- JSON 行日志：`data/logs/xiaopaw.log`
- TestAPI（如已启用）：`http://127.0.0.1:9090/api/test/message`

### 本地调试（TestAPI）

在 `config.yaml` 中设置 `debug.enable_test_api: true`，无需真实飞书环境：

```bash
# 发送消息，同步获取 Bot 回复
curl -X POST http://127.0.0.1:9090/api/test/message \
  -H "Content-Type: application/json" \
  -d '{"routing_key": "p2p:ou_test001", "content": "你好"}'

# 响应示例（Bot 回复已通过卡片消息 + update_card 完整更新）
{
  "msg_id": "test_xxx",
  "reply": "**你好！** 我是 XiaoPaw 工作助手。有什么可以帮助你的吗？",
  "session_id": "s-uuid-001",
  "duration_ms": 2345,
  "skills_called": []
}

# 清空会话数据
curl -X DELETE http://127.0.0.1:9090/api/test/sessions
```

**卡片消息流程**（从 2026-03-09 开始）：
1. 用户发送消息 → Runner 接收
2. Runner 调用 `send_thinking()` → 发送"⏳ 思考中..."加载卡片，获取 card_msg_id
3. Agent 执行（5-30s）
4. Runner 调用 `update_card(card_msg_id, 最终结果)` → 更新卡片内容为 Agent 回复
5. 若更新失败，降级调用 `send()` 重新发送整条消息

### Slash 命令

| 命令 | 功能 |
|------|------|
| `/new` | 创建新会话，之前历史不带入 |
| `/verbose on/off` | 开启/关闭推理过程实时推送 |
| `/verbose` | 查询详细模式当前状态 |
| `/status` | 查看当前会话信息 |
| `/help` | 显示命令帮助 |

### 运行测试

```bash
# 单元测试（含覆盖率）
python3 -m pytest tests/unit/ -v --cov=xiaopaw --cov-report=term-missing

# 集成测试（无 LLM，无 Sandbox）
python3 -m pytest tests/integration/ -m "not llm and not sandbox" -v

# 集成测试（含 LLM，需设置 QWEN_API_KEY）
python3 -m pytest tests/integration/test_e2e_conversation.py -m "llm and not sandbox" -v -s

# 完整集成测试（需启动 Sandbox）
python3 -m pytest tests/integration/ -v -s --timeout=180
```

**测试统计**（2026-03-21）：642 单元测试，86%+ 覆盖率 ✅

更多设计细节见 `DESIGN.md` 和 `CLAUDE.md`。
