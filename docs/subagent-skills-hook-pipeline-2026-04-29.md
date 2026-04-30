# Sub-Agent · Skills · Hook 全链路 (2026-04-29)

> 本文记录多 provider 改造（P1–P5 完成态）后，EvoPaw 中 **Sub-Agent / Skills / Hook** 三者从飞书事件入站到最终回复的端到端调用链路。读者预期：熟悉 CLAUDE.md，能读 Python。

---

## 0. TL;DR

- 每条飞书消息 → 1 次主 Agent 轮次 → 1 个 **新建** `SkillDispatcher`（或 SDK MCP server，同一份 dispatcher 内核被 SDK 包了一层）
- 每个 task 型 Skill 调用 → 1 个 **全新** Claude SDK `query()` session（Sub-Agent，cwd=`/workspace`）
- Hook 只覆盖**主 Agent**，并且只在 `verbose=True` 且 routing_key 不是 `thread:*` 时才生效；Sub-Agent 内部工具调用对用户黑盒
- Skills 的唯一入口是 `skill_loader`，三家 backend（claude_sdk_compat / openai_chat / anthropic_messages）共享同一个 `SkillDispatcher` 内核

---

## 1. 启动期装配

`evopaw/main.py:269-297` 完成所有依赖注入，工厂返回 `agent_fn` 闭包：

```text
build_agent_fn(
    sender,              # FeishuSender，verbose 推送的实际通道
    workspace_dir,       # docker 挂到容器内 /workspace
    ctx_dir,             # data/ctx
    main_runtime,        # ResolvedRuntime，由 resolve_runtime("main") 解析
    sub_runtime,         # ResolvedRuntime，由 resolve_runtime("subagent") 解析（默认锁 claude_sdk_compat）
    db_dsn,              # pgvector 连接串
    agent_max_turns=50,
    sub_agent_max_turns=20,
)
→ 返回 agent_fn 闭包
→ Runner(agent_fn=agent_fn, ...)
```

`agent_fn` 是闭包，`build_agent_fn` 之后所有飞书消息共享同一份闭包；每条消息内部再新建 dispatcher / sink。

---

## 2. 消息进入：Feishu → Runner → agent_fn

```text
Feishu WebSocket 事件
└─ FeishuListener.handle_event           (evopaw/feishu/listener.py)
    └─ 构造 InboundMessage
        └─ SessionRouter (按 routing_key 路由)
            └─ Runner._handle_inbound    (evopaw/runner.py)
                ├─ 1. Slash 命令拦截（/new /verbose /help /status）→ 不进 Agent
                ├─ 2. 附件下载到 session sandbox
                ├─ 3. 语音 ASR / 多模态拼装 → user_content
                ├─ 4. session_mgr.load_history → history
                ├─ 5. sender.send_thinking → 飞书 loading 卡片
                ├─ 6. await agent_fn(user_content, history, session_id,
                │                    routing_key, root_id, verbose)
                └─ 7. 处理回复（字数 / 卡片 / Audit）→ sender.send_*
```

`routing_key` 三种形态决定 Hook 行为（详见 §7）：

- `p2p:{open_id}` —— 私聊，verbose 推送启用
- `group:{chat_id}` —— 群聊，verbose 推送启用
- `thread:{chat_id}:{thread_id}` —— 话题，**verbose 推送禁用**（避免污染话题）

---

## 3. agent_fn 内部：组装一次 `TurnRequest`

`evopaw/agents/main_agent.py:124-245` 是 Skills 与 Hook 的**装配点**。

