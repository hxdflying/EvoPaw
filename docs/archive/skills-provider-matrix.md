# Skill × Provider 兼容矩阵

日期：2026-04-28
对应：`docs/multi-provider-final-plan-2026-04-27.md` §5 P5

---

## 1. 背景与判断标准

P5 把 P3/P4 落地的 `SkillDispatcher` + 三族 `AgentBackend` 全面对齐到现有 Skill 生态。本矩阵负责回答两个问题：

1. **某个 Skill 在「主 Agent 跑 X provider」时是否可用？**
2. **若需要 Sub-Agent，Sub-Agent 仍是 Claude SDK + Haiku，还是已经迁出？**

### 1.1 三种主 Agent runtime

| runtime_family | provider 示例 | 备注 |
|---|---|---|
| `claude_sdk_compat` | `claude_sdk` | Claude Agent SDK + CLI（默认形态，hook 机制完整） |
| `openai_chat` | `openrouter`、`openai` 等 OpenAI 兼容端点 | 走 `httpx → /v1/chat/completions`，工具协议 `function calling` |
| `anthropic_messages` | `anthropic` 直连 | 走 `httpx → /v1/messages`，工具协议 `tool_use` |

### 1.2 Sub-Agent 现状

P5 默认结论（与 §5 P5 一致）：**Sub-Agent 仍走 Claude SDK CLI（Haiku 4.5）**。task 型 Skill 的 `dispatch()` 路径调用 `evopaw.agents.skill_agent.run_skill_agent(...)`，与主 Agent runtime 解耦。也就是：

> **主 Agent 跑 OpenAI 端点时，task Skill 的执行仍由 Claude SDK Sub-Agent 完成。**

这条线不变意味着 Skill 内部使用的 `Bash / Read / Write / Edit / Grep / Glob` 六个工具的语义、cwd 隔离、超时机制都与今天完全一致。Skill 作者无需感知主 Agent provider 的变化。

### 1.3 Skill 类型说明

| type | 谁负责执行 | dispatch 返回 |
|---|---|---|
| `reference` | 主 Agent 直接读 SKILL.md 后自行推理（无 Sub-Agent） | `<skill_instructions>...</skill_instructions>` 包裹的 SKILL.md（剥离 frontmatter） |
| `task` | Sub-Agent（Claude SDK CLI + Haiku） | Sub-Agent 最终回复文本 |
| 内联（特例） | Dispatcher 内部直接处理 | 自定义 JSON / 文本 |

目前唯一的内联特例是 `history_reader`（reference 类型，但 dispatcher 内部不调 Sub-Agent，直接分页返回 `history_all`）。

---

## 2. 完整矩阵（19 个 enabled Skill）

> 数据源：`evopaw/skills/load_skills.yaml`（2026-04-28 当日快照）。状态字段含义：
> - ✅ 直接可用（dispatcher 路径完整）
> - 🟡 主 Agent 端可用，但 Sub-Agent 受 Claude SDK CLI 可用性约束
> - ❌ 当前不工作

| # | Skill | type | 主路径 | claude_sdk_compat | openai_chat | anthropic_messages | Sub-Agent 依赖项 |
|---|---|---|---|---|---|---|---|
| 1 | pdf | task | run_skill_agent | 🟡 | 🟡 | 🟡 | pypdf / pdfplumber + 文件 IO |
| 2 | docx | task | run_skill_agent | 🟡 | 🟡 | 🟡 | python-docx / pandoc + 文件 IO |
| 3 | pptx | task | run_skill_agent | 🟡 | 🟡 | 🟡 | python-pptx / markitdown |
| 4 | xlsx | task | run_skill_agent | 🟡 | 🟡 | 🟡 | pandas / openpyxl |
| 5 | feishu_ops | task | run_skill_agent | 🟡 | 🟡 | 🟡 | lark-oapi、`workspace/.config/feishu.json` |
| 6 | scheduler_mgr | task | run_skill_agent | 🟡 | 🟡 | 🟡 | `data/cron/tasks.json` 读写 + asyncio timer |
| 7 | tavily_search | task | run_skill_agent | 🟡 | 🟡 | 🟡 | Tavily API + HTTP |
| 8 | arxiv_search | task | run_skill_agent | 🟡 | 🟡 | 🟡 | arXiv API + PDF 下载 |
| 9 | web_browse | task | run_skill_agent | 🟡 | 🟡 | 🟡 | browser tools + sandbox_convert_to_markdown |
| 10 | history_reader | reference（内联） | dispatcher 直接分页 | ✅ | ✅ | ✅ | 无（读 `history_all` 内存） |
| 11 | memory-save | task | run_skill_agent | 🟡 | 🟡 | 🟡 | workspace 文件写入 |
| 12 | skill-creator | task | run_skill_agent | 🟡 | 🟡 | 🟡 | 写入 `evopaw/skills/{name}/SKILL.md` |
| 13 | memory-governance | task | run_skill_agent | 🟡 | 🟡 | 🟡 | 审计 memory.md 死链 |
| 14 | search_memory | task | run_skill_agent | 🟡 | 🟡 | 🟡 | pgvector 查询 + Qwen embedding |
| 15 | daily-summary | task | run_skill_agent | 🟡 | 🟡 | 🟡 | ctx.json 读 + memory 写 |
| 16 | investment-report | task | run_skill_agent | 🟡 | 🟡 | 🟡 | 报告模板 + tavily/arxiv 数据源 |
| 17 | investment-review | task | run_skill_agent | 🟡 | 🟡 | 🟡 | 历史报告对比 |
| 18 | investment-consult | task | run_skill_agent | 🟡 | 🟡 | 🟡 | 投资问答模板 |
| 19 | hk-investment-morning-report | task | run_skill_agent | 🟡 | 🟡 | 🟡 | 港股早报模板 + 数据源 |

