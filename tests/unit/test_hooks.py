"""hooks 单元测试"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from evopaw.agents.hooks import CompositeStreamSink, build_verbose_hooks


class TestBuildVerboseHooksStructure:
    def test_returns_dict_with_expected_keys(self):
        hooks = build_verbose_hooks()
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks

    def test_each_key_has_one_matcher(self):
        hooks = build_verbose_hooks()
        assert len(hooks["PreToolUse"]) == 1
        assert len(hooks["PostToolUse"]) == 1


class TestPreToolUseCallback:
    @pytest.mark.asyncio
    async def test_calls_callback_with_tool_name(self):
        cb = AsyncMock()
        hooks = build_verbose_hooks(callback=cb)
        hook_fn = hooks["PreToolUse"][0].hooks[0]
        await hook_fn({"tool_name": "Bash"}, "id_1", {})
        cb.assert_called_once_with("💭 即将调用工具 Bash")

    @pytest.mark.asyncio
    async def test_returns_empty_dict(self):
        cb = AsyncMock()
        hooks = build_verbose_hooks(callback=cb)
        hook_fn = hooks["PreToolUse"][0].hooks[0]
        result = await hook_fn({"tool_name": "Read"}, "id_2", {})
        assert result == {}


class TestPostToolUseCallback:
    @pytest.mark.asyncio
    async def test_calls_callback_with_tool_name(self):
        cb = AsyncMock()
        hooks = build_verbose_hooks(callback=cb)
        hook_fn = hooks["PostToolUse"][0].hooks[0]
        await hook_fn({"tool_name": "Bash"}, "id_1", {})
        cb.assert_called_once_with("✅ 工具 Bash 完成")

    @pytest.mark.asyncio
    async def test_returns_empty_dict(self):
        cb = AsyncMock()
        hooks = build_verbose_hooks(callback=cb)
        hook_fn = hooks["PostToolUse"][0].hooks[0]
        result = await hook_fn({"tool_name": "Read"}, "id_2", {})
        assert result == {}


class TestCallbackNone:
    @pytest.mark.asyncio
    async def test_pre_tool_use_only_logs(self, caplog):
        hooks = build_verbose_hooks(callback=None)
        hook_fn = hooks["PreToolUse"][0].hooks[0]
        with caplog.at_level(logging.INFO, logger="evopaw.agents.hooks"):
            result = await hook_fn({"tool_name": "Glob"}, "id_3", {})
        assert result == {}
        assert "即将调用工具: Glob" in caplog.text

    @pytest.mark.asyncio
    async def test_post_tool_use_only_logs(self, caplog):
        hooks = build_verbose_hooks(callback=None)
        hook_fn = hooks["PostToolUse"][0].hooks[0]
        with caplog.at_level(logging.INFO, logger="evopaw.agents.hooks"):
            result = await hook_fn({"tool_name": "Glob"}, "id_3", {})
        assert result == {}
        assert "工具调用完成: Glob" in caplog.text


class TestCallbackExceptionSwallowed:
    @pytest.mark.asyncio
    async def test_pre_tool_use_swallows_exception(self, caplog):
        cb = AsyncMock(side_effect=RuntimeError("send failed"))
        hooks = build_verbose_hooks(callback=cb)
        hook_fn = hooks["PreToolUse"][0].hooks[0]
        with caplog.at_level(logging.WARNING, logger="evopaw.agents.hooks"):
            result = await hook_fn({"tool_name": "Edit"}, "id_4", {})
        assert result == {}
        assert "stream_sink.on_tool_use 失败" in caplog.text

    @pytest.mark.asyncio
    async def test_post_tool_use_swallows_exception(self, caplog):
        cb = AsyncMock(side_effect=RuntimeError("send failed"))
        hooks = build_verbose_hooks(callback=cb)
        hook_fn = hooks["PostToolUse"][0].hooks[0]
        with caplog.at_level(logging.WARNING, logger="evopaw.agents.hooks"):
            result = await hook_fn({"tool_name": "Edit"}, "id_4", {})
        assert result == {}
        assert "stream_sink.on_tool_result 失败" in caplog.text


# ──────────────────────────────────────────────────────────────────
# CompositeStreamSink
# ──────────────────────────────────────────────────────────────────


class _RecordingSink:
    """记录 on_tool_use / on_tool_result 调用顺序与参数。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, object]] = []

    async def on_tool_use(self, name: str, input_data: dict) -> None:
        self.events.append(("use", name, input_data))

    async def on_tool_result(self, name: str, output) -> None:
        self.events.append(("result", name, output))


class _RaisingSink:
    """每次调用都抛错；用于验证 Composite 的错误隔离。"""

    async def on_tool_use(self, name: str, input_data: dict) -> None:  # noqa: ARG002
        raise RuntimeError("boom on_tool_use")

    async def on_tool_result(self, name: str, output) -> None:  # noqa: ARG002
        raise RuntimeError("boom on_tool_result")


class TestCompositeStreamSink:
    @pytest.mark.asyncio
    async def test_empty_sinks_is_noop(self):
        composite = CompositeStreamSink()
        # 不应抛错
        await composite.on_tool_use("Bash", {"a": 1})
        await composite.on_tool_result("Bash", "out")

    @pytest.mark.asyncio
    async def test_fanout_to_all_sinks(self):
        s1 = _RecordingSink()
        s2 = _RecordingSink()
        composite = CompositeStreamSink([s1, s2])
        await composite.on_tool_use("Read", {"path": "a"})
        await composite.on_tool_result("Read", "ok")
        assert s1.events == [("use", "Read", {"path": "a"}), ("result", "Read", "ok")]
        assert s2.events == [("use", "Read", {"path": "a"}), ("result", "Read", "ok")]

    @pytest.mark.asyncio
    async def test_one_sink_failure_does_not_block_others(self, caplog):
        good = _RecordingSink()
        bad = _RaisingSink()
        composite = CompositeStreamSink([bad, good])
        with caplog.at_level(logging.WARNING, logger="evopaw.agents.hooks"):
            await composite.on_tool_use("Edit", {"x": 1})
            await composite.on_tool_result("Edit", "y")
        # 失败的 sink 异常被吞掉，good sink 仍收到两个事件
        assert good.events == [("use", "Edit", {"x": 1}), ("result", "Edit", "y")]
        assert "_RaisingSink on_tool_use 失败" in caplog.text
        assert "_RaisingSink on_tool_result 失败" in caplog.text

    @pytest.mark.asyncio
    async def test_constructor_copies_list(self):
        s1 = _RecordingSink()
        sinks = [s1]
        composite = CompositeStreamSink(sinks)
        sinks.append(_RaisingSink())  # 之后改外部列表不应影响 composite
        await composite.on_tool_use("X", {})
        assert s1.events == [("use", "X", {})]

    def test_satisfies_stream_sink_protocol(self):
        """CompositeStreamSink 必须满足 runtime_checkable StreamSink 协议，
        以便能直接传给 build_stream_sink_hooks / FeishuStreamSink 同位置。"""
        from evopaw.agent_backends.base import StreamSink
        composite = CompositeStreamSink()
        assert isinstance(composite, StreamSink)
