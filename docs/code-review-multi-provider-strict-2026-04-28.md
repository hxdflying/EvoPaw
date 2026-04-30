---
title: EvoPaw 多 Provider 改造严格审查报告
date: 2026-04-28
scope: 多 provider 改造主链路、Provider Runtime、AgentBackend、SkillDispatcher、memory provider 接入、配置兼容与测试覆盖
status: review_completed
fix_status: 12_of_13_fixed (2026-04-29 二轮收尾，仅 P3-3 主动跳过)
---

# EvoPaw 多 Provider 改造严格审查报告

## 0. 修复状态总览（2026-04-28 收尾）

| ID    | 严重程度 | 标题                                       | 状态        |
|-------|----------|--------------------------------------------|-------------|
| P0-1  | P0       | memory_embedding/extract 默认模型回归       | ✅ 已修复   |
| P0-2  | P0       | 切 provider 错误继承旧 `agent.planner_model` | ✅ 已修复   |
| P1-1  | P1       | `roles.subagent.provider` 跨 provider 校验  | ✅ 已修复   |
| P1-2  | P1       | memory role 没有 runtime_family 校验        | ✅ 已修复   |
| P1-3  | P1       | vision capability 主链路接通 + 三态语义     | ✅ 已修复   |
| P2-1  | P2       | 生成参数没有正式配置通道（GenerationConfig）| ✅ 已修复（2026-04-29 二轮收尾，最小化方案：直接在 TurnRequest 上加 first-class 字段） |
| P2-2  | P2       | 主 Agent 错误映射（Auth/RateLimit/Invalid） | ✅ 已修复   |
| P2-3  | P2       | memory LLM/embedding 接入 `record_llm_call` | ✅ 已修复   |
| P2-4  | P2       | `agent.timeout_s` 接通 HTTP backend         | ✅ 已修复   |
| P2-5  | P2       | max_turns 耗尽专门异常 + 用户提示中性化     | ✅ 已修复   |
| P3-1  | P3       | `ProviderSpec.default_model` 语义过载       | ✅ 由 P0-1 顺带解决（`DEFAULT_ROLE_MODELS`） |
| P3-2  | P3       | 默认 provider 清单与示例的偏差              | ✅ 已修复（2026-04-29，`config.yaml.template` 新增「内置 vs 非内置」分组与脚注） |
| P3-3  | P3       | `supports_tool_calls` 缺模型级覆盖          | ⏭ 主动跳过（无具体触发模型；「短期可接受」遵循 CLAUDE.md「不为未请求的灵活性写代码」） |
| P3-4  | P3       | task skill `session_path` 仍写死 `/workspace` | ✅ 已修复（2026-04-29，`dispatcher.py` 加注释解释容器路径映射 + 跨 session 共享数据的设计意图） |
| P3-5  | P3       | 测试对错误行为有锁死风险                    | ✅ 由 P0-1 顺带解决（更新了 `test_provider_runtime.py` 期望） |

**测试闸门**（2026-04-28 22:50 收尾时刻）：
- `python3 -m pytest tests/unit/ -q` → **896 passed**
- `python3 -m pytest tests/integration/test_e2e_conversation.py tests/integration/test_api.py tests/integration/test_skill_loader_e2e.py -m "not llm" -q` → **61 passed, 26 deselected**
- `psycopg2-binary` 已安装（解除 §3.3 阻塞）

**仍开口的项**：P2-1（生成参数 GenerationConfig）、P3-2/P3-3/P3-4（文档/能力粒度强化）—— 这些与用户在本轮确认延后；如要进 P6/P7 阶段需重新评估。

### 二轮收尾补记（2026-04-29）

P2-1 / P3-2 / P3-4 在二轮被实际修复，仅 P3-3 主动跳过：

- **P2-1**：未引入 `GenerationConfig` 抽象，改为最小化方案：在 `TurnRequest` 上直接加 `max_tokens / temperature / top_p` 三个可选 first-class 字段（与 P2-4 已有的 `timeout_s` 相同模式），从 `agent.max_tokens` / `agent.temperature` / `agent.top_p` 透传，OpenAI 与 Anthropic backend 各自构造请求体时按字段是否为 `None` 决定下发。Anthropic `_DEFAULT_MAX_TOKENS=4096` 仍作为兜底（API 必填）。新增测试：`test_generation_params_from_turn_request` / `test_generation_params_omitted_when_none`（双 backend）。
- **P3-2**：`config.yaml.template` 注释明确「内置 provider」三项（claude_sdk / anthropic / dashscope）以及「非内置 provider 必须显式声明 runtime_family / api_key_env / default_api_base」的要求；moonshot 示例标注为非内置样板。
- **P3-3**：主动跳过。无具体当前触发模型，「短期可接受」由报告自身承认；按 CLAUDE.md「不为未请求的灵活性写代码」原则不引入额外抽象。后续若出现「同一 provider 下不同模型工具调用能力差异」的实际场景，再结合该需求设计 model-level capability override。
- **P3-4**：`dispatcher.py` 在 `_workspace_root = "/workspace"` 处加注释解释为何 Sub-Agent cwd 仍是 `/workspace` 而非 session_cwd（SKILL 脚本依赖 .config / cron 等跨 session 全局资源解析自 /workspace），并标注「如未来要收紧到 session 目录需 audit 全部 SKILL.md，本轮不动」。