```text
agent_fn(user_message, history, session_id, routing_key, root_id, verbose):
├─ 1. system_prompt = build_bootstrap_prompt(workspace_dir)        # L1 Bootstrap 记忆
│   + 追加 <tool_constraint>：只允许 skill_loader，禁用 Claude CLI 内置 skill
│
├─ 2. ctx_messages = load_session_ctx(session_id)                  # L2 长期上下文摘要
│   ctx_summary_text = _format_ctx_summaries(...)
│
├─ 3. 拼 user_content：
│   <long_term_context> + <conversation_history> + 用户消息
│
├─ 3b. 多模态分支：
│   builder = pick_content_builder(main_runtime.runtime_family)
│   ├─ supports_vision=True  → 加载图片 base64 → builder.build_user_content
│   └─ supports_vision=False → 降级为纯文本 + 提示语（防止 OpenAI 兼容端 400）
│
├─ 4. session_cwd = workspace/sessions/{session_id}/               # 主 Agent 的 cwd
│
├─ 5. 按 runtime_family 装 backend_hints（Skills 装配的两条路径）：
│   ┌──────────────────────────────────────────────────────────────────┐
│   │ claude_sdk_compat                                                │
│   │ ──────────────                                                   │
│   │ skill_server = build_skill_loader_server(                        │
│   │      session_id, routing_key, history_all,                       │
│   │      sub_agent_model, sub_agent_max_turns)                       │
│   │ → 内部新建 SkillDispatcher，包成 SDK MCP server (@tool skill_…)  │
│   │ backend_hints = {"mcp_servers": {"evopaw": skill_server}}        │
│   ├──────────────────────────────────────────────────────────────────┤
│   │ openai_chat / anthropic_messages                                 │
│   │ ──────────────                                                   │
│   │ dispatcher = SkillDispatcher(                                    │
│   │      session_id, routing_key, history_all,                       │
│   │      sub_agent_model, sub_agent_max_turns)                       │
│   │ backend_hints = {"skill_dispatcher": dispatcher}                 │
│   └──────────────────────────────────────────────────────────────────┘
│   两条路径 dispatcher / MCP server 都是 **每轮新建**，绑当前 session_id。
│
├─ 6. Hook 装配（verbose 模式）：
│   if verbose and not routing_key.startswith("thread:"):
│       async _send(text): await sender.send_text(routing_key, text, root_id)
│       stream_sink = FeishuStreamSink(send=_send)
│   else:
│       stream_sink = None
│   ※ thread 场景和 sub-agent 都不接 stream_sink
│
├─ 7. req = TurnRequest(role="main", runtime=main_runtime,
│                       system_prompt, user_content, cwd=session_cwd,
│                       max_turns=50, stream_sink, backend_hints)
│
├─ 8. backend = get_backend(main_runtime)
│   result = await backend.run_turn(req)                              # 进入 §4
│
├─ 9. 持久化：
│   ├─ ctx.json snapshot + raw.jsonl 审计 (memory/context_mgmt)
│   ├─ asyncio.create_task(async_index_turn(...))                     # L3 pgvector 异步
│   └─ sender.record_skills(root_id, result.skills_called)            # TestAPI 用
│
└─ return result.text
```

**关键不变量**：每条消息都新建 dispatcher / MCP server。dispatcher 绑当前 `session_id` + `history_all`，所以 history_reader 才能"看到"全量历史；session 之间彼此隔离。

---

## 4. Backend 工具循环（三家形态不同，dispatcher 是单一抽象）

### 4-A `ClaudeSDKCompatBackend.run_turn` —— Hook 是注册回调

`evopaw/agent_backends/claude_sdk.py:87-145`

```text
run_turn(req):
├─ hooks = build_stream_sink_hooks(req.stream_sink)                   # 把 sink 包成 SDK hooks
│   PreToolUse  matcher=".*" → on_tool_use(name, input)
│   PostToolUse matcher=".*" → on_tool_result(name, output)
│   sink=None 时返回 {} (verbose 关闭)
│
├─ mcp_servers = req.backend_hints["mcp_servers"]                     # skill_loader server
│
├─ options = ClaudeAgentOptions(
│      model, system_prompt, allowed_tools=[],
│      cwd=session_cwd, max_turns=50,
│      hooks=hooks, mcp_servers=mcp_servers,
│      permission_mode="bypassPermissions")
│
├─ async for message in query(prompt=user_content, options=options):
│   ├─ AssistantMessage → 收集 ToolUseBlock 到 tool_calls
│   │                  → 提取 skill_loader 的 skill_name 到 skills_called
│   │  （此时 SDK 已经触发 hooks → FeishuStreamSink → 飞书 send_text）
│   └─ ResultMessage   → final_text = message.result, usage
│
└─ return TurnResult(text, tool_calls, skills_called, usage)
```

工具循环由 SDK 在 CLI 进程内自管，evopaw 只看到 `AssistantMessage / ResultMessage` 两种事件。

### 4-B `OpenAIChatBackend.run_turn` / `AnthropicMessagesBackend.run_turn` —— Hook 是显式 await

`evopaw/agent_backends/openai_chat.py:155-313`（Anthropic 同构于 `anthropic_messages.py`）

