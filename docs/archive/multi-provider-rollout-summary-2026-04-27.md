---
status: completed
completed_at: 2026-04-28
source_plan: docs/multi-provider-final-plan-2026-04-27.md
---

# 多 Provider 改造落地总结（P1–P5）

> 本文是 [`docs/multi-provider-final-plan-2026-04-27.md`](../multi-provider-final-plan-2026-04-27.md)
> 的「实际落地结果」摘要。原计划文档保留所有设计动机、决策、验收与回滚说明；
> 这里只列「最终改了什么 / 在哪验证 / 单测基线变化」，方便事后翻账。
>
> 详细落地段落仍以原文为准；本文给出锚点索引以避免内容重复维护。

## 阶段总览

| 阶段 | 完成日期 | 主要交付 | 单测基线 | 详见 |
|---|---|---|---|---|
| P1 | 2026-04-28 | provider_runtime + 角色 resolver + memory 层接入 + observability 标签 | 638 → 683 | [§9 落地段落](../multi-provider-final-plan-2026-04-27.md#L482) |
| P2 | 2026-04-28 | agent_backends 协议层 + ClaudeSDKCompatBackend + main_agent 解耦 | 683 → 745 | [§10 落地段落](../multi-provider-final-plan-2026-04-27.md#L558) |
| P3 | 2026-04-28 | skills_runtime 拆出 + OpenAIChatBackend（DashScope/OpenRouter 等） | 745 → 801 | [§11 落地段落](../multi-provider-final-plan-2026-04-27.md#L647) |
| P4 | 2026-04-28 | content_builders + AnthropicMessagesBackend + 多模态三族对齐 | 801 → 863 | [§12](../multi-provider-final-plan-2026-04-27.md#L678) |
| P5 | 2026-04-28 | skill_loader 兼容矩阵 + 三族 e2e 测试守护 | 863（业务无新增） | [§13](../multi-provider-final-plan-2026-04-27.md#L774) |

## 模块落地速查

| 模块 | 引入阶段 | 角色 |
|---|---|---|
| `evopaw/provider_runtime/` | P1 | ProviderSpec / RoleConfig / resolve_runtime |
| `evopaw/agent_backends/{base,claude_sdk,openai_chat,anthropic_messages,_http_chat_base}.py` | P2–P4 | 三族 backend + 共享 HTTP 基类 |
| `evopaw/skills_runtime/{dispatcher,registry,instructions,tool_schema}.py` + `adapters/` | P3 | 单一 SkillDispatcher + 三种 schema adapter |
| `evopaw/content_builders/{claude_blocks,openai_blocks}.py` | P4 | 多模态 wire 形态分流（Anthropic 与 Claude 共享 claude_blocks） |
| `evopaw/observability/metrics.py::record_llm_call` | P1 | provider_id / runtime_family / role / outcome 标签 |
| `evopaw/memory/_dashscope_clients.py` | P1（refactor 在 code-review-multi-provider 后） | 摘要 + 抽取 + 向量化的统一 OpenAI 兼容 client |

## 不变量（回归基线）

P5 之后这些事实不再可变（破坏即应回滚或修正）：

- `evopaw/agents/main_agent.py` 内 `import claude_agent_sdk` 出现 0 次（grep 守护）
- 三族主 runtime 共享同一份 `SkillDispatcher` 业务逻辑（dispatcher 内零 family 分支）
- 凭证从不进入 LLM context；多模态附件读盘统一走 `tools/add_image_tool_local.load_image_data`
- `record_llm_call` 在三族成功 / 失败两路均会被调用一次（HTTP 路径在 `_HttpChatBackendBase._record`）

## 后续清理

落地完成后又做了一次深度审查，输出在 [`docs/code-review-multi-provider-2026-04-28.md`](../code-review-multi-provider-2026-04-28.md)。
该报告 17 项 finding（P0×1 / P1×5 / P2×6 / P3×5 / N×2）已在 2026-04-28 后陆续修复，
进一步收敛了 backend / memory / content_builder 三处的代码重复。详见各 finding 的 commit 关联。