**二轮测试闸门**：
- `python3 -m pytest tests/unit/ -q` → **900 passed**（896 + 二轮新增 4 个 generation 参数测试）
- `python3 -m pytest tests/integration/test_e2e_conversation.py tests/integration/test_api.py tests/integration/test_skill_loader_e2e.py -m "not llm" -q` → **61 passed, 26 deselected**

## 1. 审查结论

本次审查重点检查 `evopaw` 当前多 provider 改造是否真正满足以下核心目标：

1. 默认配置下行为不回归。
2. `roles.main.provider` 能安全切到非 Claude provider。
3. `roles.subagent`、memory roles、vision capability 等配置不会给用户制造“看似支持、实际失败”的路径。
4. OpenAI-compatible / Anthropic Messages / Claude SDK 三族 backend 在工具调用、错误处理、metrics、超时和多模态输入方面语义一致。
5. 新增测试是否覆盖真实风险，而不是只覆盖模块内部 happy path。

总体结论：

多 provider 的骨架比上一版更完整：`ResolvedRuntime.extra_body` 已经接通，HTTP backend 公共基类已经抽出，tool schema 已经集中，三族 backend 和 `SkillDispatcher` 主路径也基本可用。但当前状态仍不建议标记为“P1-P5 可发布完成”。主要原因是仍存在数个 release blocker，尤其是默认 memory 模型回归、provider/model 迁移优先级错误、Sub-Agent provider 配置误导，以及 memory role runtime family 未校验。

这些问题会直接破坏“默认行为不变”和“可配置多 provider”的核心承诺。

## 2. 审查范围

本次重点阅读和验证了以下模块：

- `evopaw/provider_runtime/`
  - `models.py`
  - `registry.py`
  - `resolve.py`
  - `capabilities.py`
- `evopaw/agent_backends/`
  - `base.py`
  - `_http_chat_base.py`
  - `claude_sdk.py`
  - `openai_chat.py`
  - `anthropic_messages.py`
- `evopaw/agents/`
  - `main_agent.py`
  - `skill_agent.py`
  - `hooks.py`
- `evopaw/skills_runtime/`
  - `dispatcher.py`
  - `tool_schema.py`
  - `adapters/claude_mcp.py`
  - `adapters/openai_tools.py`
  - `adapters/anthropic_tools.py`
- `evopaw/content_builders/`
- `evopaw/memory/`
  - `context_mgmt.py`
  - `indexer.py`
  - `_dashscope_clients.py`
- `evopaw/main.py`
- `config.yaml.template`
- 对应测试：
  - `tests/unit/test_provider_runtime.py`
  - `tests/unit/test_openai_chat_backend.py`
  - `tests/unit/test_anthropic_messages_backend.py`
  - `tests/unit/test_main_agent.py`
  - `tests/unit/test_skills_runtime_dispatcher.py`
  - `tests/integration/test_skill_loader_e2e.py`

## 3. 验证证据

### 3.1 单元测试

执行命令：

```bash
python3 -m pytest tests/unit/ -q
```

结果：

```text
861 passed, 15 warnings in 22.50s
```

结论：当前单元测试整体通过，但它没有覆盖若干真实配置风险。例如 `memory_embedding` 默认模型被错误解析成 `qwen3-turbo`，现有测试反而把这个错误行为写成了期望。

### 3.2 重点非 LLM 集成测试

执行命令：

```bash
python3 -m pytest tests/integration/test_e2e_conversation.py tests/integration/test_api.py tests/integration/test_skill_loader_e2e.py -m "not llm" -q
```

结果：

```text
61 passed, 26 deselected, 3 warnings in 2.49s
```

结论：不依赖真实 LLM 的主链路、TestAPI 和 SkillDispatcher e2e 冒烟通过。

### 3.3 全量非 LLM 集成测试

执行命令：

```bash
python3 -m pytest tests/integration/ -m "not llm" -q
```

结果：

```text
ERROR tests/integration/test_memory_system.py
ModuleNotFoundError: No module named 'psycopg2'
```

当前解释器：

```text
Python 3.11.4
```

依赖状态：

```text
psycopg2 None
psycopg2_binary None
```

结论：集成测试收集阶段被环境缺失挡住。`requirements.txt` 声明了 `psycopg2-binary>=2.9.0`，但当前解释器环境未安装该依赖。

### 3.4 手动验证：memory 默认模型

执行命令：

```bash
python3 -c "from evopaw.provider_runtime import resolve_runtime; print(resolve_runtime('memory_summary', {}).model); print(resolve_runtime('memory_embedding', {}).model); print(resolve_runtime('memory_extract', {}).model)"
```

实际输出：

```text
qwen3-turbo
qwen3-turbo
qwen3-turbo
```

期望默认行为应为：

```text
memory_summary   -> qwen3-turbo
memory_embedding -> text-embedding-v3
memory_extract   -> qwen3-max
```

