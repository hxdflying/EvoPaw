<div align="center">
  <img src="image/logo.png" alt="EvoPaw" width="640" />

  <h1>EvoPaw · 小爪子</h1>

  <p><em>A local-first Feishu work assistant built around a file-based Skills ecosystem.</em></p>

  <p>
    <strong>English</strong> · <a href="./README.zh-CN.md">中文</a>
  </p>
</div>

---

EvoPaw connects to Feishu through the official **WebSocket** channel, routes each
conversation through a multi-provider **agent runtime**, and grows new abilities
through a folder of self-describing **Skills**.

No public webhook. No vendor lock-in. Your data stays on your box.

## ✨ Highlights

- 🪶 **Local-first.** Runs on a laptop, home server, or air-gapped VM — no inbound webhook required.
- 🔌 **Multi-provider runtime.** Built-in `claude_sdk`, `anthropic`, `dashscope`, plus any OpenAI-compatible provider.
- 🧰 **File-based Skills.** Drop in a `SKILL.md`, get a new capability — PDFs, Feishu ops, web search, scheduling, investment workflows, and more.
- 🧠 **Three-layer memory.** Bootstrap files, compressed session context, and pgvector semantic recall.
- 🎙️ **Voice in, voice out.** Feishu audio → DashScope Fun-ASR → Agent → reply.
- 📡 **Verbose mode.** Stream tool execution back to the chat for transparent debugging.
- 📊 **Observable.** Prometheus metrics and JSON-line logs out of the box.

## 🚀 Quick Start

```bash
# 1. install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
npm install -g @anthropic-ai/claude-code   # default sub-agent runtime

# 2. configure
cp config.yaml.template config.yaml        # fill in Feishu app_id / app_secret

# 3. run
docker compose up -d                       # app + pgvector
# or, for a manual loop:
python3 -m evopaw.main
```

Send a message to your Feishu bot — that's it.

## 🏗️ Architecture

```text
Feishu WebSocket
    │
    ▼
FeishuListener ──▶ Runner ──▶ Main Agent Runtime
                                 ( claude_sdk │ anthropic_messages │ openai_chat )
                                       │
                          ┌────────────┴────────────┐
                          ▼                         ▼
                   SkillDispatcher              Memory runtime
                   ├─ reference / history       ├─ bootstrap files
                   └─ task → Sub-Agent          ├─ ctx.json / raw.jsonl
                                                └─ pgvector index
```

Conversations are isolated by **routing key**:

| Feishu context | Routing key |
| --- | --- |
| Direct chat | `p2p:{open_id}` |
| Group chat | `group:{chat_id}` |
| Threaded chat | `thread:{chat_id}:{thread_id}` |

<details>
<summary>📁 Repository layout</summary>

```text
evopaw/
├── main.py                 # process entrypoint and service wiring
├── runner.py               # per-routing-key queues, slash commands, dedup
├── models.py               # inbound messages, attachments, sender protocol
├── agent_backends/         # Claude SDK, Anthropic Messages, OpenAI-compatible backends
├── provider_runtime/       # provider registry and role resolver
├── content_builders/       # provider-specific text/image message builders
├── agents/                 # main agent, sub-agent, hooks, response finalizers
├── skills_runtime/         # skill registry, dispatcher, backend adapters
├── skills/                 # SKILL.md files and skill scripts
├── feishu/                 # listener, sender, downloader, session keys
├── asr/                    # DashScope Fun-ASR client and speech service
├── memory/                 # bootstrap, context compression, pgvector indexing
├── session/                # session index and JSONL history
├── cron/                   # scheduled task service
├── cleanup/                # runtime cleanup and private credential materialization
├── observability/          # logging, metrics, metrics server
├── api/                    # local TestAPI
└── tools/                  # local helper tools, including image loading
```

</details>

## ⚙️ Configuration

Create a private runtime config (Git-ignored):

```bash
cp config.yaml.template config.yaml
```

Minimum Feishu block:

```yaml
feishu:
  app_id: "${FEISHU_APP_ID}"
  app_secret: "${FEISHU_APP_SECRET}"
```

### Environment variables

| Variable | Required when | Purpose |
| --- | --- | --- |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | Always | Feishu app credentials |
| `ANTHROPIC_API_KEY` | Using `anthropic` provider | Anthropic Messages API |
| `DASHSCOPE_API_KEY` | ASR enabled | DashScope Fun-ASR WebSocket |
| `QWEN_API_KEY` | DashScope memory roles | DashScope OpenAI-compatible chat / embeddings |
| `TAVILY_API_KEY` | `tavily_search` Skill | Web search |
| `MOONSHOT_API_KEY` | Custom Moonshot provider | Example OpenAI-compatible provider |
| `POSTGRES_PASSWORD` | Override Docker pgvector | PostgreSQL password |

> `DASHSCOPE_API_KEY` and `QWEN_API_KEY` can hold the same DashScope key — they
> exist as two names because ASR and the OpenAI-compatible memory client read
> different env vars.

<details>
<summary>🎛️ Provider roles &amp; model overrides</summary>

Default role bindings:

| Role | Default provider | Default model |
| --- | --- | --- |
| `main` | `claude_sdk` | `claude-sonnet-4-6` |
| `subagent` | `claude_sdk` | `claude-haiku-4-5` |
| `memory_summary` | `dashscope` | `qwen3-turbo` |
| `memory_embedding` | `dashscope` | `text-embedding-v3` |
| `memory_extract` | `dashscope` | `qwen3-max` |

Override providers and models inside `config.yaml`:

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

