"""hooks 单元测试"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from evopaw.agents.hooks import build_verbose_hooks


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
        assert "verbose callback 失败" in caplog.text

    @pytest.mark.asyncio
    async def test_post_tool_use_swallows_exception(self, caplog):
        cb = AsyncMock(side_effect=RuntimeError("send failed"))
        hooks = build_verbose_hooks(callback=cb)
        hook_fn = hooks["PostToolUse"][0].hooks[0]
        with caplog.at_level(logging.WARNING, logger="evopaw.agents.hooks"):
            result = await hook_fn({"tool_name": "Edit"}, "id_4", {})
        assert result == {}
        assert "verbose callback 失败" in caplog.text
