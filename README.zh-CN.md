<div align="center">
  <strong>Language / 语言</strong><br>
  <a href="./README.md"><img alt="English" src="https://img.shields.io/badge/Language-English-blue?style=for-the-badge"></a>
  <a href="./README.zh-CN.md"><img alt="中文" src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E4%B8%AD%E6%96%87-green?style=for-the-badge"></a>
</div>

# EvoPaw（小爪子）

EvoPaw 是一个本地优先的飞书工作助手。它通过飞书官方 WebSocket 通道接入消息，
将每个会话路由到 Agent runtime，并通过文件化 Skills 系统扩展工具能力。

项目适合本地机器、私人服务器和内网环境部署，不需要公网入站 Webhook 地址。

## 核心能力

- 飞书 WebSocket 接入，支持单聊、群聊、话题群。
- 主 Agent 支持多 provider runtime，内置 `claude_sdk`、`anthropic`、`dashscope`，
  也支持配置 OpenAI 兼容 provider。
- 任务型 Skills 覆盖文档处理、飞书操作、网页搜索、定时任务、记忆管理和投资工作流。
- 三层记忆：本地 bootstrap 文件、会话上下文压缩、pgvector 语义搜索。
- 飞书语音消息处理：下载音频、DashScope Fun-ASR 转写、Agent 推理、回发答案。
- Verbose 模式可将工具执行进度实时推送回飞书。
- 可选本地 TestAPI，调试时不依赖真实飞书事件。
- Prometheus 指标和 JSON 行运行日志。

## 架构

```text
Feishu WebSocket
    |
    v
FeishuListener
    |
    v
Runner
    |
    v
Main Agent Runtime
  claude_sdk | anthropic_messages | openai_chat
    |
    +--> SkillDispatcher
    |       |
    |       +--> reference Skills 与 history_reader 内联执行
    |       +--> task Skills 通过 Claude SDK Sub-Agent 执行
    |
    +--> Memory runtime
            |
            +--> bootstrap files
            +--> ctx.json / raw.jsonl
            +--> pgvector semantic index
```

不同飞书会话通过 routing key 隔离状态：

| 飞书上下文 | Routing key |
| --- | --- |
| 单聊 | `p2p:{open_id}` |
| 群聊 | `group:{chat_id}` |
| 话题群 | `thread:{chat_id}:{thread_id}` |

## 目录结构

```text
evopaw/
├── main.py                 # 进程入口和服务装配
├── runner.py               # per-routing-key 队列、Slash 命令、去重
├── models.py               # 入站消息、附件、发送协议
├── agent_backends/         # Claude SDK、Anthropic Messages、OpenAI 兼容后端
├── provider_runtime/       # provider 注册表和角色解析器
├── content_builders/       # 不同 provider 的文本/图片消息构造
├── agents/                 # 主 Agent、Sub-Agent、hooks、回复 finalizer
├── skills_runtime/         # Skill 注册、调度器、backend adapters
├── skills/                 # SKILL.md 与 Skill 脚本
├── feishu/                 # listener、sender、downloader、session key
├── asr/                    # DashScope Fun-ASR 客户端和语音服务
├── memory/                 # bootstrap、上下文压缩、pgvector 索引
├── session/                # session index 与 JSONL 历史
├── cron/                   # 定时任务服务
├── cleanup/                # 运行时清理与私有凭证落盘
├── observability/          # 日志、指标、metrics server
├── api/                    # 本地 TestAPI
└── tools/                  # 本地辅助工具，包括图片加载
```

## 环境要求

本地开发需要：

- Python 3.12 或更新版本，与 `pyproject.toml` 保持一致。
- Node.js 22 或更新版本。
- 当 `roles.subagent` 使用默认 `claude_sdk` runtime 时，需要 Claude Code CLI。
- 如需使用内置 pgvector 服务，需要 Docker 与 Docker Compose。
- 一个已配置机器人和 WebSocket 事件权限的飞书应用。

安装 Python 依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

安装默认 Sub-Agent 路径需要的 Claude Code CLI：

```bash
npm install -g @anthropic-ai/claude-code
```

根据你选择的 provider runtime，完成 Claude Code CLI 登录或配置对应 API Key。

## 配置

创建私有运行配置：

```bash
cp config.yaml.template config.yaml
```

`config.yaml` 已被 Git 忽略。真实凭证和本地部署参数应放在这里，不要提交到仓库。

最小飞书配置：

```yaml
feishu:
  app_id: "${FEISHU_APP_ID}"
  app_secret: "${FEISHU_APP_SECRET}"
```

常用环境变量：