该输出证明默认 memory role 模型解析存在回归。

### 3.5 手动验证：切 provider 时错误继承旧 Claude 模型

执行命令：

```bash
python3 -c "from evopaw.provider_runtime import resolve_runtime; cfg={'agent': {'planner_model':'claude-sonnet-4-6'}, 'providers': {'moonshot': {'runtime_family':'openai_chat','default_model':'moonshot-v1-32k','default_api_base':'https://api.moonshot.cn/v1','api_key_env':'MOONSHOT_API_KEY'}}, 'roles': {'main': {'provider':'moonshot'}}}; rt=resolve_runtime('main', cfg); print(rt.provider_id, rt.model)"
```

实际输出：

```text
moonshot claude-sonnet-4-6
```

期望输出：

```text
moonshot moonshot-v1-32k
```

该输出证明旧字段兼容逻辑会把 Claude 模型错误套到非 Claude provider 上。

## 4. 严重问题

### P0-1 默认 memory 模型被破坏，embedding 会默认使用 chat 模型

**状态：✅ 已修复（2026-04-28）** — 引入 `DEFAULT_ROLE_MODELS`，model 优先级改为 `overrides > roles.{role}.model > legacy/env > DEFAULT_ROLE_MODELS[role] > ProviderSpec.default_model`。回归测试已加入 `tests/unit/test_provider_runtime.py`。验证：`resolve_runtime("memory_embedding", {}).model == "text-embedding-v3"`、`resolve_runtime("memory_extract", {}).model == "qwen3-max"`。

严重程度：P0 / release blocker

涉及文件：

- `evopaw/provider_runtime/registry.py`
- `evopaw/provider_runtime/resolve.py`
- `evopaw/memory/indexer.py`
- `config.yaml.template`

现状：

`dashscope` provider 在 `DEFAULT_PROVIDERS` 中只有一个统一的 `default_model="qwen3-turbo"`。`resolve_runtime()` 对 `memory_summary`、`memory_embedding`、`memory_extract` 三个 role 都使用同一个 provider default model 作为最终 fallback。

实际解析结果：

```text
memory_summary   -> qwen3-turbo
memory_embedding -> qwen3-turbo
memory_extract   -> qwen3-turbo
```

这与 `config.yaml.template` 示例中的角色默认值不一致：

```yaml
roles:
  memory_summary:    { provider: dashscope, model: qwen3-turbo }
  memory_embedding:  { provider: dashscope, model: text-embedding-v3 }
  memory_extract:    { provider: dashscope, model: qwen3-max }
```

影响：

- `memory_embedding` 会在 `embed_texts()` 中用 `qwen3-turbo` 调 embeddings API。
- 默认配置下 memory 向量化可能直接失败。
- 即使某些兼容端点返回错误，该错误发生在后台索引任务里，主链路不一定可见。
- “不写 providers / roles 块时行为与改造前完全一致”的承诺不成立。

根因：

provider default model 被错误用作所有 role 的 fallback。`ProviderSpec.default_model` 不适合表达同一 provider 下不同角色的默认模型。

建议修复：

1. 新增 role-level default model 表，例如：

   ```python
   DEFAULT_ROLE_MODELS = {
       "main": "claude-sonnet-4-6",
       "subagent": "claude-haiku-4-5",
       "memory_summary": "qwen3-turbo",
       "memory_embedding": "text-embedding-v3",
       "memory_extract": "qwen3-max",
   }
   ```

2. `resolve_runtime()` 的 model 优先级改为：

   ```text
   overrides.model
   > roles.{role}.model
   > compatible legacy/env fallback
   > DEFAULT_ROLE_MODELS[role]
   > ProviderSpec.default_model
   ```

3. 加回归测试：

   ```python
   assert resolve_runtime("memory_embedding", {}).model == "text-embedding-v3"
   assert resolve_runtime("memory_extract", {}).model == "qwen3-max"
   ```

4. 在 `configure_memory_runtime()` 中记录 resolved provider/model，确保日志能看出实际使用的 embedding 模型。

### P0-2 切 provider 时会错误继承旧 `agent.planner_model`

**状态：✅ 已修复（2026-04-28）** — 采用 Method A（lenient）：`roles.{role}.provider` 显式且与该 role 默认 provider 不同时，跳过 `agent.planner_model` / `agent.sub_agent_model` 这条 legacy fallback。验证：`{providers.moonshot, roles.main.provider=moonshot, agent.planner_model=claude-sonnet-4-6}` → `model=moonshot-v1-32k`。

严重程度：P0 / release blocker

涉及文件：

- `evopaw/provider_runtime/resolve.py`
- `config.yaml.template`
- `evopaw/main.py`

现状：

`resolve_runtime()` 先从 `roles.main.provider` 解析 provider，再单独解析 model。model 优先级中旧字段 `agent.planner_model` 高于 provider default model。这样当用户只写：

```yaml
providers:
  moonshot:
    runtime_family: openai_chat
    api_key_env: MOONSHOT_API_KEY
    default_api_base: https://api.moonshot.cn/v1
    default_model: moonshot-v1-32k

roles:
  main:
    provider: moonshot
```