> **Current limitation.** Task-style Skills still run through the Claude SDK
> Sub-Agent path. Keep `roles.subagent` on `claude_sdk` unless you are changing
> the runtime implementation.

</details>

<details>
<summary>📦 Local workspace &amp; bootstrap files</summary>

EvoPaw reads optional bootstrap files from `data/workspace` before each agent turn:

```bash
mkdir -p data/workspace
touch data/workspace/{soul,user,agent,memory}.md
```

| File | Purpose |
| --- | --- |
| `soul.md` | Assistant identity and tone |
| `user.md` | User profile, preferences, recurring context |
| `agent.md` | Local operating rules and tool guidance |
| `memory.md` | Long-term memory index |

Missing files are silently skipped.

</details>

## ▶️ Running

### Docker Compose (recommended)

```bash
docker compose up -d --build
```

Set `memory.db_dsn` to the Compose service host:

```yaml
memory:
  db_dsn: "postgresql://evopaw:evopaw123@evopaw-pgvector:5432/evopaw_memory"
```

| Service | Purpose |
| --- | --- |
| `evopaw-main` | Python app and agent runtime |
| `pgvector` | PostgreSQL 16 with pgvector |

### Manual

```bash
docker compose -f pgvector-docker-compose.yaml up -d   # optional: semantic memory
python3 -m evopaw.main
```

For the manual path use `localhost` in the DSN:

```yaml
memory:
  db_dsn: "postgresql://evopaw:evopaw123@localhost:5432/evopaw_memory"
```

### Runtime endpoints

| Item | Location |
| --- | --- |
| Prometheus metrics | `http://127.0.0.1:9100/metrics` |
| Runtime logs | `data/logs/evopaw.log` |
| Session data | `data/sessions/` |
| Context snapshots | `data/ctx/` |
| Workspace data | `data/workspace/` |

<details>
<summary>🧪 Local TestAPI (no Feishu needed)</summary>

Enable in `config.yaml`:

```yaml
debug:
  enable_test_api: true
  test_api_host: "127.0.0.1"
  test_api_port: 9090
```

Send a local test message:

```bash
curl -X POST http://127.0.0.1:9090/api/test/message \
  -H "Content-Type: application/json" \
  -d '{"routing_key": "p2p:ou_test001", "content": "Hello"}'
```

Clear local test sessions:

```bash
curl -X DELETE http://127.0.0.1:9090/api/test/sessions
```

</details>

## 💬 Slash Commands

| Command | Description |
| --- | --- |
| `/new` | Start a fresh session |
| `/verbose on` · `/verbose off` · `/verbose` | Stream / stop / inspect tool-progress streaming |
| `/status` | Show current session details |
| `/help` | Show command help |

## 🛠️ Skills

The authoritative list lives in [`evopaw/skills/load_skills.yaml`](./evopaw/skills/load_skills.yaml).

| Skill | Type | Purpose |
| --- | --- | --- |
| `pdf` / `docx` / `pptx` / `xlsx` | task | Document parsing and extraction |
| `feishu_ops` | task | Feishu docs, sheets, bitables, messages, files |
| `scheduler_mgr` | task | Scheduled task management |
| `tavily_search` | task | Internet search via Tavily |
| `arxiv_search` | task | arXiv search and PDF retrieval |
| `web_browse` | task | Web content extraction |
| `history_reader` | reference | Inline paginated conversation history |
| `memory-save` / `search_memory` / `memory-governance` | task | Long-term memory lifecycle |
| `skill-creator` | task | Turn repeatable workflows into Skills |
| `daily-summary` | task | Daily work summaries |
| `investment-report` / `investment-review` / `investment-consult` | task | Investment workflows |
| `hk-investment-morning-report` | task | Hong Kong market morning report |

## 🧠 Memory

| Layer | Storage | Role |
| --- | --- | --- |
| L1 · Bootstrap | `data/workspace/*.md` | Identity, profile, operating rules, memory index |
| L2 · Context | `data/ctx/*.json` · `*.jsonl` | Compressed session context and audit log |
| L3 · Vector | PostgreSQL + pgvector | Semantic search over historical turns |

If the database or DashScope key is unavailable, semantic memory degrades —
the main Feishu flow keeps running.

## 🎙️ Voice Messages

```text
Feishu audio → FeishuDownloader → SpeechRecognitionService
            → FunASRRealtimeClient → Main Agent → Feishu reply
```

- Audio bytes stream straight to Fun-ASR after download.
- ASR credentials stay in the main process environment.
- Long or slow clips receive an early acknowledgement before the final reply.
- Duplicate Feishu deliveries are deduped by recent `msg_id`.
- ASR metrics are exported through Prometheus.

Helpers:

```bash
python3 scripts/audit_audio_sample_rate.py data/workspace/sessions/
python3 scripts/calibrate_thresholds.py
```

## 🧪 Testing

```bash
python3 -m pytest                                              # everything
python3 -m pytest tests/unit/ --cov=evopaw --cov-report=term   # unit + coverage
python3 -m pytest tests/integration/ -m "not llm"              # integration (no LLM)
python3 -m pytest tests/integration/test_voice_end_to_end.py   # voice e2e mock
```

## 🔒 Security & Local-Only Files

Keep these on the local box — never commit:

- `.env`, `config.yaml`
- `data/`, `tests/logs/`, `workspace-init/`
- `.coverage`, `coverage.json`, `htmlcov/`
- Python and test caches

> If a secret was ever committed, **rotate it** and clean Git history — don't
> just delete it in a follow-up commit.

---

<div align="center">
  <sub>Made with 🐾 for people who want their assistant to live on their own machine.</sub>
</div>
