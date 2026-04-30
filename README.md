<div align="center">
  <strong>Language / 语言</strong><br>
  <a href="./README.md"><img alt="English" src="https://img.shields.io/badge/Language-English-blue?style=for-the-badge"></a>
  <a href="./README.zh-CN.md"><img alt="中文" src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E4%B8%AD%E6%96%87-green?style=for-the-badge"></a>
</div>

# EvoPaw

EvoPaw is a local-first Feishu work assistant. It connects to Feishu through the
official WebSocket channel, routes each conversation through an agent runtime,
and expands the assistant with a file-based Skills system.

The project is designed for local machines, private servers, and internal
networks. It does not require a public inbound webhook endpoint.

## Highlights

- Feishu WebSocket integration for direct chats, group chats, and threaded chats.
- Multi-provider main-agent runtime with built-in `claude_sdk`, `anthropic`, and
  `dashscope` providers, plus configurable OpenAI-compatible providers.
- Task Skills for document processing, Feishu operations, web search, scheduling,
  memory management, and investment workflows.
- Three-layer memory: local bootstrap files, compressed session context, and
  pgvector semantic search.
- Feishu audio handling through DashScope Fun-ASR: download, transcribe, reason,
  and reply.
- Verbose mode that streams tool progress back to Feishu.
- Optional local TestAPI for debugging without a real Feishu event.
- Prometheus metrics and JSON-line runtime logs.

## Architecture

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
    |       +--> reference Skills and history_reader inline
    |       +--> task Skills through Claude SDK Sub-Agent
    |
    +--> Memory runtime
            |
            +--> bootstrap files
            +--> ctx.json / raw.jsonl
            +--> pgvector semantic index
```

Routing keys separate conversation state:

| Feishu context | Routing key |
| --- | --- |
| Direct chat | `p2p:{open_id}` |
| Group chat | `group:{chat_id}` |
| Threaded chat | `thread:{chat_id}:{thread_id}` |

## Repository Map

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

## Requirements

For local development:

- Python 3.12 or newer, matching `pyproject.toml`.
- Node.js 22 or newer.
- Claude Code CLI if `roles.subagent` uses the default `claude_sdk` runtime.
- Docker and Docker Compose if you want the bundled pgvector service.
- A Feishu app with bot and WebSocket event permissions.

Install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install the Claude Code CLI for the default Sub-Agent path:

```bash
npm install -g @anthropic-ai/claude-code
```

Authenticate the CLI or configure API keys according to the provider runtime you
choose.

## Configuration

Create a private runtime config:

```bash
cp config.yaml.template config.yaml
```

`config.yaml` is intentionally ignored by Git. Keep real credentials and local
deployment details there, not in committed files.

Minimum Feishu configuration:

```yaml
feishu:
  app_id: "${FEISHU_APP_ID}"
  app_secret: "${FEISHU_APP_SECRET}"
```

Common environment variables:

| Variable | Required when | Purpose |
| --- | --- | --- |
| `FEISHU_APP_ID` | Always | Feishu app ID |
| `FEISHU_APP_SECRET` | Always | Feishu app secret |
| `ANTHROPIC_API_KEY` | `anthropic` provider | Anthropic Messages API |
| `DASHSCOPE_API_KEY` | ASR enabled | DashScope Fun-ASR WebSocket |
| `QWEN_API_KEY` | DashScope memory roles | DashScope OpenAI-compatible chat and embeddings |
| `TAVILY_API_KEY` | `tavily_search` Skill | Web search |
| `MOONSHOT_API_KEY` | Custom Moonshot provider | Example OpenAI-compatible provider |
| `POSTGRES_PASSWORD` | Docker pgvector override | PostgreSQL password |

`DASHSCOPE_API_KEY` and `QWEN_API_KEY` can usually hold the same DashScope key.
They are separate names because ASR and OpenAI-compatible memory clients read
different environment variables.

### Provider Roles

The default role bindings are:

| Role | Default provider | Default model |
| --- | --- | --- |
| `main` | `claude_sdk` | `claude-sonnet-4-6` |
| `subagent` | `claude_sdk` | `claude-haiku-4-5` |
| `memory_summary` | `dashscope` | `qwen3-turbo` |
| `memory_embedding` | `dashscope` | `text-embedding-v3` |
| `memory_extract` | `dashscope` | `qwen3-max` |

You can override providers and models in `config.yaml`:

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

Current limitation: task-style Skills still run through the Claude SDK Sub-Agent
path. Keep `roles.subagent` on `claude_sdk` unless you are changing the runtime
implementation.

## Local Workspace

EvoPaw reads optional bootstrap files from `data/workspace` before each agent
turn. These files are personal runtime data and are intentionally ignored by Git.

```bash
mkdir -p data/workspace
touch data/workspace/soul.md
touch data/workspace/user.md
touch data/workspace/agent.md
touch data/workspace/memory.md
```

Bootstrap files:

| File | Purpose |
| --- | --- |
| `soul.md` | Assistant identity and tone |
| `user.md` | User profile, preferences, recurring context |
| `agent.md` | Local operating rules and tool guidance |
| `memory.md` | Long-term memory index |

If these files are missing, EvoPaw skips the corresponding bootstrap section and
continues to run.

## Run

### Docker Compose

Docker Compose starts the app and PostgreSQL with pgvector:

```bash
docker compose up -d --build
```

When running inside Compose, set `memory.db_dsn` in `config.yaml` to the Compose
service host:

```yaml
memory:
  db_dsn: "postgresql://evopaw:evopaw123@evopaw-pgvector:5432/evopaw_memory"