并保留旧配置：

```yaml
agent:
  planner_model: claude-sonnet-4-6
```

解析结果会变成：

```text
provider_id = moonshot
model       = claude-sonnet-4-6
```

影响：

- OpenAI-compatible provider 会收到 Claude 模型名。
- 请求很可能返回 400 / model not found。
- 用户明明配置了 provider 的 `default_model`，却被旧字段覆盖。
- 迁移体验很差，且错误不直观。

根因：

旧字段兼容逻辑没有判断 provider 是否仍是默认 provider。`agent.planner_model` 本质上是旧 Claude SDK main role 的模型字段，不应该在用户显式切换 provider 时继续生效。

建议修复：

1. 如果 `roles.{role}.provider` 显式存在且与该 role 默认 provider 不同，则跳过 legacy model fallback。
2. 或者更严格：legacy model 只在 `roles.{role}` 完全不存在时生效。
3. 加回归测试：

   ```python
   cfg = {
       "agent": {"planner_model": "claude-sonnet-4-6"},
       "providers": {
           "moonshot": {
               "runtime_family": "openai_chat",
               "default_model": "moonshot-v1-32k",
               "default_api_base": "https://api.moonshot.cn/v1",
           }
       },
       "roles": {"main": {"provider": "moonshot"}},
   }
   assert resolve_runtime("main", cfg).model == "moonshot-v1-32k"
   ```

### P1-1 `roles.subagent.provider` 看起来可配置，实际仍强依赖 Claude SDK

**状态：✅ 已修复（2026-04-28）** — 启动期在 `main.py` 直接 `raise RuntimeError`：若 `sub_runtime.runtime_family != "claude_sdk_compat"`，进程拒绝启动并提示当前版本仅支持 `claude_sdk` provider 作为 Sub-Agent。同时保留 Claude CLI 检查。

严重程度：P1

涉及文件：

- `evopaw/main.py`
- `evopaw/agents/skill_agent.py`
- `evopaw/skills_runtime/dispatcher.py`
- `docs/skills-provider-matrix.md`

现状：

`main.py` 判断是否需要 Claude CLI 的逻辑是：

```python
needs_claude_cli = (
    main_runtime.runtime_family == "claude_sdk_compat"
    or sub_runtime.runtime_family == "claude_sdk_compat"
)
```

如果用户把 `roles.subagent.provider` 配成 `openai_chat`，启动时可能跳过 Claude CLI 检查。

但 task skill 的执行路径仍是：

```text
SkillDispatcher.dispatch()
-> evopaw.agents.skill_agent.run_skill_agent()
-> claude_agent_sdk.query()
```

也就是说 Sub-Agent 并没有真正支持非 Claude SDK provider。

影响：

- 配置层暗示 `subagent` 可切 provider，但运行时会失败。
- 如果主 Agent 也不是 `claude_sdk_compat`，启动阶段可能不再检查 Claude CLI，直到第一次 task skill 才暴露错误。
- 这与 `docs/skills-provider-matrix.md` 中“task Skill 始终 fallback 到 Claude SDK Sub-Agent”的真实设计不一致。

建议修复：

短期建议选择保守方案：

1. 启动期强制校验：

   ```python
   if sub_runtime.runtime_family != "claude_sdk_compat":
       raise RuntimeError("当前版本 task Skill 的 Sub-Agent 仅支持 claude_sdk_compat")
   ```

2. 或者不允许 `roles.subagent.provider` 配成非 `claude_sdk`，除非显式开启 experimental flag。
3. 保留 Claude CLI 检查，只要 task skill 启用，就必须检查 CLI。
4. 文档明确：P1-P5 只支持主 Agent 跨 provider，Sub-Agent 跨 provider 属 P6。

### P1-2 memory role 没有 runtime family 校验

**状态：✅ 已修复（2026-04-28）** — `configure_memory_runtime()`（`context_mgmt.py` / `indexer.py` 两处）在解析后强校验 `runtime_family == "openai_chat"`，否则抛 `ResolveError` fail-fast。运行时容器启动日志已能看到 `memory_summary resolved: provider=dashscope model=qwen3-turbo` 与 `indexer runtime configured: extract=qwen3-max embed=text-embedding-v3` 双行解析记录。

严重程度：P1

涉及文件：

- `evopaw/memory/context_mgmt.py`
- `evopaw/memory/indexer.py`
- `evopaw/memory/_dashscope_clients.py`
- `evopaw/provider_runtime/resolve.py`

现状：

memory 模块通过 `resolve_runtime("memory_*", cfg)` 拿到 `ResolvedRuntime`，然后无条件调用 `make_openai_client(resolved)`。

`make_openai_client()` 永远构造 OpenAI SDK client：

```python
return OpenAI(
    api_key=resolved.api_key or "",
    base_url=resolved.api_base or _DASHSCOPE_DEFAULT_BASE_URL,
)
```

如果用户配置：

```yaml
roles:
  memory_summary:
    provider: anthropic
    model: claude-haiku-4-5
```

