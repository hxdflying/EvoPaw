---
title: 多 Provider 改造代码审查报告
date: 2026-04-28
author: Claude (审查) / 用户委托
scope: P1-P5 多 provider 改造完成后的全项目冗余审查
---

# 多 Provider 改造代码审查报告

> 审查范围：`evopaw/agent_backends/`、`evopaw/provider_runtime/`、`evopaw/content_builders/`、`evopaw/skills_runtime/`、`evopaw/agents/`、`evopaw/main.py`、`evopaw/llm/`、`evopaw/memory/`、`evopaw/tools/`，以及对应的 `tests/unit/` 与 `docs/`。
>
> 审查方法：静态阅读 + grep 验证 + 与 `docs/multi-provider-final-plan-2026-04-27.md` 落地结果对照。

---

## 0. 总览

多 provider 改造（P1-P5）整体设计合理：`ProviderSpec` 作为静态 metadata、`ResolvedRuntime` 作为一次性快照、三个 `runtime_family` 加 `AgentBackend` Protocol，把品牌（Kimi/DeepSeek/OpenRouter…）和协议族（openai_chat/anthropic_messages/claude_sdk_compat）解耦——这套骨架是干净的。但**落地阶段在「补全空缺」时积累了大量重复代码、未接通的死路径、为兼容旧测试而保留的薄壳文件**，其中 1 项是「**已声明但未连通的特性**」，需要立即处理。

下面按严重程度分四档：

| 级别 | 说明 | 数量 |
|------|------|------|
| **P0** | Bug 或断裂的特性（声明了但实际不工作） | 1 |
| **P1** | 显著代码重复，影响后续维护 | 5 |
| **P2** | 死代码 / 未使用模型 / 薄壳文件 | 6 |
| **P3** | 轻量优化、文档冗余、风格一致性 | 5 |

合计 17 项。

---

## 1. P0 —— 已声明但未连通的特性

### [P0-1] `ResolvedRuntime.extra_body` 端到端断链 —— **provider-specific 字段从未真正透传**

- **文件**：
  - `evopaw/provider_runtime/models.py:100`（声明 `extra_body: dict`）
  - `evopaw/provider_runtime/registry.py:DEFAULT_PROVIDERS`（dashscope 声明 `extra_body_whitelist=frozenset({"enable_thinking"})`)
  - `evopaw/provider_runtime/resolve.py:219-226`（`resolve_runtime` 构造 `ResolvedRuntime` 时**未填 `extra_body`**）
  - `evopaw/agent_backends/openai_chat.py:_extract_extra_body`、`anthropic_messages.py:_extract_extra_body`（永远拿到空 dict）

- **现状**：
  Plan 文档 §7 / §10 反复强调 `extra_body_whitelist` 是为了防止 hermes #8591 那种"OpenRouter 的 `provider` 字段泄漏到 DashScope 请求"事故。但顺着代码追：
  1. `ProviderSpec` 上声明了白名单。
  2. `resolve_runtime()` **从未把任何 extra 数据放进 `ResolvedRuntime.extra_body`**。
  3. backend 里 `_extract_extra_body(runtime)` 走 `runtime.extra_body or {}`，永远返回 `{}`。
  4. 唯一真正用到 `enable_thinking=False` 的地方（`memory/context_mgmt.py:165`、`memory/indexer.py:165`）是**绕过 backend 直接 hardcode** 的——根本没经过这条管道。

  也就是说：白名单是空跑的、`extra_body_whitelist` 这个字段永远未被消费、设计文档承诺的"防泄漏机制"实际不存在。

- **影响**：
  - 现状：未来接 OpenRouter / Together 这类需要 `provider`、`route` 等 provider-specific 字段的网关时，会发现"没地方填"，然后第二次现场打补丁。
  - 风险：维护者读代码会以为白名单已生效，写新 provider 时不去填 `extra_body`，结果功能不工作。

