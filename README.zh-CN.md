<div align="center">
  <img src="image/logo.png" alt="EvoPaw" width="640" />

  <h1>EvoPaw · 小爪子</h1>

  <p><em>本地优先的飞书工作助手，围绕文件化 Skills 生态构建。</em></p>

  <p>
    <a href="./README.md">English</a> · <strong>中文</strong>
  </p>
</div>

---

EvoPaw 通过飞书官方 **WebSocket** 通道接入消息，将每个会话路由到多 provider
的 **Agent 运行时**，并通过一组自描述的 **Skills 文件** 持续扩展能力。

不需要公网入站 Webhook，不绑死某一家厂商，数据永远留在你自己的机器上。

## ✨ 核心亮点

- 🪶 **本地优先**：笔记本、家庭服务器、内网 VM 都能跑，无需公网 Webhook。
- 🔌 **多 Provider Runtime**：内置 `claude_sdk` / `anthropic` / `dashscope`，并支持任意 OpenAI 兼容 Provider。
- 🧰 **文件化 Skills**：丢一个 `SKILL.md` 就多一个能力 —— PDF、飞书操作、网页搜索、定时任务、投资工作流……
- 🧠 **三层记忆**：Bootstrap 文件、压缩会话上下文、pgvector 语义召回。
- 🎙️ **语音对答**：飞书语音 → DashScope Fun-ASR → Agent → 回复。
- 📡 **Verbose 模式**：把工具执行过程实时推送回飞书，调试一目了然。
- 📊 **可观测**：开箱即用的 Prometheus 指标和 JSON 行日志。

## 🚀 快速开始

```bash
# 1. 安装
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
npm install -g @anthropic-ai/claude-code   # 默认 Sub-Agent runtime

# 2. 配置
cp config.yaml.template config.yaml        # 填入飞书 app_id / app_secret

# 3. 启动
docker compose up -d                       # 应用 + pgvector
# 或者手动：
python3 -m evopaw.main
```

给你的飞书机器人发条消息 —— 就这样。

## 🏗️ 架构

```text
Feishu WebSocket
    │
    ▼
FeishuListener ──▶ Runner ──▶ 主 Agent 运行时
                                 ( claude_sdk │ anthropic_messages │ openai_chat )
                                       │
                          ┌────────────┴────────────┐
                          ▼                         ▼
                   SkillDispatcher              Memory 运行时
                   ├─ reference / history       ├─ bootstrap 文件
                   └─ task → Sub-Agent          ├─ ctx.json / raw.jsonl
                                                └─ pgvector 索引
```

不同飞书会话通过 **routing key** 隔离状态：

| 飞书上下文 | Routing key |
| --- | --- |
| 单聊 | `p2p:{open_id}` |
| 群聊 | `group:{chat_id}` |
| 话题群 | `thread:{chat_id}:{thread_id}` |

<details>
<summary>📁 目录结构</summary>

```text
evopaw/
├── main.py                 # 进程入口和服务装配
├── runner.py               # per-routing-key 队列、Slash 命令、去重
├── models.py               # 入站消息、附件、发送协议
├── agent_backends/         # Claude SDK / Anthropic Messages / OpenAI 兼容后端
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

</details>

## ⚙️ 配置

创建私有运行配置（已被 Git 忽略）：

```bash
cp config.yaml.template config.yaml
```

最小飞书配置：

```yaml
feishu:
  app_id: "${FEISHU_APP_ID}"
  app_secret: "${FEISHU_APP_SECRET}"
```

### 环境变量

| 变量 | 何时需要 | 用途 |
| --- | --- | --- |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 始终需要 | 飞书应用凭证 |
| `ANTHROPIC_API_KEY` | 使用 `anthropic` provider | Anthropic Messages API |
| `DASHSCOPE_API_KEY` | 启用 ASR 时 | DashScope Fun-ASR WebSocket |
| `QWEN_API_KEY` | DashScope 记忆角色 | DashScope OpenAI 兼容 chat / embeddings |
| `TAVILY_API_KEY` | 使用 `tavily_search` Skill | 互联网搜索 |
| `MOONSHOT_API_KEY` | 自定义 Moonshot provider | OpenAI 兼容 provider 示例 |
| `POSTGRES_PASSWORD` | 覆盖 Docker pgvector 密码时 | PostgreSQL 密码 |

> `DASHSCOPE_API_KEY` 和 `QWEN_API_KEY` 通常可填同一个 DashScope Key，
> 保留两个变量名是因为 ASR 与 OpenAI 兼容记忆客户端读取的环境变量不同。

<details>
<summary>🎛️ Provider 角色 &amp; 模型覆盖</summary>

默认角色绑定：

| Role | 默认 provider | 默认模型 |
| --- | --- | --- |
| `main` | `claude_sdk` | `claude-sonnet-4-6` |
| `subagent` | `claude_sdk` | `claude-haiku-4-5` |
| `memory_summary` | `dashscope` | `qwen3-turbo` |
| `memory_embedding` | `dashscope` | `text-embedding-v3` |
| `memory_extract` | `dashscope` | `qwen3-max` |

在 `config.yaml` 中覆盖 Provider 和模型：

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

> **当前限制**：task 类型 Skill 仍通过 Claude SDK Sub-Agent 路径执行。
> 除非你正在改造 runtime 实现，否则请保持 `roles.subagent` 使用 `claude_sdk`。

</details>

<details>
<summary>📦 本地 Workspace &amp; Bootstrap 文件</summary>

EvoPaw 每轮 Agent 执行前会从 `data/workspace` 读取可选 bootstrap 文件：

```bash
mkdir -p data/workspace
touch data/workspace/{soul,user,agent,memory}.md
```

| 文件 | 用途 |
| --- | --- |
| `soul.md` | 助手身份和语气 |
| `user.md` | 用户画像、偏好、长期上下文 |
| `agent.md` | 本地操作规则和工具使用规范 |
| `memory.md` | 长期记忆索引 |

文件缺失时会被静默跳过。

</details>

## ▶️ 启动方式

### Docker Compose（推荐）

```bash
docker compose up -d --build
```

`config.yaml` 中的 `memory.db_dsn` 使用 Compose 服务名：

```yaml
memory:
  db_dsn: "postgresql://evopaw:evopaw123@evopaw-pgvector:5432/evopaw_memory"