代码会用 OpenAI SDK 访问 Anthropic base URL 的 OpenAI-compatible 路径，这并不存在。

影响：

- 用户可配置出一个静态解析通过、运行时必失败的状态。
- memory_summary、memory_extract、memory_embedding 三条链路错误不一致。
- 后台索引失败会被吞掉，只记 warning，不容易定位。

建议修复：

1. 在 `configure_memory_runtime()` 中校验：

   ```python
   if runtime.runtime_family != "openai_chat":
       raise ResolveError("memory_* roles currently require openai_chat runtime_family")
   ```

2. 对 `memory_embedding` 加更明确的能力字段，例如：

   ```python
   supports_embeddings: bool
   default_embedding_model: str | None
   ```

3. 在文档中说明：当前 memory roles 只支持 OpenAI-compatible endpoint，不支持 Anthropic Messages。

### P1-3 vision capability 已定义但主链路完全不使用

**状态：✅ 已修复（2026-04-28）** — `ProviderSpec.supports_vision` 改为三态 `bool | None`（`None` 表示沿用 family default）；`capabilities.supports_vision()` 现在是「显式优先于 family default」。`main_agent.py` 在拼装多模态 user_content 前先看 `main_runtime.supports_vision`，不支持则降级为文本注释：`[附件图片：…，当前模型不支持图像理解，已降级为纯文本]`。新增测试 `test_runtime_without_vision_drops_image_to_text`。

严重程度：P1

涉及文件：

- `evopaw/provider_runtime/registry.py`
- `evopaw/provider_runtime/capabilities.py`
- `evopaw/agents/main_agent.py`
- `evopaw/content_builders/`

现状：

`dashscope` provider 标记：

```python
supports_vision=False
```

但 `main_agent.py` 在检测到图片附件后，只按 `runtime_family` 选择 content builder：

```python
builder = pick_content_builder(main_runtime.runtime_family)
user_content = builder.build_user_content(...)
```

这意味着只要 runtime family 是 `openai_chat`，就会构造 `image_url` block，即使当前 provider 明确声明不支持 vision。

同时 `capabilities.py` 的实现也有逻辑问题：

```python
def supports_vision(spec: ProviderSpec) -> bool:
    return spec.supports_vision and _FAMILY_DEFAULTS[spec.runtime_family]["vision"]
```

`openai_chat` 的 family default vision 是 false，因此即使某个 OpenAI-compatible provider 显式 `supports_vision=True`，这个函数仍会返回 false。

影响：

- 不支持 vision 的 provider 会收到图片 block，可能返回 400。
- 支持 vision 的 OpenAI-compatible provider 也无法通过 capability 函数表达支持。
- capability 定义与实际发送行为脱节。

建议修复：

1. `main_agent.py` 发送图片前检查 provider capability。
2. 如果不支持 vision，应降级为文本提示，例如“已收到图片附件，但当前 provider 不支持视觉输入”。
3. 修正 capability 语义：

   - `ProviderSpec.supports_vision` 若显式设置，应优先于 family default。
   - 或把字段改成三态：`None` 表示使用 family default，`True/False` 表示 provider 显式覆盖。

4. 加回归测试：

   - `openai_chat + supports_vision=False + image` 不应发送 image block。
   - `openai_chat + supports_vision=True + image` 应发送 image_url block。

## 5. 中等问题

### P2-1 生成参数没有正式配置通道，注释与代码矛盾

**状态：⏸ 延后** — 已与用户确认本轮不修。该项需要新增 `GenerationConfig`（或 `RequestPolicy`）抽象，并迁移 `max_tokens` / `temperature` / `top_p` 等通用字段，影响 `TurnRequest` schema、所有 backend 与配置模板，工作量与 P0/P1 修复不在同一轮范围内。后续单独立项处理。

严重程度：P2

涉及文件：

- `evopaw/agent_backends/_http_chat_base.py`
- `evopaw/agent_backends/openai_chat.py`
- `evopaw/agent_backends/anthropic_messages.py`
- `evopaw/provider_runtime/models.py`

现状：

`anthropic_messages.py` 注释说 `max_tokens` 可被 `runtime.extra_body` 覆盖。但 `_extract_extra_body()` 会过滤掉所有通用字段：

```python
return {k: v for k, v in raw.items() if k not in cls._generic_body_fields}
```

`_generic_body_fields` 包含：

```python
"max_tokens", "temperature", "top_p", "stream"
```

因此这些参数无法通过 `extra_body` 覆盖。

影响：

- 用户无法配置 `max_tokens`、`temperature`、`top_p` 等常规参数。
- Anthropic backend 永远使用默认 `max_tokens=4096`。
- OpenAI backend 没有显式 max token 限制，行为完全交给 provider。
- 注释和代码不一致，会误导维护者。

建议修复：

1. 不要用 `extra_body` 承载通用 generation 参数。
2. 新增 `RequestPolicy` 或 `GenerationConfig`：

   ```python
   class GenerationConfig(BaseModel):
       max_tokens: int | None = None
       temperature: float | None = None
       top_p: float | None = None
       timeout_s: float | None = None
   ```