- **建议**：
  二选一，看后续 6 个月是否要接 OpenRouter：
  - **方案 A（连通）**：在 `RoleConfig` / `roles.{role}` 配置块里增加 `extra_body: dict` 字段；`resolve_runtime` 把"角色级配置 ∪ provider 默认 extra_body"按白名单过滤后写入 `ResolvedRuntime.extra_body`。约 30 行 + 2~3 个单测。
  - **方案 B（删除）**：如果半年内不打算接网关型 provider，删除 `ProviderSpec.extra_body_whitelist` 与 `ResolvedRuntime.extra_body` 字段、删 `_extract_extra_body`，把 memory 层的 `enable_thinking` hardcode 注释清楚是 DashScope 专用。约删 25 行。

  **我的偏向是 A**——因为它和"backend 修复优于绕开模型"的项目偏好一致：未来切 thinking 模型/网关时会再次踩到这个，现在补好一劳永逸。

---

## 2. P1 —— 显著代码重复

### [P1-1] HTTP backend 双胞胎重复 —— `openai_chat.py` 与 `anthropic_messages.py` 大段镜像

- **文件**：`evopaw/agent_backends/openai_chat.py`（371 行）、`anthropic_messages.py`（387 行）

- **重复点**（逐项对照过）：
  | 函数 / 片段 | openai_chat | anthropic_messages | 差异 |
  |-------------|-------------|---------------------|------|
  | `_classify_http_error(status, body)` | ~30 行 | ~30 行 | 无差异 |
  | `_outcome_for(exc)` | ~15 行 | ~15 行 | 无差异 |
  | `_extract_extra_body(runtime)` | ~10 行 | ~10 行 | 无差异 |
  | `_record(provider_id, family, role, outcome)` | 5 行 | 5 行 | 无差异 |
  | `_GENERIC_BODY_FIELDS` frozenset | 同 | 同 | 无差异 |
  | `httpx.AsyncClient` 构造 + `try/except` 异常分发 | ~40 行 | ~40 行 | 仅请求体/url 不同 |

  保守估计 130~150 行属于"换个文件名再写一遍"。

- **建议**：抽 `agent_backends/_http_chat_base.py`：
  ```python
  class _HttpChatBackendBase:
      runtime_family: ClassVar[str]   # 子类覆盖
      _GENERIC_BODY_FIELDS: ClassVar[frozenset[str]]

      @staticmethod
      def _classify_http_error(...): ...
      @staticmethod
      def _outcome_for(exc): ...
      @staticmethod
      def _extract_extra_body(runtime): ...
      @staticmethod
      def _record(provider_id, family, role, outcome): ...

      async def _post_and_handle(self, client, url, json, headers): ...
      # 子类只实现：_build_request_body、_parse_response、_apply_runtime_specific_quirks
  ```
  - 工作量：~2 小时（含单测调整）。
  - 风险：低——两个 backend 已经各有 ~600 行单测覆盖，重构后跑测试即可。
  - 收益：未来加第四个 HTTP 协议族（如 `bedrock_converse`、`codex_responses`）只需写差异部分。

---

### [P1-2] Verbose hooks 双套 —— `build_verbose_hooks` 与 `_build_hooks_from_stream_sink` 同源

- **文件**：
  - `evopaw/agents/hooks.py:build_verbose_hooks`（保留给老路径 / 旧测试）
  - `evopaw/agent_backends/claude_sdk.py:_build_hooks_from_stream_sink`（P2 新路径）

- **现状**：两者最终给飞书推送的字符串是字节级一致的——`f"💭 即将调用工具 {tool_name}"` 与 `f"✅ 工具 {tool_name} 完成"`。区别只在前者直接拿 `FeishuSender`，后者拿 `StreamSink` Protocol。`FeishuStreamSink` 又是对 `FeishuSender` 的薄包装。

- **建议**：
  1. 把 `_build_hooks_from_stream_sink` 移到 `hooks.py`，改名 `build_stream_sink_hooks(sink: StreamSink)`。
  2. `build_verbose_hooks(sender, ...)` 改为内部调用 `build_stream_sink_hooks(FeishuStreamSink(sender, ...))`。
  3. `claude_sdk.py` 直接 import 用，不再 fork 一份。
  - 工作量：~30 分钟。
  - 风险：低——`tests/unit/test_hooks.py` 仍能跑。