```

| 服务 | 用途 |
| --- | --- |
| `evopaw-main` | Python 应用和 Agent 运行时 |
| `pgvector` | PostgreSQL 16 + pgvector |

### 手动启动

```bash
docker compose -f pgvector-docker-compose.yaml up -d   # 可选：开启语义记忆
python3 -m evopaw.main
```

手动启动时数据库 host 保持 `localhost`：

```yaml
memory:
  db_dsn: "postgresql://evopaw:evopaw123@localhost:5432/evopaw_memory"
```

### 运行时端点

| 项目 | 位置 |
| --- | --- |
| Prometheus 指标 | `http://127.0.0.1:9100/metrics` |
| 运行日志 | `data/logs/evopaw.log` |
| 会话数据 | `data/sessions/` |
| 上下文快照 | `data/ctx/` |
| Workspace 数据 | `data/workspace/` |

<details>
<summary>🧪 本地 TestAPI（不依赖飞书）</summary>

在 `config.yaml` 中启用：

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

</details>

## 💬 Slash 命令

| 命令 | 说明 |
| --- | --- |
| `/new` | 创建新会话 |
| `/verbose on` · `/verbose off` · `/verbose` | 开启 / 关闭 / 查看工具进度推送 |
| `/status` | 查看当前会话信息 |
| `/help` | 显示命令帮助 |

## 🛠️ Skills

权威清单见 [`evopaw/skills/load_skills.yaml`](./evopaw/skills/load_skills.yaml)。

| Skill | 类型 | 用途 |
| --- | --- | --- |
| `pdf` / `docx` / `pptx` / `xlsx` | task | 文档解析与文本提取 |
| `feishu_ops` | task | 飞书云文档、表格、多维表、消息、文件操作 |
| `scheduler_mgr` | task | 定时任务管理 |
| `tavily_search` | task | 通过 Tavily 进行互联网搜索 |
| `arxiv_search` | task | arXiv 搜索和 PDF 获取 |
| `web_browse` | task | 网页内容提取 |
| `history_reader` | reference | 内联分页读取对话历史 |
| `memory-save` / `search_memory` / `memory-governance` | task | 长期记忆生命周期 |
| `skill-creator` | task | 将重复工作流固化为 Skill |
| `daily-summary` | task | 每日工作总结 |
| `investment-report` / `investment-review` / `investment-consult` | task | 投资工作流 |
| `hk-investment-morning-report` | task | 港股每日早报 |

## 🧠 记忆体系

| 层级 | 存储 | 职责 |
| --- | --- | --- |
| L1 · Bootstrap | `data/workspace/*.md` | 身份、用户画像、操作规则、记忆索引 |
| L2 · Context | `data/ctx/*.json` · `*.jsonl` | 压缩会话上下文与原始审计日志 |
| L3 · Vector | PostgreSQL + pgvector | 历史对话语义搜索 |

数据库或 DashScope Key 不可用时，语义记忆能力降级 —— 飞书主链路照常运行。

## 🎙️ 语音消息

```text
飞书 audio → FeishuDownloader → SpeechRecognitionService
          → FunASRRealtimeClient → 主 Agent → 飞书回复
```

- 音频下载后直接以字节流送进 Fun-ASR WebSocket。
- ASR 凭证只保留在主进程环境变量中。
- 长语音或转写较慢时先发送回执，再发送最终回复。
- 飞书重复投递通过最近 `msg_id` 去重。
- ASR 指标通过 Prometheus 暴露。

辅助脚本：

```bash
python3 scripts/audit_audio_sample_rate.py data/workspace/sessions/
python3 scripts/calibrate_thresholds.py
```

## 🧪 测试

```bash
python3 -m pytest                                              # 全量
python3 -m pytest tests/unit/ --cov=evopaw --cov-report=term   # 单元 + 覆盖率
python3 -m pytest tests/integration/ -m "not llm"              # 集成（不依赖外部 LLM）
python3 -m pytest tests/integration/test_voice_end_to_end.py   # 语音端到端 mock
```

## 🔒 安全与本地专用文件

以下内容只应保留在本地，绝不提交：

- `.env`、`config.yaml`
- `data/`、`tests/logs/`、`workspace-init/`
- `.coverage`、`coverage.json`、`htmlcov/`
- Python 和测试缓存

> 如果密钥曾经被提交，应该**轮换密钥**并清理 Git 历史，而不是只在后续 commit 中删除。

---

<div align="center">
  <sub>用 🐾 做给希望"助手运行在自己机器上"的人。</sub>
</div>