3. `extra_body` 只保留 provider-specific 字段，例如 OpenRouter 的 `provider`、DashScope 的 `enable_thinking`。

### P2-2 HTTP provider 错误被主 Agent 吞成内部错误

**状态：✅ 已修复（2026-04-28）** — `main_agent.py` 显式分类捕获：`ProviderAuthError` → 凭证错误提示（带 `provider_id`，不暴露 key）；`ProviderRateLimited` → 限流提示；`ProviderInvalidRequest` → 拒绝并附原因；`ProviderTransientError` → 网络/瞬态。新增三条单测 `test_auth_error_returns_credential_message` / `test_rate_limited_returns_throttle_message` / `test_invalid_request_returns_rejected_message`。

严重程度：P2

涉及文件：

- `evopaw/agent_backends/base.py`
- `evopaw/agent_backends/_http_chat_base.py`
- `evopaw/agents/main_agent.py`

现状：

HTTP backend 已经把错误分成：

- `ProviderAuthError`
- `ProviderRateLimited`
- `ProviderInvalidRequest`
- `ProviderTransientError`
- `ProviderUnknownError`

但 `main_agent.py` 只显式捕获：

```python
except ProviderTransientError:
except ProviderUnknownError:
except Exception:
```

因此 auth、rate limit、invalid request 都会落入 generic exception，用户只看到：

```text
Agent 发生内部错误，请稍后重试。
```

影响：

- 401/403 无法提示用户检查 API key。
- 429 无法提示限流。
- 400 无法提示 provider/model/config 请求体错误。
- 多 provider 上线后排障成本会明显增加。

建议修复：

在 `main_agent.py` 增加专门错误映射：

```python
except ProviderAuthError as exc:
    return f"Provider 鉴权失败，请检查 {main_runtime.provider_id} 凭证配置。"

except ProviderRateLimited:
    return "Provider 当前限流，请稍后重试。"

except ProviderInvalidRequest as exc:
    return f"Provider 请求配置无效：{exc}"
```

同时注意不要把 API key 或完整响应 body 原样暴露给用户。

### P2-3 LLM metrics 只覆盖主 Agent backend，不覆盖 memory LLM/embedding

**状态：✅ 已修复（2026-04-28）** — `_summarize_chunk()`（context_mgmt）、`extract_summary_and_tags()`（indexer）、`embed_texts()`（indexer）三条路径全部接入 `record_llm_call`，role 分别为 `memory_summary` / `memory_extract` / `memory_embedding`，outcome 覆盖 `success` / `error`。embedding 没有 usage 时只记 latency。新增测试见 `tests/unit/test_indexer.py::TestExtractMetrics` / `TestEmbedTextsMetrics` 与 `tests/unit/test_context_mgmt.py::TestSummarizeChunkMetrics`。

严重程度：P2

涉及文件：

- `evopaw/observability/metrics.py`
- `evopaw/memory/context_mgmt.py`
- `evopaw/memory/indexer.py`

现状：

主 Agent backend 成功和失败都会调用 `record_llm_call()`。但 memory 层三条 LLM/embedding 调用没有接入：

- `memory_summary`: `_summarize_chunk()`
- `memory_extract`: `extract_summary_and_tags()`
- `memory_embedding`: `embed_texts()`

影响：

- 多 provider 上线后，主对话 latency/cost 可见，但 memory 的成本和错误不可见。
- `memory_embedding` 默认模型回归这类问题不容易被 metrics 发现。
- 后台索引失败只打 warning，缺少 Prometheus 层面的失败计数。

建议修复：

1. 对 memory 三个 role 调用 `record_llm_call()`。
2. outcome 至少覆盖：

   ```text
   success
   auth_error
   invalid_request
   transient
   unknown_error
   ```

3. embedding tokens 如果 provider 不返回 usage，可以只记录 latency 和 outcome。

### P2-4 `agent.timeout_s` 被忽略，HTTP backend 写死 120 秒

**状态：✅ 已修复（2026-04-28）** — `TurnRequest` 新增字段 `timeout_s: float = Field(default=120.0, gt=0)`；`main.py` 从 `agent.timeout_s` 读取并通过 `build_agent_fn(agent_timeout_s=...)` 传入；`openai_chat.py` / `anthropic_messages.py` 改用 `httpx.Timeout(req.timeout_s)`。`claude_sdk_compat` 由 SDK 自管，不消费该字段。新增测试 `test_request_timeout_passed_to_async_client`（双 backend）。

严重程度：P2

涉及文件：

- `config.yaml.template`
- `evopaw/main.py`
- `evopaw/agent_backends/openai_chat.py`
- `evopaw/agent_backends/anthropic_messages.py`

现状：

配置模板中有：

```yaml
agent:
  timeout_s: 300
```

但 HTTP backend 直接写死：

```python
httpx.AsyncClient(timeout=httpx.Timeout(120.0))
```

影响：

- 用户以为配置了 300 秒，实际 120 秒超时。
- Claude SDK 路径和 HTTP provider 路径超时语义不一致。
- 长工具调用、多轮工具调用后再次请求 LLM 时，可能更容易超时。