---

### [P1-3] 三处 Skill 工具 schema 重复定义

- **文件**：
  - `evopaw/skills_runtime/adapters/claude_mcp.py`：MCP 内联 `{"skill_name": str, "task_context": str}`
  - `evopaw/skills_runtime/adapters/openai_tools.py`：`type: function` envelope + 同一对 properties
  - `evopaw/skills_runtime/adapters/anthropic_tools.py`：`input_schema` envelope + 同一对 properties

- **现状**：三处都在写"`skill_name` 是 string，`task_context` 是 string"，仅外层信封不同。新增字段（比如 P5 plan 里提到过的 `verbose: bool`）需要改三个文件。

- **建议**：在 `skills_runtime/dispatcher.py` 旁加一个 `SKILL_TOOL_SCHEMA` 常量：
  ```python
  SKILL_TOOL_NAME = "skill_loader"
  SKILL_TOOL_DESCRIPTION = "..."
  SKILL_TOOL_PROPERTIES = {
      "skill_name": {"type": "string", "description": "..."},
      "task_context": {"type": "string", "description": "..."},
  }
  SKILL_TOOL_REQUIRED = ["skill_name", "task_context"]
  ```
  三个 adapter 各自包信封，不再重复声明 properties。
  - 工作量：~20 分钟。
  - 风险：极低。

---

### [P1-4] `memory/context_mgmt.py` 与 `memory/indexer.py` 的 `_make_*_client` 重复

- **文件**：
  - `evopaw/memory/context_mgmt.py:_make_summary_client`（line 133）
  - `evopaw/memory/indexer.py:_make_llm_client`（line 88）+ `_make_embed_client`

- **现状**：两个文件都在做"用 `openai.AsyncOpenAI` 直连 DashScope OpenAI 兼容端点"，只是 role 名不同。各自维护一个 `_resolved_*` 模块级 global + 一个 `_*_client` 模块级 global，加起来 6 个全局变量。

- **建议**：在 `evopaw/memory/_dashscope_clients.py` 集中：
  ```python
  def get_dashscope_client(role: Literal["memory_summary", "memory_extract", "memory_embedding"]) -> AsyncOpenAI:
      """惰性单例；按 role 解析 ResolvedRuntime 并构造客户端。"""
  ```
  把 6 个全局收成 1 个 `dict[role, AsyncOpenAI]` 缓存，`_resolved_*` 也合并。
  - 工作量：~45 分钟。
  - 风险：中——这两个文件并发安全比较敏感（`asyncio.Lock` 当前在哪一层加的？）；重构时要保留惰性初始化语义。

---

### [P1-5] 三个 backend 各写一遍 metrics 记录

- **文件**：`agent_backends/openai_chat.py:_record`、`anthropic_messages.py:_record`、`claude_sdk.py:_record_metric`

- **现状**：三处都在做 `record_llm_call(provider_id=..., runtime_family=..., role=..., outcome=...)`，只是命名不一致（前两个叫 `_record`，第三个叫 `_record_metric`）。

- **建议**：
  - 短期：与 [P1-1] 合并解决——抽到 `_HttpChatBackendBase._record`，`claude_sdk.py` 也直接 import。
  - 至少：统一函数名为 `_record`。
  - 工作量：含在 [P1-1] 内。

---

## 3. P2 —— 死代码 / 未使用模型 / 薄壳文件

### [P2-1] `ChatMessage` / `ContentPart` 从未被使用

- **文件**：`evopaw/agent_backends/base.py`（约 50~80 行的 dataclass/Pydantic 定义）+ `agent_backends/__init__.py` re-export

- **现状**：grep 全仓后，`ChatMessage` 和 `ContentPart` 只出现在：
  1. `base.py` 的定义处。
  2. `__init__.py` 的 `__all__` 导出。
  3. 单测 `test_agent_backend_base.py`（仅自测它们的字段，没人在 production 路径调用）。

  实际请求体里走的是 `TurnRequest.messages: str | list[dict]`，三个 backend 都直接处理 `list[dict]`。

