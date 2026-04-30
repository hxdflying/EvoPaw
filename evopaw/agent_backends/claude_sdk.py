"""ClaudeSDKCompatBackend —— P2 唯一一个真正实现的 backend。

把现有 `main_agent.py:191-218` 的 query() 主循环 + skills_called 收集 + 异常归一化
整段搬过来，只改三处：

1. 模型名从 `req.runtime.model` 取，而不是闭包变量 `planner_model`。
2. verbose hooks 不再由 `main_agent.py` 直接构造，而是把 `req.stream_sink` 在 backend
   内部转成 PreToolUse / PostToolUse 字典（行为字节级一致：同样的 `{tool_name}` 字段
   驱动同样的回调）。
3. 异常归一化为 `ProviderTransientError`（CLINotFoundError / CLIConnectionError）和
   `ProviderUnknownError`（其它），上层 `main_agent.agent_fn` 捕获后映射为友好文本。

`backend_hints` 通道用于把
`evopaw.skills_runtime.adapters.claude_mcp.build_skill_loader_server(...)`
的产物（一个 SDK MCP server 对象）从 main_agent.py 透传过来；其它 backend 不会读取。
"""

from __future__ import annotations

import logging
import time
from typing import Any

# claude_sdk 是 P2 的硬依赖（仅当 runtime_family=='claude_sdk_compat' 时才会
# 加载本模块；其它 backend 的部署不会触发本 import）。模块级 import 让单测
# 可以直接 patch `evopaw.agent_backends.claude_sdk.query` 等符号。
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLINotFoundError,
    ResultMessage,
    ToolUseBlock,
    query,
)

from evopaw.agents.hooks import build_stream_sink_hooks
from evopaw.llm.claude_client import build_main_agent_options
from evopaw.observability.metrics import record_llm_call

from .base import (
    AgentBackend,
    ProviderTransientError,
    ProviderUnknownError,
    ToolCall,
    TurnRequest,
    TurnResult,
    Usage,
)

logger = logging.getLogger(__name__)


# 老符号别名：测试 `tests/unit/test_claude_sdk_backend.py` 仍 import
# `_build_hooks_from_stream_sink`，保留以避免迁移测试导入路径；新代码请用
# `evopaw.agents.hooks.build_stream_sink_hooks`。
_build_hooks_from_stream_sink = build_stream_sink_hooks


def _extract_skill_name(block: Any) -> str | None:
    """从 ToolUseBlock 中提取 skill_loader 的 skill_name；非 skill_loader 调用返回 None。"""
    name = getattr(block, "name", "") or ""
    if not name.endswith("skill_loader"):
        return None
    block_input = getattr(block, "input", {}) or {}
    skill_name = block_input.get("skill_name", "") if isinstance(block_input, dict) else ""
    return skill_name or None


def _extract_usage(message: Any) -> Usage:
    """从 ResultMessage 提取 usage（如有）。Claude SDK 的 ResultMessage 是一个
    数据类，目前不一定带 usage 字段；缺失时返回零值 Usage。"""
    raw = getattr(message, "usage", None)
    if not isinstance(raw, dict):
        return Usage()
    return Usage(
        prompt_tokens=int(raw.get("input_tokens") or raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("output_tokens") or raw.get("completion_tokens") or 0),
        total_tokens=int(raw.get("total_tokens") or 0),
    )


class ClaudeSDKCompatBackend(AgentBackend):
    """完全包装现有 `query()` 调用路径的 backend，行为零变化。"""

    runtime_family: str = "claude_sdk_compat"

    async def run_turn(self, req: TurnRequest) -> TurnResult:
        hooks = build_stream_sink_hooks(req.stream_sink)
        mcp_servers = req.backend_hints.get("mcp_servers") or {}

        options = build_main_agent_options(
            system_prompt=req.system_prompt,
            cwd=req.cwd,
            model=req.runtime.model,
            max_turns=req.max_turns,
            hooks=hooks,
            mcp_servers=mcp_servers,
        )

        started_at = time.monotonic()
        outcome = "success"
        final_text = ""
        skills_called: list[str] = []
        tool_calls: list[ToolCall] = []
        usage = Usage()
        raw_extra: dict[str, Any] = {}

        try:
            async for message in query(prompt=req.user_content, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            input_dict = (
                                block.input if isinstance(block.input, dict) else {}
                            )
                            tool_calls.append(
                                ToolCall(name=block.name, input=input_dict)
                            )
                            skill_name = _extract_skill_name(block)
                            if skill_name:
                                skills_called.append(skill_name)
                if isinstance(message, ResultMessage):
                    final_text = message.result or ""
                    usage = _extract_usage(message)
                    raw_extra["result_message_repr"] = repr(message)
        except (CLINotFoundError, CLIConnectionError) as exc:
            outcome = "transient"
            logger.error("Claude SDK transient error: %s", exc)
            self._record_metric(req, outcome, started_at, usage)
            raise ProviderTransientError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            outcome = "error"
            logger.exception("ClaudeSDKCompatBackend unexpected error")
            self._record_metric(req, outcome, started_at, usage)
            raise ProviderUnknownError(str(exc)) from exc

        self._record_metric(req, outcome, started_at, usage)

        return TurnResult(
            text=final_text,
            tool_calls=tool_calls,
            skills_called=skills_called,
            usage=usage,
            raw=raw_extra,
        )

    @staticmethod
    def _record_metric(
        req: TurnRequest,
        outcome: str,
        started_at: float,
        usage: Usage,
    ) -> None:
        """打 metrics（P1 已在 metrics.py 声明 record_llm_call）。"""
        try:
            record_llm_call(
                provider_id=req.runtime.provider_id,
                runtime_family=req.runtime.runtime_family,
                role=req.role,
                outcome=outcome,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                latency_seconds=time.monotonic() - started_at,
            )
        except Exception:  # noqa: BLE001
            logger.warning("record_llm_call failed", exc_info=True)