| 变量 | 何时需要 | 用途 |
| --- | --- | --- |
| `FEISHU_APP_ID` | 始终需要 | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 始终需要 | 飞书应用 App Secret |
| `ANTHROPIC_API_KEY` | 使用 `anthropic` provider | Anthropic Messages API |
| `DASHSCOPE_API_KEY` | 启用 ASR 时 | DashScope Fun-ASR WebSocket |
| `QWEN_API_KEY` | DashScope 记忆角色 | DashScope OpenAI 兼容 chat 与 embeddings |
| `TAVILY_API_KEY` | 使用 `tavily_search` Skill | 互联网搜索 |
| `MOONSHOT_API_KEY` | 自定义 Moonshot provider | OpenAI 兼容 provider 示例 |
| `POSTGRES_PASSWORD` | 覆盖 Docker pgvector 密码时 | PostgreSQL 密码 |

`DASHSCOPE_API_KEY` 和 `QWEN_API_KEY` 通常可以填写同一个 DashScope Key。
它们保留为两个变量名，是因为 ASR 与 OpenAI 兼容记忆客户端读取的环境变量不同。

### Provider Roles

默认角色绑定如下：

| Role | 默认 provider | 默认模型 |
| --- | --- | --- |
| `main` | `claude_sdk` | `claude-sonnet-4-6` |
| `subagent` | `claude_sdk` | `claude-haiku-4-5` |
| `memory_summary` | `dashscope` | `qwen3-turbo` |
| `memory_embedding` | `dashscope` | `text-embedding-v3` |
| `memory_extract` | `dashscope` | `qwen3-max` |

你可以在 `config.yaml` 中覆盖 provider 和模型：

```yaml
providers:
  moonshot:
    runtime_family: openai_chat
    api_key_env: MOONSHOT_API_KEY
    default_api_base: "https://api.moonshot.cn/v1"
    default_model: "moonshot-v1-32k"

roles:
  main: { provider: claude_sdk, model: claude-sonnet-4-6 }
  subagent: { provider: claude_sdk, model: claude-haiku-4-5 }
  memory_summary: { provider: dashscope, model: qwen3-turbo }
  memory_embedding: { provider: dashscope, model: text-embedding-v3 }
  memory_extract: { provider: dashscope, model: qwen3-max }
```

当前限制：task 类型 Skill 仍通过 Claude SDK Sub-Agent 路径执行。除非你正在改造
runtime 实现，否则请保持 `roles.subagent` 使用 `claude_sdk`。

## 本地 Workspace

EvoPaw 每轮 Agent 执行前会从 `data/workspace` 读取可选 bootstrap 文件。这些文件
属于个人运行数据，已被 Git 忽略。

```bash
mkdir -p data/workspace
touch data/workspace/soul.md
touch data/workspace/user.md
touch data/workspace/agent.md
touch data/workspace/memory.md
```

Bootstrap 文件说明：

| 文件 | 用途 |
| --- | --- |
| `soul.md` | 助手身份和语气 |
| `user.md` | 用户画像、偏好、长期上下文 |
| `agent.md` | 本地操作规则和工具使用规范 |
| `memory.md` | 长期记忆索引 |

这些文件缺失时，EvoPaw 会跳过对应 bootstrap section，并继续运行。

## 启动

### Docker Compose

Docker Compose 会启动应用和带 pgvector 的 PostgreSQL：

```bash
docker compose up -d --build
```

使用 Compose 时，`config.yaml` 中的 `memory.db_dsn` 需要使用 Compose 服务名：

```yaml
memory:
  db_dsn: "postgresql://evopaw:evopaw123@evopaw-pgvector:5432/evopaw_memory"
```

服务说明：

| 服务 | 用途 |
| --- | --- |
| `evopaw-main` | Python 应用和 Agent runtime |
| `pgvector` | PostgreSQL 16 + pgvector |

### 手动启动

如需语义记忆，可单独启动 pgvector：

```bash
docker compose -f pgvector-docker-compose.yaml up -d
```

手动运行 Python 进程时，数据库 host 保持 `localhost`：

```yaml
memory:
  db_dsn: "postgresql://evopaw:evopaw123@localhost:5432/evopaw_memory"
```

然后启动 EvoPaw：

```bash
python3 -m evopaw.main
```

运行时端点和文件：

| 项目 | 位置 |
| --- | --- |
| Prometheus 指标 | `http://127.0.0.1:9100/metrics` |
| 运行日志 | `data/logs/evopaw.log` |
| 会话数据 | `data/sessions/` |
| 上下文快照 | `data/ctx/` |
| Workspace 数据 | `data/workspace/` |

## 本地 TestAPI

在 `config.yaml` 中启用 TestAPI：