```text
run_turn(req):
├─ dispatcher = req.backend_hints["skill_dispatcher"]
├─ tools_schema = [build_openai_tool_schema(dispatcher)]              # 单工具 skill_loader
│   description = dispatcher.get_description()                        # 阶段一 XML
│
├─ messages = _build_messages(req)
│
├─ for _turn in range(max_turns):
│   ├─ POST /chat/completions { messages, tools, ... }
│   ├─ 累计 usage
│   ├─ 解析 finish_reason / tool_calls
│   ├─ 有 tool_calls：
│   │   ├─ append assistant 消息（带 tool_calls + reasoning_content）
│   │   ├─ for tc in tool_calls:
│   │   │   ├─ name == "skill_loader" → skills_called.append
│   │   │   ├─ stream_sink?.on_tool_use(name, args)                   # Hook 直驱
│   │   │   ├─ tool_text = await dispatcher.dispatch(skill_name, task_context)
│   │   │   ├─ stream_sink?.on_tool_result(name, tool_text)
│   │   │   └─ append {role:"tool", tool_call_id, content:tool_text}
│   │   └─ continue 进入下一轮
│   └─ 无 tool_calls：final_text = msg.content; break
│
└─ for-else 触底 → ProviderMaxTurnsExceeded
```

**两条路径对照**：

| 维度 | claude_sdk_compat | openai_chat / anthropic_messages |
|---|---|---|
| 工具循环 | SDK 内部（CLI 进程） | evopaw backend 自己拥有 `for _turn in range(max_turns)` |
| Hook 触发 | SDK 注册回调（PreToolUse / PostToolUse） | backend 显式 `await stream_sink.on_*` |
| Skills 暴露 | SDK MCP server (`@tool skill_loader`) | OpenAI tools schema / Anthropic input_schema |
| 工具结果回填 | SDK 自管 | backend 显式 append `{role:"tool", tool_call_id, ...}` |
| `tool_call_id` 来源 | SDK 自管 | OpenAI `tc.id` / Anthropic `tool_use_id` |

---

## 5. Skill 调度：`SkillDispatcher.dispatch`

`evopaw/skills_runtime/dispatcher.py:129-187`，**三家 backend 共享的纯逻辑**。

```text
dispatch(skill_name, task_context):
├─ ctx_str = _normalize_task_context(task_context)                    # dict→json.dumps
│
├─ 分支 1：skill_name 不在 registry
│   → 返回 "错误：未找到 Skill ..." + available 列表
│
├─ 分支 2：skill_name == "history_reader"
│   → _handle_history_reader(history_all, ctx_str)
│   → 内联分页 (page/page_size) → 返回 JSON {messages, total, ...}
│   ※ 不创建 sub-agent（dispatcher 内联完成）
│
├─ 分支 3：registry[skill]["type"] == "reference"
│   → instructions = _get_skill_instructions(...)
│       ├─ 读 SKILL.md，剥离 frontmatter
│       ├─ 替换占位符 {skill_base}/{session_id}/{session_dir}
│       └─ 拼 <execution_directive>（含 routing_key）
│   → 返回 "<skill_instructions>...</skill_instructions>"
│   ※ 主 Agent 自己消化，不创建 sub-agent
│
└─ 分支 4：registry[skill]["type"] == "task"
    ├─ instructions 同上
    ├─ workspace_root = "/workspace"                                  # 容器路径，跨 session 共享
    ├─ from evopaw.agents.skill_agent import run_skill_agent          # 延迟 import
    └─ result_text = await run_skill_agent(
            skill_name=skill_name,
            skill_instructions=instructions,                          # SKILL.md 正文 + execution_directive
            task_context=ctx_str,
            session_path="/workspace",
            model=sub_agent_model,                                    # 默认 claude-haiku-4-5
            max_turns=sub_agent_max_turns,                            # 默认 20
       )
       → 进入 Sub-Agent，详见 §6
```

`registry` 由 `evopaw/skills_runtime/registry.py:_build_skill_registry` 从 `skills/load_skills.yaml` 构建；`instructions` 由 `evopaw/skills_runtime/instructions.py` 组装并被 `_instruction_cache` 复用（单 dispatcher 实例内）。

---

## 6. Sub-Agent 内部：`run_skill_agent` → 第二个 SDK query loop

`evopaw/agents/skill_agent.py`

```text
run_skill_agent(skill_name, skill_instructions, task_context,
                session_path="/workspace", model="claude-haiku-4-5",
                max_turns=20):
├─ options = build_sub_agent_options(
│       system_prompt = skill_instructions,                           # SKILL.md 正文 + execution_directive
│       cwd           = "/workspace",                                 # 注：不是 session_cwd，跨 session 共享资源
│       model         = "claude-haiku-4-5",
│       allowed_tools = ["Bash","Read","Write","Edit","Grep","Glob"],
│       max_turns     = 20,
│       permission_mode = "bypassPermissions",
│   )    ※ 注意：没有 hooks，没有 mcp_servers
│
├─ async for message in query(prompt=task_context, options=options):
│   └─ ResultMessage → final_text = message.result
│
└─ return final_text                                                  # 字符串原样回到 dispatcher
```