- **建议**：删除 `ChatMessage`、`ContentPart` 及对应的 `test_*` 测试。
  - 工作量：~10 分钟。
  - 风险：低——确认无 production 引用即可。
  - 收益：base.py 减少约 80 行，认知负担小一截。

---

### [P2-2] `TurnRequest.tools` 字段（`list[ToolSpec]`）三个 backend 都忽略

- **文件**：`agent_backends/base.py:TurnRequest.tools`

- **现状**：`TurnRequest` 上有 `tools: list[ToolSpec] | None = None` 字段，但：
  - `claude_sdk.py` 从 `mcp_servers` 拿工具，不看 `tools`。
  - `openai_chat.py` 从 `backend_hints["tools"]` 拿（`main_agent.py:199-216` 注入）。
  - `anthropic_messages.py` 同上。

- **建议**：删除 `TurnRequest.tools` 与 `ToolSpec` 类，统一让 `backend_hints["tools"]` 作为唯一通道（这也是当前实际工作方式）。
  - 工作量：~15 分钟。
  - 风险：低。

---

### [P2-3] `evopaw/content_builders/anthropic_blocks.py` 是纯 re-export 壳文件

- **文件**：`evopaw/content_builders/anthropic_blocks.py`（17 行）

- **现状**：
  ```python
  from .claude_blocks import build_image_block, build_user_content
  __all__ = ["build_image_block", "build_user_content"]
  ```
  这就是整个文件的实质内容。Anthropic Messages API 与 Claude SDK 的 content block 格式确实一致，但保留两份"模块名"会让 `pick_content_builder("anthropic_messages")` 看起来有专门实现。

- **建议**：删除 `anthropic_blocks.py`，让 `pick_content_builder` 在 `runtime_family in {"claude_sdk_compat", "anthropic_messages"}` 时都返回 `claude_blocks` 模块。
  - 工作量：~10 分钟。
  - 风险：低。

---

### [P2-4] `tools/add_image_tool_local.py` 的 `load_image_for_claude` 只为旧测试存在

- **文件**：`evopaw/tools/add_image_tool_local.py`（141 行）

- **现状**：P4 重构后 canonical 函数是 `load_image_data(...)`，返回 `(b64, mime)` 元组。`load_image_for_claude` 是薄包装，仅 `tests/unit/test_add_image_tool.py` 的 8 行调用 + `tools/__init__.py` 的 re-export。

- **建议**：把测试改用 `load_image_data`，删除 `load_image_for_claude` 与 re-export。
  - 工作量：~15 分钟。
  - 风险：低。

---

### [P2-5] `tools/skill_loader.py` 是 P3 切分后的薄壳

- **文件**：`evopaw/tools/skill_loader.py`（33 行）

- **现状**：P3 把真正的逻辑搬到 `skills_runtime/`。`skill_loader.py` 现在只是 `from evopaw.skills_runtime.adapters.claude_mcp import build_skill_loader_server` 之类的 re-export，配合 `tests/unit/test_skill_loader.py` 的旧 import 路径。

- **建议**：迁移测试 import 路径到新位置，删除壳文件。
  - 工作量：~20 分钟。
  - 风险：低（test_skill_loader.py 当前规模不大，搜索替换即可）。

---

### [P2-6] `agents/main_agent.py:_build_default_runtime` 实际不可达

- **文件**：`evopaw/agents/main_agent.py:_build_default_runtime`

- **现状**：`build_agent_fn` 入口要求 `main_runtime: ResolvedRuntime`。`main.py` 在启动时强制 resolver 一次。`_build_default_runtime` 是 P1 落地阶段的兜底，目前 production 启动路径都不会进入这个分支（覆盖率单测能跑到，但 main.py 走不到）。