建议修复：

1. 把 timeout 放入 `TurnRequest` 或 `ResolvedRuntime` 的 request policy。
2. 从 `agent.timeout_s` 读取并传到 backend。
3. 测试确认 HTTP backend 使用配置值，而不是硬编码值。

### P2-5 工具循环达到 `max_turns` 时被当成空回复

**状态：✅ 已修复（2026-04-28）** — 新增异常 `ProviderMaxTurnsExceeded`（`agent_backends/base.py`）；两个 HTTP backend 在 for/else 中循环耗尽即抛；`_outcome_for` 映射为 `outcome="max_turns_exceeded"`；`main_agent.py` 捕获后返回中性提示「⚠️ Agent 工具调用轮次达到上限（max_turns=N），请缩小任务范围或在配置里提高 agent.max_turns」。新增测试 `TestRunTurnMaxTurns`（双 backend）+ `test_max_turns_exceeded_returns_loop_message`。

严重程度：P2

涉及文件：

- `evopaw/agent_backends/openai_chat.py`
- `evopaw/agent_backends/anthropic_messages.py`
- `evopaw/agents/main_agent.py`

现状：

HTTP backend 的工具循环：

```python
for _turn in range(max(1, req.max_turns)):
    ...
```

如果模型每轮都继续返回 tool calls，循环耗尽后 `final_text` 仍为空。随后主 Agent 返回：

```text
Claude 未返回有效回复，请重试。
```

影响：

- 错误提示中仍写 Claude，即使当前 provider 可能是 OpenAI-compatible 或 Anthropic Messages。
- max turns exhaustion 被记录成 success outcome。
- 用户无法知道是工具循环耗尽还是 provider 空回复。

建议修复：

1. 循环耗尽时抛专门异常，例如 `ProviderMaxTurnsExceeded`。
2. metrics outcome 使用 `max_turns_exceeded`。
3. 用户提示改为 provider-neutral：

   ```text
   Agent 工具调用轮次达到上限，请缩小任务范围或提高 agent.max_turns。
   ```

## 6. 轻量问题与改进建议

### P3-1 `ProviderSpec.default_model` 语义过载

**状态：✅ 由 P0-1 顺带解决** — `DEFAULT_ROLE_MODELS` 已把「角色默认模型」与「provider 默认模型」分离；`provider_spec.default_model` 仅作为最低优先级 fallback。

`default_model` 同时被用于主模型、摘要模型、embedding 模型和抽取模型，已经造成 P0-1。建议把 provider 默认模型与 role 默认模型分离。

### P3-2 默认 provider 清单偏少，但配置模板示例暗示支持更多 provider

**状态：❌ 未修复** — 文档级建议，本轮未列入。后续在 `docs/skills-provider-matrix.md` 或 `config.yaml.template` 注释里明确「内置 vs 示例」即可。

内置 provider 只有：

- `claude_sdk`
- `anthropic`
- `dashscope`

模板示例展示 `moonshot`，docker-compose 又加入 `OPENAI_API_KEY`、`MOONSHOT_API_KEY`。这不是 bug，但会造成“看起来内置 openai/moonshot，实际需要用户手写 providers 块”的误解。建议文档明确哪些是内置 provider，哪些只是示例。

### P3-3 `supports_tool_calls` 只按 provider 配置判断，缺少模型级覆盖

**状态：❌ 未修复** — 能力粒度增强，本轮未列入。后续若引入 model-level capability override 一并处理。

同一个 provider 下不同模型工具调用能力可能不同。例如某些轻量模型、视觉模型、reasoning 模型的 tool call 支持差异较大。当前 `ProviderSpec` 粒度偏粗。短期可接受，但后续应支持 model overrides。

### P3-4 `SkillDispatcher` 仍把 task skill 的 `session_path` 固定为 `/workspace`

**状态：❌ 未修复** — 设计澄清类，本轮未列入。后续要么调整 Sub-Agent cwd，要么在 `docs/skills-provider-matrix.md` 添加说明。

现有旧逻辑也是如此，但多 provider 改造后，主 Agent 的 `TurnRequest.cwd` 是具体 session 目录，而 task skill 仍从 `/workspace` 开始。这依赖 skill 指令中的 `{session_dir}` 来约束写入路径。建议未来把 Sub-Agent cwd 改为当前 session 目录，或至少在文档中解释为什么仍用 `/workspace`。

### P3-5 测试对错误行为有“锁死”风险

**状态：✅ 由 P0-1 顺带解决** — 修 P0-1 时已经更新 `tests/unit/test_provider_runtime.py` 的期望，并新增针对 `DEFAULT_ROLE_MODELS` 的回归用例。

`tests/unit/test_provider_runtime.py` 中 `test_default_model_used_when_nothing_specified` 目前期望 `memory_embedding` 使用 `DEFAULT_PROVIDERS["dashscope"].default_model`。这等于把 P0-1 错误写进测试。应先修测试期望，再修 resolver。

## 7. 修复优先级

建议按以下顺序处理：

