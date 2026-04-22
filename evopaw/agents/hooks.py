"""Agent Hooks — Verbose 模式钩子

Phase 6：PreToolUse/PostToolUse 支持飞书推送回调。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from claude_agent_sdk import HookMatcher

logger = logging.getLogger(__name__)

# callback 类型：async def callback(text: str) -> None
VerboseCallback = Callable[[str], Coroutine[Any, Any, None]]


def build_verbose_hooks(callback: VerboseCallback | None = None) -> dict:
    """构建 verbose 模式的 hooks 字典。

    Args:
        callback: 可选异步回调，用于飞书推送。为 None 时仅打印日志。

    Returns:
        Claude Agent SDK 可用的 hooks 字典。
    """

    async def _pre_tool_use(input_data: dict, tool_use_id: str, context: dict) -> dict:
        tool_name = input_data.get("tool_name", "unknown")
        logger.info("即将调用工具: %s (id=%s)", tool_name, tool_use_id)
        if callback is not None:
            try:
                await callback(f"💭 即将调用工具 {tool_name}")
            except Exception:
                logger.warning("verbose callback 失败 (pre_tool_use, tool=%s)", tool_name, exc_info=True)
        return {}

    async def _post_tool_use(input_data: dict, tool_use_id: str, context: dict) -> dict:
        tool_name = input_data.get("tool_name", "unknown")
        logger.info("工具调用完成: %s (id=%s)", tool_name, tool_use_id)
        if callback is not None:
            try:
                await callback(f"✅ 工具 {tool_name} 完成")
            except Exception:
                logger.warning("verbose callback 失败 (post_tool_use, tool=%s)", tool_name, exc_info=True)
        return {}

    return {
        "PreToolUse": [HookMatcher(matcher=".*", hooks=[_pre_tool_use])],
        "PostToolUse": [HookMatcher(matcher=".*", hooks=[_post_tool_use])],
    }