```

Services:

| Service | Purpose |
| --- | --- |
| `evopaw-main` | Python app and agent runtime |
| `pgvector` | PostgreSQL 16 with pgvector |

### Manual Run

Start only pgvector if you want semantic memory:

```bash
docker compose -f pgvector-docker-compose.yaml up -d
```

For a manual Python process, keep the database host as `localhost`:

```yaml
memory:
  db_dsn: "postgresql://evopaw:evopaw123@localhost:5432/evopaw_memory"
```

Then start EvoPaw:

```bash
python3 -m evopaw.main
```

Runtime endpoints and files:

| Item | Location |
| --- | --- |
| Prometheus metrics | `http://127.0.0.1:9100/metrics` |
| Runtime logs | `data/logs/evopaw.log` |
| Session data | `data/sessions/` |
| Context snapshots | `data/ctx/` |
| Workspace data | `data/workspace/` |

## Local TestAPI

Enable the TestAPI in `config.yaml`:

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

## Slash Commands

| Command | Description |
| --- | --- |
| `/new` | Start a fresh session |
| `/verbose on` | Stream tool progress to Feishu |
| `/verbose off` | Stop streaming tool progress |
| `/verbose` | Show verbose-mode status |
| `/status` | Show current session details |
| `/help` | Show command help |

## Skills

The authoritative Skills list lives in `evopaw/skills/load_skills.yaml`.

| Skill | Type | Purpose |
| --- | --- | --- |
| `pdf` | task | PDF parsing and text extraction |
| `docx` | task | Word document handling |
| `pptx` | task | PowerPoint handling |
| `xlsx` | task | Excel handling |
| `feishu_ops` | task | Feishu docs, sheets, bitables, messages, and files |
| `scheduler_mgr` | task | Scheduled task management |
| `tavily_search` | task | Internet search through Tavily |
| `arxiv_search` | task | arXiv search and PDF retrieval |
| `web_browse` | task | Web content extraction |
| `history_reader` | reference | Inline paginated conversation history |
| `memory-save` | task | Save durable memory |
| `search_memory` | task | Search pgvector-backed memory |
| `memory-governance` | task | Review and clean memory files |
| `skill-creator` | task | Turn repeatable workflows into Skills |
| `daily-summary` | task | Daily work summaries |
| `investment-report` | task | Investment research reports |
| `investment-review` | task | Portfolio review |
| `investment-consult` | task | Investment Q&A |
| `hk-investment-morning-report` | task | Hong Kong market morning report |

## Memory

| Layer | Storage | Role |
| --- | --- | --- |
| L1 Bootstrap | `data/workspace/*.md` | Identity, user profile, operating rules, memory index |
| L2 Context | `data/ctx/*.json` and `*.jsonl` | Compressed session context and raw audit log |
| L3 Vector | PostgreSQL + pgvector | Semantic search over historical turns |

If the database or DashScope key is unavailable, EvoPaw can still run; semantic
memory features degrade instead of blocking the main Feishu flow.

## Voice Messages

When Feishu sends an audio message, EvoPaw can run:

```text
Feishu audio
    -> FeishuDownloader
    -> SpeechRecognitionService
    -> FunASRRealtimeClient
    -> Main Agent
    -> Feishu reply
```

Key behavior:

- Audio bytes are sent directly to the Fun-ASR WebSocket after download.
- ASR credentials stay in the main process environment.
- Long or slow audio receives an early acknowledgement before the final reply.
- Duplicate Feishu message deliveries are deduplicated by recent `msg_id`.
- ASR metrics are exported through Prometheus.

Useful tools:

```bash
python3 scripts/audit_audio_sample_rate.py data/workspace/sessions/
python3 scripts/calibrate_thresholds.py
```

See `docs/runbooks/voice-pre-production.md` for deployment checks.

## Testing

Run the full test suite:

```bash
python3 -m pytest
```

Run only unit tests:

```bash
python3 -m pytest tests/unit/ -v --cov=evopaw --cov-report=term-missing
```

Run integration tests that do not require external LLM access:

```bash
python3 -m pytest tests/integration/ -m "not llm" -v
```

Run the voice end-to-end mock test:

```bash
python3 -m pytest tests/integration/test_voice_end_to_end.py -v
```

## Security and Local-Only Files

The following are intentionally local-only:

- `.env`
- `config.yaml`
- `data/`
- `tests/logs/`
- `workspace-init/`
- `.coverage`, `coverage.json`, `htmlcov/`
- Python and test caches

Do not commit real Feishu, Anthropic, DashScope, Tavily, database, or provider
credentials. If a secret was ever committed, rotate it and clean Git history
instead of only deleting it in a later commit.

## Further Reading

- `config.yaml.template` for all runtime options.
- `docs/message-flow.md` for message routing details.
- `docs/design-data.md` for data and memory design.
- `docs/runbooks/voice-pre-production.md` for ASR rollout checks.