**注解**：

- 「🟡」并不是「跑不通」，而是「Sub-Agent 仍依赖 Claude SDK CLI 与本机环境」。换句话说：
  - 主 Agent 跨 provider 已统一（`SkillDispatcher` 是单一业务逻辑层）。
  - Sub-Agent 跨 provider 是 P6 的可选议题，现阶段不做。
- 唯一「✅」的 `history_reader` 是因为它根本不创建 Sub-Agent，dispatcher 内联直接读 `history_all` 返回 JSON。

---

## 3. 三族主 Agent 路径核对

下表展示「Skill 调用」从主 Agent 到 dispatcher 的实际链路。终点（`SkillDispatcher.dispatch`）是同一个对象、同一份代码，只是入口不同。

```
[claude_sdk_compat]
  query() → MCP tool "skill_loader" → claude_mcp adapter
                                       │
                                       ▼
                                SkillDispatcher.dispatch
                                       │
                                       ├── unknown   → 友好错误
                                       ├── inline    → history_reader
                                       ├── reference → <skill_instructions>...
                                       └── task      → run_skill_agent (Claude SDK Sub-Agent + Haiku)

[openai_chat]
  POST /v1/chat/completions → tool_calls 解析 → backend.run_turn 调用 dispatcher
                                                 │
                                                 ▼ （同上）

[anthropic_messages]
  POST /v1/messages → content[].type == "tool_use" → backend.run_turn 调用 dispatcher
                                                      │
                                                      ▼ （同上）
```

工具 schema 在三族下分别由：

| family | adapter | 形态 |
|---|---|---|
| claude_sdk_compat | `evopaw/skills_runtime/adapters/claude_mcp.py::build_skill_loader_server` | `@tool("skill_loader", description, {skill_name:str, task_context:str})` |
| openai_chat | `evopaw/skills_runtime/adapters/openai_tools.py::build_openai_tool_schema` | `{type:"function", function:{name, description, parameters}}` |
| anthropic_messages | `evopaw/skills_runtime/adapters/anthropic_tools.py::build_anthropic_tool_schema` | `{name, description, input_schema}` |

三个 adapter 共享 `dispatcher.get_description()` 给出的 `<available_skills>` 渐进披露 XML 文本，描述完全一致。

---

## 4. 凭证与会话隔离

跨 provider 后凭证管理保持原状：

- 主 Agent provider 凭证：通过 `ResolvedRuntime.api_key`（仅 backend 内部读取，不写日志、不进 LLM context、不持久化）。
- 飞书凭证：依然写到 `workspace/.config/feishu.json`，由 Sub-Agent 通过 `feishu_ops` Skill 脚本读取，不进入任何 LLM。
- session 隔离：`session_id` 不进 LLM context（仅 dispatcher / adapter 用作 cwd 拼接）；`workspace/sessions/{sid}/` 仍是 Sub-Agent 唯一可写区。

---

## 5. 端到端冒烟覆盖（P5 验收）

P5 单元/集成测试覆盖（`pytest tests/unit/ -q + tests/integration/test_skill_loader_e2e.py`）：

- `test_skills_runtime_dispatcher.py`：5 类分发分支单测。
- `test_skill_loader_e2e.py`：参数化 (主 runtime × Skill 类型) 的 e2e 路径校验。
- `test_main_agent.py::TestRuntimeFamilyDispatch`：三族主 runtime backend_hints 注入路径。
- `test_anthropic_messages_backend.py` / `test_openai_chat_backend.py`：tool_use → dispatcher 调用闭环。

未覆盖（按计划留给运维）：

- 三族 × 真实 LLM 端点的 e2e（标 `pytest.mark.live`，CI nightly 触发）。
- Sub-Agent 跨 provider（属 P6 范围，本期不做）。

---

## 6. 维护守则

- 在 `load_skills.yaml` 新增 / 改名 / 改 type 时，**同步更新本文 §2 表格**。
- 把 reference Skill 改成 task 或反之时，同步检查 `dispatcher.dispatch` 的对应路径是否仍是预期效果。
- 引入新 provider（如 `bedrock_converse`）时，本文 §1.1 / §3 表格各加一列，并新增对应 adapter（不改 dispatcher）。
- Sub-Agent 真要替换（P6）时，本文 §1.2 与 §2 「Sub-Agent 依赖项」列要全面重写。