1. 修复 `memory_embedding` / `memory_extract` 默认模型回归。
2. 修复切 provider 时错误继承旧 `agent.planner_model` / `agent.sub_agent_model` 的问题。
3. 明确禁止或强校验非 `claude_sdk_compat` 的 `roles.subagent`。
4. 对 memory roles 强制校验 `runtime_family == openai_chat`。
5. 接入 vision capability，避免不支持视觉的 provider 收到 image block。
6. 增加 `ProviderAuthError` / `ProviderRateLimited` / `ProviderInvalidRequest` 的主 Agent 友好错误映射。
7. 把 `agent.timeout_s` 接入 HTTP backend。
8. 将 generation 参数从 `extra_body` 中拆出，形成正式 request policy。
9. 给 memory LLM/embedding 调用补齐 metrics。
10. 对 max-turns exhaustion 增加明确异常和 metrics outcome。

## 8. 建议新增测试

### 8.1 provider resolver 回归测试

```python
def test_memory_embedding_default_model_is_embedding_model():
    assert resolve_runtime("memory_embedding", {}).model == "text-embedding-v3"


def test_memory_extract_default_model_is_extract_model():
    assert resolve_runtime("memory_extract", {}).model == "qwen3-max"


def test_switching_provider_uses_provider_default_not_legacy_claude_model():
    cfg = {
        "agent": {"planner_model": "claude-sonnet-4-6"},
        "providers": {
            "moonshot": {
                "runtime_family": "openai_chat",
                "default_api_base": "https://api.moonshot.cn/v1",
                "default_model": "moonshot-v1-32k",
            }
        },
        "roles": {"main": {"provider": "moonshot"}},
    }
    rt = resolve_runtime("main", cfg)
    assert rt.provider_id == "moonshot"
    assert rt.model == "moonshot-v1-32k"
```

### 8.2 Sub-Agent runtime 校验测试

```python
def test_subagent_non_claude_runtime_rejected_at_startup():
    cfg = {
        "providers": {
            "moonshot": {
                "runtime_family": "openai_chat",
                "default_model": "moonshot-v1-32k",
                "default_api_base": "https://api.moonshot.cn/v1",
            }
        },
        "roles": {
            "subagent": {"provider": "moonshot"}
        },
    }
    with pytest.raises(RuntimeError, match="Sub-Agent"):
        validate_runtimes(cfg)
```

### 8.3 memory runtime family 校验测试

```python
def test_memory_summary_rejects_anthropic_messages_runtime():
    cfg = {
        "roles": {
            "memory_summary": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
            }
        }
    }
    with pytest.raises(ResolveError):
        configure_memory_runtime(cfg)
```

### 8.4 vision capability 测试

```python
async def test_openai_chat_provider_without_vision_does_not_send_image_block():
    runtime = ResolvedRuntime(
        role="main",
        provider_id="dashscope",
        runtime_family="openai_chat",
        model="qwen3-turbo",
    )
    # 附图消息下，TurnRequest.user_content 应降级为纯文本或提示，而不是 image_url blocks。
```

### 8.5 HTTP backend 超时测试

```python
async def test_http_backend_uses_request_timeout_from_turn_request():
    req = TurnRequest(..., timeout_s=300)
    await OpenAIChatBackend().run_turn(req)
    assert httpx.AsyncClient.call_args.kwargs["timeout"] == httpx.Timeout(300.0)
```

## 9. 发布判断

当前不建议以“多 provider 改造完成”发布。

可以接受的发布前最低门槛：

1. P0-1 与 P0-2 必须修复。
2. Sub-Agent 非 Claude provider 必须被显式拒绝或明确标记 experimental。
3. memory roles 必须限制在当前真实支持的 runtime family。
4. `tests/unit/test_provider_runtime.py` 必须增加默认 memory 模型和 provider/model 迁移回归测试。
5. 至少跑通：

   ```bash
   python3 -m pytest tests/unit/ -q
   python3 -m pytest tests/integration/test_e2e_conversation.py tests/integration/test_api.py tests/integration/test_skill_loader_e2e.py -m "not llm" -q
   ```

如果要声明完整集成测试可用，还需要先修复当前环境缺少 `psycopg2` 导致的 collection failure。

### 9.1 收尾验证（2026-04-28）

| 闸门 | 当时结果 | 收尾结果 |
|------|---------|---------|
| 1. P0-1/P0-2 修复 | 未修 | ✅ |
| 2. Sub-Agent 非 Claude 显式拒绝 | 未做 | ✅（启动期 RuntimeError） |
| 3. memory roles runtime_family 限制 | 未做 | ✅（fail-fast 抛 ResolveError） |
| 4. test_provider_runtime.py 回归用例 | 缺失 | ✅（已加） |
| 5. 单元 + 三档 smoke 集成跑通 | 未跑 | ✅（896 + 61 passed） |
| 6. `psycopg2` 阻塞 | 阻塞 | ✅（已装 psycopg2-binary） |

**剩余开口**：P2-1（生成参数 GenerationConfig）、P3-2/P3-3/P3-4（文档/能力粒度强化）—— 与用户确认本轮延后；进 P6/P7 阶段时重新评估。