**Sub-Agent 是全新的 SDK query session**（与主 Agent 完全独立），生命周期 = 单次 skill 调用。SDK 内部用这 6 个工具完成多轮工具调用、文件 IO、子进程，evopaw 只接最终 `ResultMessage`。

为什么 cwd 固定 `/workspace` 而不是 `sessions/{sid}/`：

1. SKILL.md / 脚本（pdf、docx、feishu_ops、scheduler_mgr 等）普遍假设相对路径解析自 `/workspace`（如 `.config/feishu.json`、`cron/tasks.json`）
2. session 隔离由 SKILL.md 内显式拼 `sessions/{session_dir}/...` 完成，不依赖 cwd
3. docker-compose 把 `workspace_dir` 挂到 `/workspace`

字符串结果回流：

```text
sub-agent final_text
   ↓
dispatcher.dispatch 返回字符串
   ↓
backend 把字符串作为 tool_result 回填 messages
   ↓
主 Agent 下一轮看到工具结果 → 继续推理 → 最终 final_text
   ↓
agent_fn 返回 → Runner → sender.send_card / send_text
```

---

## 7. Hook 链路总览

`evopaw/agents/hooks.py`

| 触发点 | 通道 | 主要消费者 |
|---|---|---|
| 主 Agent / SDK 工具 PreToolUse | `build_stream_sink_hooks` 注册到 `ClaudeAgentOptions(hooks=...)`，由 SDK 在 CLI 进程内触发 | `FeishuStreamSink._send` → `sender.send_text` → 飞书 |
| 主 Agent / SDK 工具 PostToolUse | 同上 | 同上 |
| 主 Agent / OpenAI 工具调用前 | `OpenAIChatBackend.run_turn` 显式 `await req.stream_sink.on_tool_use(...)` | 同上 |
| 主 Agent / OpenAI 工具调用后 | 同上 | 同上 |
| 主 Agent / Anthropic 工具调用前 / 后 | `AnthropicMessagesBackend.run_turn` 显式 `await stream_sink.on_*` | 同上 |
| Sub-Agent 内部工具 | **不接 hook**（`build_sub_agent_options` 不传 hooks） | — |
| `thread:*` routing_key | **不创建 stream_sink**（`main_agent.py:226` 行） | — |

文本格式（字节级一致）：

- `💭 即将调用工具 {tool_name}`
- `✅ 工具 {tool_name} 完成`

`build_verbose_hooks(callback)` 是旧 API，内部已经走 `build_stream_sink_hooks(_CallbackSink(callback))`，仅为兼容老测试 `tests/unit/test_hooks.py` 保留。

`StreamSink` 是 `Protocol`（`evopaw/agent_backends/base.py:80-90`，`runtime_checkable`），`FeishuStreamSink` 实现协议但无需显式继承。`on_tool_use / on_tool_result` 内部的异常被 sink 自己 try/except 吞掉，保护主流程。

---

## 8. 一图流（合并视角）