- **建议**：删除 `_build_default_runtime`，把 `build_agent_fn(main_runtime: ResolvedRuntime)` 标记为必填，移除 `main_runtime: ResolvedRuntime | None = None` 的可选签名。
  - 工作量：~15 分钟。
  - 风险：低——但要扫一遍是否有 `tests/integration/` 直接构造 `build_agent_fn` 的地方。

---

## 4. P3 —— 轻量优化 / 文档冗余 / 风格一致性

### [P3-1] `main.py` 同时把 `planner_model: str` 与 `main_runtime: ResolvedRuntime` 传给 `build_agent_fn`

- **文件**：`evopaw/main.py:192-249`、`agents/main_agent.py:build_agent_fn`

- **现状**：`main_runtime.model` 已经包含 `planner_model` 信息，但调用点同时传两份。`build_agent_fn` 内部偏好用 `main_runtime.model`，只在 fallback 时看 `planner_model`。

- **建议**：删 `planner_model` 参数，所有用方走 `main_runtime.model`。同样地，`DEFAULT_PLANNER_MODEL` / `DEFAULT_SUB_AGENT_MODEL`（`llm/claude_client.py`）只剩"默认值"语义，可下沉到 `provider_runtime/registry.py:DEFAULT_PROVIDERS` 的 `default_model`。
  - 工作量：~30 分钟。
  - 风险：中——需要检查所有 `tests/unit/test_main_agent.py`（888 行）的 fixture。

---

### [P3-2] `main_agent.py:199-216` 缺 `else` 分支处理未知 family

- **文件**：`evopaw/agents/main_agent.py:199-216`

- **现状**：
  ```python
  if runtime_family == "claude_sdk_compat":
      backend_hints = {...}
  elif runtime_family == "openai_chat":
      backend_hints = {...}
  elif runtime_family == "anthropic_messages":
      backend_hints = {...}
  # 没有 else，未来加第四个 family 会静默走空 backend_hints
  ```

- **建议**：补 `else: raise ValueError(f"未支持的 runtime_family: {runtime_family}")`。
  - 工作量：~5 分钟。
  - 风险：无。

---

### [P3-3] `_outcome_for(ProviderInvalidRequest)` 与 fallback 返回值相同

- **文件**：`agent_backends/openai_chat.py` / `anthropic_messages.py`

- **现状**：`_outcome_for` 中 `ProviderInvalidRequest` 分支返回 `"invalid_request"`，fallback `else` 也返回 `"invalid_request"`。这把"4xx 客户端错误"和"我们没识别到的异常"折叠成同一个 metric label，未来排查 4xx vs 未知异常会打架。

- **建议**：fallback 改返回 `"unknown_error"`，与已知 4xx/5xx label 区分。
  - 工作量：~5 分钟。
  - 风险：无（仅 metrics 标签变化，需要 Grafana 看板同步）。

---

### [P3-4] backend `runtime_family: str` 类属性未被 `get_backend()` 消费

- **文件**：`agent_backends/__init__.py:get_backend()`、各 backend 类的 `runtime_family: ClassVar[str]`

- **现状**：`get_backend(family)` 用 `if/elif` 字符串匹配硬编码三个分支，没用 `runtime_family` 类属性做反射注册。类属性纯粹自描述。

- **建议**：在 `agent_backends/__init__.py` 维护一个 `_BACKENDS: dict[str, type[AgentBackend]]` 注册表（导入时填充），`get_backend` 走 dict 查表。
  - 工作量：~15 分钟。
  - 风险：低——但要注意惰性导入语义（避免引入 `httpx` 失败时影响 claude_sdk 路径）。

---

### [P3-5] Plan 文档把"P1-P5 落地结果"写进同一份文件 —— `docs/multi-provider-final-plan-2026-04-27.md`

- **文件**：`docs/multi-provider-final-plan-2026-04-27.md`（868 行）

- **现状**：这份文档既是设计提案、又是落地总结、还是历史记录。每个 P 阶段下面都写了"落地结果"小节，把"plan 写了什么"和"实际改了什么"混在一起。后续任何回看 P1 plan 的人，会同时被 P1 落地总结、P2 plan、P2 总结四份内容轰炸。