```yaml
debug:
  enable_test_api: true
  test_api_host: "127.0.0.1"
  test_api_port: 9090
```

发送本地测试消息：

```bash
curl -X POST http://127.0.0.1:9090/api/test/message \
  -H "Content-Type: application/json" \
  -d '{"routing_key": "p2p:ou_test001", "content": "你好"}'
```

清空本地测试会话：

```bash
curl -X DELETE http://127.0.0.1:9090/api/test/sessions
```

## Slash 命令

| 命令 | 说明 |
| --- | --- |
| `/new` | 创建新会话 |
| `/verbose on` | 将工具执行过程推送到飞书 |
| `/verbose off` | 停止推送工具执行过程 |
| `/verbose` | 查看 Verbose 模式状态 |
| `/status` | 查看当前会话信息 |
| `/help` | 显示命令帮助 |

## Skills

权威 Skill 清单位于 `evopaw/skills/load_skills.yaml`。

| Skill | 类型 | 用途 |
| --- | --- | --- |
| `pdf` | task | PDF 解析和文本提取 |
| `docx` | task | Word 文档处理 |
| `pptx` | task | PowerPoint 处理 |
| `xlsx` | task | Excel 处理 |
| `feishu_ops` | task | 飞书云文档、多维表格、消息和文件操作 |
| `scheduler_mgr` | task | 定时任务管理 |
| `tavily_search` | task | 通过 Tavily 进行互联网搜索 |
| `arxiv_search` | task | arXiv 搜索和 PDF 获取 |
| `web_browse` | task | 网页内容提取 |
| `history_reader` | reference | 内联分页读取对话历史 |
| `memory-save` | task | 保存长期记忆 |
| `search_memory` | task | 搜索 pgvector 记忆 |
| `memory-governance` | task | 审计和整理记忆文件 |
| `skill-creator` | task | 将重复工作流固化为 Skill |
| `daily-summary` | task | 每日工作总结 |
| `investment-report` | task | 投资研究报告 |
| `investment-review` | task | 投资组合复盘 |
| `investment-consult` | task | 投资咨询问答 |
| `hk-investment-morning-report` | task | 港股每日早报 |

## 记忆体系

| 层级 | 存储 | 职责 |
| --- | --- | --- |
| L1 Bootstrap | `data/workspace/*.md` | 身份、用户画像、操作规则、记忆索引 |
| L2 Context | `data/ctx/*.json` 与 `*.jsonl` | 压缩会话上下文和原始审计日志 |
| L3 Vector | PostgreSQL + pgvector | 历史对话语义搜索 |

如果数据库或 DashScope Key 不可用，EvoPaw 仍可运行；语义记忆能力会降级，但不会阻塞
飞书主链路。

## 语音消息

飞书发送 audio 消息时，EvoPaw 可以执行：

```text
Feishu audio
    -> FeishuDownloader
    -> SpeechRecognitionService
    -> FunASRRealtimeClient
    -> Main Agent
    -> Feishu reply
```

关键行为：

- 音频下载后直接以字节流发送到 Fun-ASR WebSocket。
- ASR 凭证只保留在主进程环境变量中。
- 长语音或转写较慢时，先发送回执，再发送最终回复。
- 飞书重复投递会通过最近 `msg_id` 去重。
- ASR 指标通过 Prometheus 暴露。

实用工具：

```bash
python3 scripts/audit_audio_sample_rate.py data/workspace/sessions/
python3 scripts/calibrate_thresholds.py
```

部署检查见 `docs/runbooks/voice-pre-production.md`。

## 测试

运行完整测试：

```bash
python3 -m pytest
```

只运行单元测试：

```bash
python3 -m pytest tests/unit/ -v --cov=evopaw --cov-report=term-missing
```

运行不依赖外部 LLM 的集成测试：

```bash
python3 -m pytest tests/integration/ -m "not llm" -v
```

运行语音端到端 mock 测试：

```bash
python3 -m pytest tests/integration/test_voice_end_to_end.py -v
```

## 安全与本地专用文件

以下内容只应保留在本地：

- `.env`
- `config.yaml`
- `data/`
- `tests/logs/`
- `workspace-init/`
- `.coverage`、`coverage.json`、`htmlcov/`
- Python 和测试缓存

不要提交真实的飞书、Anthropic、DashScope、Tavily、数据库或其它 provider 凭证。
如果密钥曾经被提交，应该轮换密钥并清理 Git 历史，而不是只在后续 commit 中删除。

## 更多文档

- `config.yaml.template`：完整运行配置。
- `docs/message-flow.md`：消息路由细节。
- `docs/design-data.md`：数据和记忆设计。
- `docs/runbooks/voice-pre-production.md`：ASR 上线检查。