```text
飞书事件 ──► Listener ──► Router ──► Runner ──► agent_fn(闭包)
                                                   │
                                                   │  组装：
                                                   │   - system_prompt (Bootstrap + tool_constraint)
                                                   │   - ctx 摘要 / history / 多模态 user_content
                                                   │   - SkillDispatcher / SDK MCP server  ◄─ Skills 装配点
                                                   │   - FeishuStreamSink                  ◄─ Hook 装配点
                                                   ▼
                          ┌────────────────────────────────────────────┐
                          │  TurnRequest(role=main, runtime, hints,    │
                          │              stream_sink, ...)             │
                          └─────────────────┬──────────────────────────┘
                                            ▼
              ┌─────────────────────────────────────────────────────────┐
              │  get_backend(runtime).run_turn(req)                     │
              │                                                         │
              │  ┌───────────────────┐  ┌────────────────────────────┐  │
              │  │ ClaudeSDK Backend │  │ OpenAI/Anthropic Backend   │  │
              │  │  - SDK query loop │  │  - HTTP + tools_schema     │  │
              │  │  - hooks (PreUse) │  │  - 自身循环                │  │
              │  │  - hooks (PostUse)│  │  - 显式 await stream_sink  │  │
              │  │  - tool=skill_…   │  │  - tool=skill_loader       │  │
              │  └────────┬──────────┘  └────────────┬───────────────┘  │
              │           │                          │                   │
              │           └──────► dispatcher.dispatch(skill, ctx) ◄────┤
              └────────────────────┬────────────────────────────────────┘
                                   │
                ┌──────────────────┴────────────────────────┐
                │                                           │
   分支1: 未知Skill        分支2: history_reader 内联    分支3: reference     分支4: task
   返回错误+available     从 history_all 分页 → JSON     返回 SKILL.md 全文   ▼
                                                                      run_skill_agent
                                                                      (claude_agent_sdk.query
                                                                       新 session, cwd=/workspace
                                                                       Haiku 4.5
                                                                       Bash/Read/Write/Edit/Grep/Glob)
                                                                              │
                                                                              ▼
                                                                       ResultMessage.result
                                                                              │
                                                              ◄───────────────┘
                                                       字符串原路返回到 backend
                                                              ▼
                                          backend 把结果回填 messages，进入下一轮
                                                              ▼
                                                  最终 final_text 返回 agent_fn
                                                              ▼
                                       持久化（ctx.json + raw.jsonl + pgvector异步）
                                                              ▼
                                                  Runner → sender 发飞书消息
```

---

## 9. 关键不变量速查

1. **每条消息 = 1 个主轮次 = 1 个 dispatcher / MCP server 实例**（绑当前 `session_id` / `history_all`）
2. **每个 task skill 调用 = 1 个全新 SDK query session**（短生命周期，cwd=`/workspace`）
3. **Sub-Agent 永远是 Claude SDK CLI**（与主 runtime 解耦；P6 评估见 `multi-provider-final-plan-2026-04-27.md` §5 P6）
4. **Hook 不进 Sub-Agent**（设计上阻断，避免飞书噪音）
5. **`thread:*` 不进 Hook**（避免污染话题）
6. **Skills 单一入口 = `skill_loader`**（无论哪条 backend）
7. **`history_reader` 永不触发 Sub-Agent**（dispatcher 内联返回）
8. **`session_id` 不进 LLM**：只把 `/workspace/sessions/{session_id}/` 路径字符串注入到 SKILL.md instruction（防泄漏）

---

## 10. 关键文件锚点

| 节点 | 文件:行 |
|---|---|
| 启动期工厂调用 | `evopaw/main.py:269-297` |
| Runner 调用 agent_fn | `evopaw/runner.py:344` |
| agent_fn 闭包定义 | `evopaw/agents/main_agent.py:124-245` |
| backend 选择 | `evopaw/agents/main_agent.py:200-222` |
| Hook 装配 | `evopaw/agents/main_agent.py:225-229` |
| Claude SDK backend | `evopaw/agent_backends/claude_sdk.py:87-145` |
| OpenAI Chat backend | `evopaw/agent_backends/openai_chat.py:155-313` |
| Anthropic Messages backend | `evopaw/agent_backends/anthropic_messages.py` |
| StreamSink 协议 | `evopaw/agent_backends/base.py:80-90` |
| FeishuStreamSink + SDK hooks | `evopaw/agents/hooks.py` |
| Skills 单入口工具名 | `evopaw/skills_runtime/tool_schema.py:20` |
| dispatcher 主体 | `evopaw/skills_runtime/dispatcher.py:129-187` |
| SDK MCP adapter | `evopaw/skills_runtime/adapters/claude_mcp.py` |
| OpenAI tool schema adapter | `evopaw/skills_runtime/adapters/openai_tools.py` |
| Anthropic tool schema adapter | `evopaw/skills_runtime/adapters/anthropic_tools.py` |
| SKILL.md 注册 / 占位符替换 | `evopaw/skills_runtime/registry.py`、`evopaw/skills_runtime/instructions.py` |
| Sub-Agent 入口 | `evopaw/agents/skill_agent.py` |
| Sub-Agent options 构造 | `evopaw/llm/claude_client.py:65-86` |

---

## 11. 相关文档

- `docs/multi-provider-final-plan-2026-04-27.md` —— 多 provider 改造路线（P1–P6）
- `docs/skills-provider-matrix.md` —— 18 个 Skill 跨 provider 兼容矩阵
- `docs/message-flow.md` —— 飞书消息层流转（Listener / Router / Sender）
- `CLAUDE.md` —— 架构总述（消息流、三层记忆、关键设计决策）