- **建议**：
  1. 拆出 `docs/archive/multi-provider-rollout-summary-2026-04-27.md`，把 P1-P5 的「落地结果」段落迁过去；
  2. 原文档只留下"plan + 决策"主线；
  3. 头部 frontmatter 加上 `status: completed`，避免读者误以为还在进行中。
  - 工作量：~30 分钟纯编辑。
  - 风险：无。

---

## 5. 不算冗余、但建议关注的事项

### [N-1] memory 层的 `enable_thinking=False` hardcode

- **文件**：`memory/context_mgmt.py:165`、`memory/indexer.py:165`

- **现状**：两处都直接 `extra_body={"enable_thinking": False}` 硬编码，绕开了 [P0-1] 提到的 `extra_body_whitelist` 管道。

- **建议**：等 [P0-1] 方案 A 落地后，删掉这两处 hardcode，让 memory 层也走 `ResolvedRuntime.extra_body`。

---

### [N-2] memory 层模块级 globals 6 个

- **文件**：`memory/context_mgmt.py`、`memory/indexer.py` 共 6 个 `_resolved_*` / `_*_client` 全局

- **现状**：模块级 globals 在惰性初始化模式下需要 `asyncio.Lock` 才能避免竞争，目前未见明显的 lock。在 fork-after-init 或多 event loop 场景容易出 bug。

- **建议**：与 [P1-4] 一并重构。

---

## 6. 优先级建议

按"投入产出 + 风险"排序的修复顺序：

1. **[P0-1] `extra_body` 端到端断链** —— 推荐方案 A（连通），约 30 行代码 + 3 个单测。这是唯一一条「特性已宣传但实际不工作」的，需要尽快闭环。
2. **[P1-1] HTTP backend 抽基类** —— 收益最大（消除 130~150 行重复），单测覆盖充分，风险低。
3. **[P1-2] Verbose hooks 合并** —— 耗时短、风险极低。
4. **[P2-1] [P2-2] 删 ChatMessage / ContentPart / ToolSpec** —— 一起做，约 30 分钟。
5. **[P3-2] 补 main_agent.py 未知 family else** —— 5 分钟，必做。
6. **[P3-5] 拆 plan 文档** —— 30 分钟纯编辑，让后续读者更顺畅。
7. **[P1-3] [P1-4] [P1-5]** —— 时间允许时分别处理，每个不超过 1 小时。
8. **[P2-3]~[P2-6]** —— 集中清理薄壳文件，约 1 小时。
9. **[P3-1] [P3-3] [P3-4]** —— 杂项 polish，可以攒到下个迭代再做。

---

## 7. 单测影响估计

修复以上所有项后，预计需要调整的单测：

| 影响 | 单测文件 | 估计调整 |
|------|----------|----------|
| [P1-1] HTTP base 抽取 | `test_openai_chat_backend.py` (684) / `test_anthropic_messages_backend.py` (566) | 重整 fixture，~1 小时 |
| [P0-1] extra_body 连通 | `test_resolve.py` / 新增 `test_extra_body_passthrough.py` | 新增 ~3 个用例 |
| [P2-1] [P2-2] 删模型 | `test_agent_backend_base.py` | 删除约 10 个用例 |
| [P2-4] [P2-5] | `test_add_image_tool.py` / `test_skill_loader.py` | 改 import 路径 |
| [P3-1] | `test_main_agent.py` (888) | 检查 fixture |

整体看：**没有任何修复需要重写大块测试逻辑**——多 provider 改造的单测覆盖率（4161 LOC across 10 files）已经很扎实，重构主要是抽取共用代码、删除壳层。

---

## 8. 一句话总结

> 多 provider 改造的**架构是对的**，但**落地动作偏保守**：为了不打破老路径，留了一批"老 + 新并存"的双份代码和壳文件；并且在 `extra_body` 这条链路上"建好了管道但没接通水"。建议本次审查产出的 17 项里，**[P0-1] 必须做**，**[P1-1] 强烈建议做**，其余按优先级有空就清，预计总投入 6-8 小时。
