"""Agent Hooks / StreamSink —— Verbose 模式事件接入。

`StreamSink` 把 provider backend 的工具事件转成统一的 verbose 推送接口。
`build_stream_sink_hooks()` 负责把 sink 适配到 Claude SDK hooks；旧的
`build_verbose_hooks(callback)` 保留为兼容入口。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from claude_agent_sdk import HookMatcher

from evopaw.agent_backends.base import StreamSink

logger = logging.getLogger(__name__)

# callback 类型：async def callback(text: str) -> None
VerboseCallback = Callable[[str], Coroutine[Any, Any, None]]


# ──────────────────────────────────────────────────────────────────
# FeishuStreamSink —— verbose 推送适配
# ──────────────────────────────────────────────────────────────────


class FeishuStreamSink:
    """把 backend 的 `on_tool_use / on_tool_result` 事件推到飞书会话。

    实现 `evopaw.agent_backends.base.StreamSink` Protocol（runtime_checkable，
    无需显式继承）。文本格式与原 `build_verbose_hooks` 字节级一致：
      - 即将调用：`💭 即将调用工具 {tool_name}`
      - 调用完成：`✅ 工具 {tool_name} 完成`

    构造时接收一个 async 回调 `send(text)`；通常由 `main_agent.py` 闭合
    `sender.send_text(routing_key, text, root_id)`。回调内部异常会被吞掉
    （仅记录 warning），保护主 query 流程。
    """

    def __init__(self, send: VerboseCallback) -> None:
        self._send = send

    async def on_tool_use(self, name: str, input_data: dict) -> None:
        logger.info("即将调用工具: %s", name)
        try:
            await self._send(f"💭 即将调用工具 {name}")
        except Exception:  # noqa: BLE001
            logger.warning(
                "FeishuStreamSink.on_tool_use 推送失败 (tool=%s)", name, exc_info=True,
            )

    async def on_tool_result(self, name: str, output: Any) -> None:
        logger.info("工具调用完成: %s", name)
        try:
            await self._send(f"✅ 工具 {name} 完成")
        except Exception:  # noqa: BLE001
            logger.warning(
                "FeishuStreamSink.on_tool_result 推送失败 (tool=%s)", name, exc_info=True,
            )


# ──────────────────────────────────────────────────────────────────
# CompositeStreamSink —— 把多个 sink 组合成一个（fan-out + 错误隔离）
# ──────────────────────────────────────────────────────────────────


class CompositeStreamSink:
    """把同一事件 fan-out 给一组下游 StreamSink；单 sink 异常不影响其它。

    实现 `evopaw.agent_backends.base.StreamSink` Protocol（runtime_checkable，
    无需显式继承）。空列表也是合法构造，等价于 no-op sink。
    """

    def __init__(self, sinks: list[StreamSink] | None = None) -> None:
        self._sinks: list[StreamSink] = list(sinks) if sinks else []

    async def on_tool_use(self, name: str, input_data: dict) -> None:
        for sink in self._sinks:
            try:
                await sink.on_tool_use(name, input_data)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "CompositeStreamSink: %s on_tool_use 失败 (tool=%s)",
                    type(sink).__name__, name, exc_info=True,
                )

    async def on_tool_result(self, name: str, output: Any) -> None:
        for sink in self._sinks:
            try:
                await sink.on_tool_result(name, output)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "CompositeStreamSink: %s on_tool_result 失败 (tool=%s)",
                    type(sink).__name__, name, exc_info=True,
                )


# ──────────────────────────────────────────────────────────────────
# build_stream_sink_hooks —— SDK hooks 构造器
# ──────────────────────────────────────────────────────────────────


def build_stream_sink_hooks(stream_sink: StreamSink | None) -> dict:
    """把 StreamSink 适配为 Claude SDK 的 PreToolUse / PostToolUse hooks。

    这是 SDK hooks 的唯一来源，`claude_sdk` backend 与旧 `build_verbose_hooks`
    都复用本函数，避免两份钩子逻辑漂移。

    行为：
    - `tool_name` 字段从 `input_data.get("tool_name", "unknown")` 取。
    - PreToolUse 调用 `on_tool_use(name, tool_input)`；
      PostToolUse 调用 `on_tool_result(name, tool_response)`。
    - sink 内部异常一律吞掉（防止主 query 流程被 hook 破坏）。
    - sink=None 直接返回 `{}`，等价于"未启用 verbose"。
    """
    if stream_sink is None:
        return {}

    async def _pre_tool_use(input_data: dict, tool_use_id: str, context: dict) -> dict:
        tool_name = input_data.get("tool_name", "unknown")
        logger.info("即将调用工具: %s (id=%s)", tool_name, tool_use_id)
        try:
            await stream_sink.on_tool_use(tool_name, input_data.get("tool_input", {}))
        except Exception:  # noqa: BLE001
            logger.warning(
                "stream_sink.on_tool_use 失败 (tool=%s)", tool_name, exc_info=True,
            )
        return {}

    async def _post_tool_use(input_data: dict, tool_use_id: str, context: dict) -> dict:
        tool_name = input_data.get("tool_name", "unknown")
        logger.info("工具调用完成: %s (id=%s)", tool_name, tool_use_id)
        try:
            await stream_sink.on_tool_result(tool_name, input_data.get("tool_response"))
        except Exception:  # noqa: BLE001
            logger.warning(
                "stream_sink.on_tool_result 失败 (tool=%s)", tool_name, exc_info=True,
            )
        return {}

    return {
        "PreToolUse": [HookMatcher(matcher=".*", hooks=[_pre_tool_use])],
        "PostToolUse": [HookMatcher(matcher=".*", hooks=[_post_tool_use])],
    }


# ──────────────────────────────────────────────────────────────────
# build_verbose_hooks —— callback 形态兼容入口
# ──────────────────────────────────────────────────────────────────


class _CallbackSink:
    """把单参数 async callback 适配为 StreamSink 协议。

    callback=None 时所有事件都 no-op（便于旧测试 `test_callback_none` 仍能拿到
    可调用 hook 闭包，仅检查日志输出）。
    """

    def __init__(self, callback: VerboseCallback | None) -> None:
        self._callback = callback

    async def on_tool_use(self, name: str, input_data: dict) -> None:  # noqa: ARG002
        if self._callback is None:
            return
        await self._callback(f"💭 即将调用工具 {name}")

    async def on_tool_result(self, name: str, output: Any) -> None:  # noqa: ARG002
        if self._callback is None:
            return
        await self._callback(f"✅ 工具 {name} 完成")


def build_verbose_hooks(callback: VerboseCallback | None = None) -> dict:
    """构建 verbose 模式的 SDK hooks 字典（旧 API）。

    Args:
        callback: 可选异步回调；为 None 时仅打印日志。

    Returns:
        Claude Agent SDK 可用的 hooks 字典（即使 callback=None 也返回有效 hooks，
        以保证旧测试期望的 hook 闭包仍可被取出执行）。
    """
    return build_stream_sink_hooks(_CallbackSink(callback))
