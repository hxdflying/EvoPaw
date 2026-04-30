"""main_agent 单元测试

P2 改造后：本文件不再 import `claude_agent_sdk`，所有测试 patch
`evopaw.agents.main_agent.get_backend` 注入一个 FakeBackend，验证
`build_agent_fn` 的输入装配（system prompt / user_content / cwd /
stream_sink / backend_hints）与输出消化（skills_called / 持久化 /
async_index_turn）行为。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from evopaw.agent_backends import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderMaxTurnsExceeded,
    ProviderRateLimited,
    ProviderTransientError,
    ProviderUnknownError,
    TurnRequest,
    TurnResult,
    Usage,
)
from evopaw.agents.main_agent import (
    _format_ctx_summaries,
    _format_history,
    build_agent_fn as _build_agent_fn_orig,
)
from evopaw.provider_runtime import ResolvedRuntime
from evopaw.session.models import MessageEntry


# 默认 ResolvedRuntime（claude_sdk_compat 等价于改造前的隐式默认）。
# 测试 patch 了 get_backend，所以 model 名只是占位，不会真发请求。
_DEFAULT_MAIN_RUNTIME = ResolvedRuntime(
    role="main",
    provider_id="claude_sdk",
    runtime_family="claude_sdk_compat",
    model="claude-sonnet-4-6",
)
_DEFAULT_SUB_RUNTIME = ResolvedRuntime(
    role="subagent",
    provider_id="claude_sdk",
    runtime_family="claude_sdk_compat",
    model="claude-haiku-4-5",
)


def build_agent_fn(*args, **kwargs):
    """test wrapper: 没显式传 main_runtime/sub_runtime 时填默认值，避免每个测试 case 重写。"""
    kwargs.setdefault("main_runtime", _DEFAULT_MAIN_RUNTIME)
    kwargs.setdefault("sub_runtime", _DEFAULT_SUB_RUNTIME)
    return _build_agent_fn_orig(*args, **kwargs)


# ──────────────────────────────────────────────────────────────────
# 测试夹具：FakeBackend
# ──────────────────────────────────────────────────────────────────


class FakeBackend:
    """记录每次 run_turn 的 TurnRequest，并按预设返回 TurnResult 或抛异常。"""

    def __init__(
        self,
        result: TurnResult | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._result = result if result is not None else TurnResult(text="ok")
        self._raise_exc = raise_exc
        self.calls: list[TurnRequest] = []

    async def run_turn(self, req: TurnRequest) -> TurnResult:
        self.calls.append(req)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result


def _patch_backend(fake: FakeBackend):
    """patch get_backend 让它无视 runtime，永远返回 fake。"""
    return patch("evopaw.agents.main_agent.get_backend", return_value=fake)


# ──────────────────────────────────────────────────────────────────
# _format_history（保持原有覆盖）
# ──────────────────────────────────────────────────────────────────


class TestFormatHistory:
    def test_empty_history_returns_placeholder(self):
        assert _format_history([]) == "（无历史记录）"

    def test_single_user_message(self):
        history = [MessageEntry(role="user", content="你好", ts=1000)]
        result = _format_history(history)
        assert "用户" in result
        assert "你好" in result

    def test_single_assistant_message(self):
        history = [MessageEntry(role="assistant", content="我很好", ts=1000)]
        result = _format_history(history)
        assert "助手" in result
        assert "我很好" in result

    def test_multiple_messages_order(self):
        history = [
            MessageEntry(role="user", content="question", ts=1000),
            MessageEntry(role="assistant", content="answer", ts=2000),
        ]
        result = _format_history(history)
        assert "用户: question" in result
        assert "助手: answer" in result
        assert result.index("用户") < result.index("助手")

    def test_max_turns_truncates_old_messages(self):
        history = [
            MessageEntry(role="user", content=f"msg{i}", ts=i * 1000)
            for i in range(10)
        ]
        result = _format_history(history, max_turns=4)
        assert "msg9" in result
        assert "msg6" in result
        assert "msg0" not in result
        assert "msg5" not in result

    def test_truncated_history_adds_note(self):
        history = [
            MessageEntry(role="user", content=f"msg{i}", ts=i * 1000)
            for i in range(10)
        ]
        result = _format_history(history, max_turns=4)
        assert "历史" in result
        assert "history_reader" in result

    def test_max_turns_exact_boundary_no_note(self):
        history = [
            MessageEntry(role="user", content="q", ts=1000),
            MessageEntry(role="assistant", content="a", ts=2000),
        ]
        result = _format_history(history, max_turns=2)
        assert "history_reader" not in result

    def test_default_max_turns_is_20(self):
        history = [
            MessageEntry(role="user", content=f"m{i}", ts=i * 1000)
            for i in range(19)
        ]
        result = _format_history(history)
        assert "m0" in result
        assert "history_reader" not in result


# ──────────────────────────────────────────────────────────────────
# build_agent_fn —— 主入口装配
# ──────────────────────────────────────────────────────────────────


class TestBuildAgentFn:
    def test_returns_callable(self, tmp_path):
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, db_dsn="")
        assert callable(fn)

    @pytest.mark.asyncio
    async def test_calls_bootstrap_prompt(self, tmp_path):
        """build_bootstrap_prompt 被调用一次，结果作为 TurnRequest.system_prompt 前缀"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(result=TurnResult(text="Claude 回复"))

        with _patch_backend(fake), \
             patch(
                 "evopaw.agents.main_agent.build_bootstrap_prompt",
                 return_value="<soul>test</soul>",
             ) as mock_bp:
            await fn("你好", [], "sid_001")

        mock_bp.assert_called_once_with(ws)
        assert len(fake.calls) == 1
        assert fake.calls[0].system_prompt.startswith("<soul>test</soul>")
        # 主 Agent 的 tool_constraint 注入也保留
        assert "<tool_constraint>" in fake.calls[0].system_prompt

    @pytest.mark.asyncio
    async def test_cwd_points_to_session_dir(self, tmp_path):
        """TurnRequest.cwd 指向 {workspace_dir}/sessions/{session_id}/"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_123")

        assert fake.calls[0].cwd == str(ws / "sessions" / "sid_123")

    @pytest.mark.asyncio
    async def test_verbose_p2p_sets_stream_sink(self, tmp_path):
        """verbose=True + p2p routing_key → TurnRequest.stream_sink 非 None"""
        sender = MagicMock()
        sender.send_text = AsyncMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_p2p", routing_key="p2p:user1", verbose=True)

        assert fake.calls[0].stream_sink is not None

    @pytest.mark.asyncio
    async def test_verbose_group_sets_stream_sink(self, tmp_path):
        """verbose=True + group routing_key → stream_sink 非 None"""
        sender = MagicMock()
        sender.send_text = AsyncMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_grp", routing_key="group:chat1", verbose=True)

        assert fake.calls[0].stream_sink is not None

    @pytest.mark.asyncio
    async def test_verbose_thread_no_stream_sink(self, tmp_path):
        """verbose=True + thread routing_key → stream_sink 仍为 None（不推送飞书）"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_thr", routing_key="thread:chat1:thr1", verbose=True)

        assert fake.calls[0].stream_sink is None

    @pytest.mark.asyncio
    async def test_non_verbose_no_stream_sink(self, tmp_path):
        """verbose=False → stream_sink 为 None"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_nv", routing_key="p2p:u1", verbose=False)

        assert fake.calls[0].stream_sink is None

    @pytest.mark.asyncio
    async def test_backend_hints_carries_skill_loader(self, tmp_path):
        """skill_loader server 通过 backend_hints['mcp_servers']['evopaw'] 透传"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch(
                 "evopaw.agents.main_agent.build_skill_loader_server",
                 return_value="<sentinel-mcp-server>",
             ):
            await fn("hi", [], "sid_001")

        mcp = fake.calls[0].backend_hints.get("mcp_servers", {})
        assert mcp.get("evopaw") == "<sentinel-mcp-server>"

    @pytest.mark.asyncio
    async def test_runtime_passed_to_request(self, tmp_path):
        """显式 main_runtime 入参会被透传到 TurnRequest.runtime"""
        from evopaw.provider_runtime import ResolvedRuntime

        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        runtime = ResolvedRuntime(
            role="main",
            provider_id="claude_sdk",
            runtime_family="claude_sdk_compat",
            model="claude-sonnet-4-6-explicit",
        )
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime,
        )
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_rt")

        assert fake.calls[0].runtime is runtime
        assert fake.calls[0].runtime.model == "claude-sonnet-4-6-explicit"

    @pytest.mark.asyncio
    async def test_transient_error_returns_friendly_message(self, tmp_path):
        """ProviderTransientError 返回「调用失败」文本"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(raise_exc=ProviderTransientError("connection refused"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            result = await fn("hi", [], "sid_err")

        assert "调用失败" in result
        assert "connection refused" in result

    @pytest.mark.asyncio
    async def test_unknown_error_returns_friendly_message(self, tmp_path):
        """ProviderUnknownError 返回「内部错误」文本"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(raise_exc=ProviderUnknownError("boom"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            result = await fn("hi", [], "sid_boom")

        assert "内部错误" in result

    @pytest.mark.asyncio
    async def test_unexpected_runtime_error_returns_friendly_message(self, tmp_path):
        """非归一化的意外异常也归内部错误"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(raise_exc=RuntimeError("unexpected"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            result = await fn("hi", [], "sid_unex")

        assert "内部错误" in result

    @pytest.mark.asyncio
    async def test_auth_error_returns_credential_message(self, tmp_path):
        """ProviderAuthError → 提示凭证错误且包含 provider_id"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(raise_exc=ProviderAuthError("invalid api key"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            result = await fn("hi", [], "sid_auth")

        assert "凭证" in result or "未授权" in result
        assert "claude_sdk" in result  # 默认 main_runtime 的 provider_id

    @pytest.mark.asyncio
    async def test_rate_limited_returns_throttle_message(self, tmp_path):
        """ProviderRateLimited → 提示被限流"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(raise_exc=ProviderRateLimited("429 too many"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            result = await fn("hi", [], "sid_rl")

        assert "限流" in result
        assert "claude_sdk" in result

    @pytest.mark.asyncio
    async def test_invalid_request_returns_rejected_message(self, tmp_path):
        """ProviderInvalidRequest → 提示请求被拒绝并带原因"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(raise_exc=ProviderInvalidRequest("bad model field"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            result = await fn("hi", [], "sid_inv")

        assert "拒绝" in result
        assert "bad model field" in result

    @pytest.mark.asyncio
    async def test_max_turns_exceeded_returns_loop_message(self, tmp_path):
        """ProviderMaxTurnsExceeded → 提示工具调用轮次达上限并报当前 max_turns"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx, agent_max_turns=7,
        )
        fake = FakeBackend(raise_exc=ProviderMaxTurnsExceeded("loop never converged"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            result = await fn("hi", [], "sid_loop")

        assert "max_turns=7" in result
        assert "工具调用" in result

    @pytest.mark.asyncio
    async def test_history_included_in_prompt(self, tmp_path):
        """TurnRequest.user_content 中包含历史与当前用户消息"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(result=TurnResult(text="reply"))

        history = [
            MessageEntry(role="user", content="prev question", ts=1000),
            MessageEntry(role="assistant", content="prev answer", ts=2000),
        ]

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("new question", history, "sid_hist")

        text = fake.calls[0].user_content
        assert isinstance(text, str)
        assert "prev question" in text
        assert "prev answer" in text
        assert "new question" in text


# ──────────────────────────────────────────────────────────────────
# 三族 runtime_family：backend_hints / content_builder 选择
# ──────────────────────────────────────────────────────────────────


class TestRuntimeFamilyDispatch:
    """P4 验收：claude_sdk_compat / openai_chat / anthropic_messages 三族
    应分别走不同的 backend_hints + content_builder 路径。"""

    @pytest.mark.asyncio
    async def test_anthropic_messages_uses_skill_dispatcher(self, tmp_path):
        from evopaw.provider_runtime import ResolvedRuntime

        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        runtime = ResolvedRuntime(
            role="main",
            provider_id="anthropic",
            runtime_family="anthropic_messages",
            model="claude-sonnet-4-6",
            api_base="https://api.anthropic.com",
            api_key="sk-ant-x",
        )
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_anth")

        hints = fake.calls[0].backend_hints
        assert "skill_dispatcher" in hints
        # anthropic_messages 不走 SDK MCP 路径
        assert "mcp_servers" not in hints

    @pytest.mark.asyncio
    async def test_openai_chat_uses_skill_dispatcher(self, tmp_path):
        from evopaw.provider_runtime import ResolvedRuntime

        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        runtime = ResolvedRuntime(
            role="main",
            provider_id="openai",
            runtime_family="openai_chat",
            model="gpt-4o",
            api_base="https://api.openai.com",
            api_key="sk-x",
        )
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_oai")

        hints = fake.calls[0].backend_hints
        assert "skill_dispatcher" in hints
        assert "mcp_servers" not in hints

    @pytest.mark.asyncio
    async def test_claude_sdk_compat_uses_mcp_servers(self, tmp_path):
        # claude_sdk_compat 仍然走 SDK MCP 路径（不构造 SkillDispatcher）
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch(
                 "evopaw.agents.main_agent.build_skill_loader_server",
                 return_value="<sentinel>",
             ):
            await fn("hi", [], "sid_sdk")

        hints = fake.calls[0].backend_hints
        assert "mcp_servers" in hints
        assert "skill_dispatcher" not in hints

    @pytest.mark.asyncio
    async def test_http_backend_dispatcher_has_bg_result_callback(self, tmp_path):
        """P2-1：HTTP backend 路径下 dispatcher 必须带 result_callback；
        触发 callback 后应通过 sender.send 把后台结果推送到当前 routing_key。"""
        from evopaw.provider_runtime import ResolvedRuntime
        from evopaw.skills_runtime.dispatcher import SkillDispatcher

        sender = MagicMock()
        sender.send = AsyncMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        runtime = ResolvedRuntime(
            role="main",
            provider_id="openai",
            runtime_family="openai_chat",
            model="gpt-4o",
            api_base="https://api.openai.com",
            api_key="sk-x",
        )
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn(
                "hi", [], "sid_bg",
                routing_key="p2p:ou_bg", root_id="root123",
            )

        dispatcher = fake.calls[0].backend_hints["skill_dispatcher"]
        assert isinstance(dispatcher, SkillDispatcher)
        assert dispatcher.result_callback is not None

        # 触发 callback：应调用 sender.send 把后台结果发回 routing_key + root_id
        await dispatcher.result_callback("abc12345", "bg_skill", "后台结果")
        sender.send.assert_awaited_once()
        args, _ = sender.send.call_args
        sent_routing_key, sent_msg, sent_root = args
        assert sent_routing_key == "p2p:ou_bg"
        assert sent_root == "root123"
        assert "task#abc12345" in sent_msg
        assert "bg_skill" in sent_msg
        assert "后台结果" in sent_msg

    @pytest.mark.asyncio
    async def test_bg_result_callback_swallows_sender_exception(self, tmp_path):
        """P2-1：sender.send 抛错不应往上冒到 dispatcher。"""
        from evopaw.provider_runtime import ResolvedRuntime

        sender = MagicMock()
        sender.send = AsyncMock(side_effect=RuntimeError("sender 故意抛"))
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        runtime = ResolvedRuntime(
            role="main",
            provider_id="openai",
            runtime_family="openai_chat",
            model="gpt-4o",
            api_base="https://api.openai.com",
            api_key="sk-x",
        )
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_bg2", routing_key="p2p:ou_bg2", root_id="r")

        dispatcher = fake.calls[0].backend_hints["skill_dispatcher"]
        # 不应 raise
        await dispatcher.result_callback("deadbeef", "bg_skill", "x")
        sender.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_anthropic_messages_uses_anthropic_image_block(self, tmp_path):
        """anthropic_messages family 下，附图走 Anthropic 形态 image block"""
        from evopaw.provider_runtime import ResolvedRuntime

        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        runtime = ResolvedRuntime(
            role="main",
            provider_id="anthropic",
            runtime_family="anthropic_messages",
            model="claude-sonnet-4-6",
            api_base="https://api.anthropic.com",
            api_key="sk-ant-x",
        )
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)
        fake = FakeBackend()

        msg = "看图说话\n`/workspace/sessions/sid_img/uploads/foo.png`\n"
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch(
                 "evopaw.agents.main_agent.extract_image_path",
                 return_value="/workspace/sessions/sid_img/uploads/foo.png",
             ), \
             patch(
                 "evopaw.agents.main_agent.load_image_data",
                 return_value=("AAA", "image/png"),
             ):
            await fn(msg, [], "sid_img")

        uc = fake.calls[0].user_content
        assert isinstance(uc, list)
        # 末块为 Anthropic 形态 image block：source/base64
        last = uc[-1]
        assert last["type"] == "image"
        assert last["source"]["type"] == "base64"
        assert last["source"]["media_type"] == "image/png"
        assert last["source"]["data"] == "AAA"

    @pytest.mark.asyncio
    async def test_openai_chat_uses_openai_image_url_block(self, tmp_path):
        """openai_chat family 下，附图走 OpenAI 形态 image_url block"""
        from evopaw.provider_runtime import ResolvedRuntime

        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        runtime = ResolvedRuntime(
            role="main",
            provider_id="openai",
            runtime_family="openai_chat",
            model="gpt-4o",
            api_base="https://api.openai.com",
            api_key="sk-x",
        )
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)
        fake = FakeBackend()

        msg = "看图\n`/workspace/sessions/sid_oai_img/uploads/bar.jpg`\n"
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch(
                 "evopaw.agents.main_agent.extract_image_path",
                 return_value="/workspace/sessions/sid_oai_img/uploads/bar.jpg",
             ), \
             patch(
                 "evopaw.agents.main_agent.load_image_data",
                 return_value=("BBB", "image/jpeg"),
             ):
            await fn(msg, [], "sid_oai_img")

        uc = fake.calls[0].user_content
        assert isinstance(uc, list)
        last = uc[-1]
        assert last["type"] == "image_url"
        assert last["image_url"]["url"] == "data:image/jpeg;base64,BBB"

    @pytest.mark.asyncio
    async def test_runtime_without_vision_drops_image_to_text(self, tmp_path):
        """P1-3：runtime.supports_vision=False 时不调用 load_image_data，并在 user_content 末尾追加文字提示。"""
        from evopaw.provider_runtime import ResolvedRuntime

        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        runtime = ResolvedRuntime(
            role="main",
            provider_id="dashscope",
            runtime_family="openai_chat",
            model="qwen3-max",
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-x",
            supports_vision=False,
        )
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)
        fake = FakeBackend()

        msg = "看图\n`/workspace/sessions/sid_no_vis/uploads/foo.png`\n"
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch(
                 "evopaw.agents.main_agent.extract_image_path",
                 return_value="/workspace/sessions/sid_no_vis/uploads/foo.png",
             ), \
             patch(
                 "evopaw.agents.main_agent.load_image_data",
             ) as mock_load:
            await fn(msg, [], "sid_no_vis")

        # vision=False 时不应去读图片字节
        mock_load.assert_not_called()

        # user_content 应为纯文本（openai_chat builder 在无图时返回 str）
        uc = fake.calls[0].user_content
        if isinstance(uc, list):
            text_blob = "".join(
                blk.get("text", "") for blk in uc if isinstance(blk, dict) and blk.get("type") == "text"
            )
        else:
            text_blob = uc
        assert "不支持图像" in text_blob
        assert "/workspace/sessions/sid_no_vis/uploads/foo.png" in text_blob


# ──────────────────────────────────────────────────────────────────
# Hooks（保持原 sanity 测试）
# ──────────────────────────────────────────────────────────────────


class TestHooks:
    def test_build_verbose_hooks_returns_dict(self):
        from evopaw.agents.hooks import build_verbose_hooks
        hooks = build_verbose_hooks()
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks

    def test_build_verbose_hooks_has_matchers(self):
        from evopaw.agents.hooks import build_verbose_hooks
        hooks = build_verbose_hooks()
        assert len(hooks["PreToolUse"]) == 1
        assert len(hooks["PostToolUse"]) == 1


# ──────────────────────────────────────────────────────────────────
# _format_ctx_summaries
# ──────────────────────────────────────────────────────────────────


class TestFormatCtxSummaries:
    def test_empty_list_returns_empty_string(self):
        assert _format_ctx_summaries([]) == ""

    def test_extracts_context_summary_tags(self):
        msgs = [
            {"role": "system", "content": "<context_summary>\n用户讨论了PDF解析\n</context_summary>"},
            {"role": "user", "content": "hello"},
        ]
        result = _format_ctx_summaries(msgs)
        assert "<context_summary>" in result
        assert "PDF解析" in result

    def test_ignores_non_summary_messages(self):
        msgs = [
            {"role": "user", "content": "just a message"},
            {"role": "assistant", "content": "a reply"},
        ]
        assert _format_ctx_summaries(msgs) == ""

    def test_multiple_summaries_joined(self):
        msgs = [
            {"role": "system", "content": "<context_summary>\n摘要1\n</context_summary>"},
            {"role": "system", "content": "<context_summary>\n摘要2\n</context_summary>"},
        ]
        result = _format_ctx_summaries(msgs)
        assert "摘要1" in result
        assert "摘要2" in result


# ──────────────────────────────────────────────────────────────────
# 记忆集成
# ──────────────────────────────────────────────────────────────────


class TestMemoryIntegration:
    @pytest.mark.asyncio
    async def test_ctx_json_loaded_into_prompt(self, tmp_path):
        """ctx.json 中的摘要被注入 TurnRequest.user_content"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()

        ctx_data = [
            {"role": "system", "content": "<context_summary>\n之前讨论了机器学习\n</context_summary>"},
        ]
        (ctx / "sid_mem_ctx.json").write_text(json.dumps(ctx_data), encoding="utf-8")

        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("new question", [], "sid_mem")

        text = fake.calls[0].user_content
        assert "机器学习" in text
        assert "<long_term_context>" in text

    @pytest.mark.asyncio
    async def test_ctx_json_saved_after_turn(self, tmp_path):
        """run_turn 完成后 ctx.json 被更新"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(result=TurnResult(text="agent reply"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hello", [], "sid_ctx_save")

        ctx_path = ctx / "sid_ctx_save_ctx.json"
        assert ctx_path.exists()
        saved = json.loads(ctx_path.read_text(encoding="utf-8"))
        assert any(m.get("content") == "hello" for m in saved)
        assert any(m.get("content") == "agent reply" for m in saved)

    @pytest.mark.asyncio
    async def test_raw_jsonl_appended_after_turn(self, tmp_path):
        """run_turn 完成后 raw.jsonl 被追加 user+assistant 两行"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(result=TurnResult(text="reply"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("msg", [], "sid_raw")

        raw_path = ctx / "sid_raw_raw.jsonl"
        assert raw_path.exists()
        lines = raw_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_pgvector_index_triggered_when_db_dsn_set(self, tmp_path):
        """db_dsn 非空时 async_index_turn 被调度"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx,
            db_dsn="postgresql://localhost/test",
        )
        fake = FakeBackend(result=TurnResult(text="reply"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch(
                 "evopaw.agents.main_agent.async_index_turn",
                 new_callable=AsyncMock,
             ) as mock_idx:
            await fn("hi", [], "sid_idx", routing_key="p2p:user1")
            await asyncio.sleep(0)

        mock_idx.assert_called_once()
        call_kwargs = mock_idx.call_args[1]
        assert call_kwargs["session_id"] == "sid_idx"
        assert call_kwargs["routing_key"] == "p2p:user1"
        assert call_kwargs["user_message"] == "hi"
        assert call_kwargs["assistant_reply"] == "reply"
        assert call_kwargs["db_dsn"] == "postgresql://localhost/test"

    @pytest.mark.asyncio
    async def test_pgvector_index_not_triggered_when_db_dsn_empty(self, tmp_path):
        """db_dsn 为空时跳过 async_index_turn"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, db_dsn="")
        fake = FakeBackend(result=TurnResult(text="reply"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch(
                 "evopaw.agents.main_agent.async_index_turn",
                 new_callable=AsyncMock,
             ) as mock_idx:
            await fn("hi", [], "sid_no_idx")
            await asyncio.sleep(0)

        mock_idx.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_long_term_context_when_ctx_empty(self, tmp_path):
        """ctx.json 不存在 → user_content 不含 <long_term_context>"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend()

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_no_ctx")

        text = fake.calls[0].user_content
        assert "<long_term_context>" not in text


# ──────────────────────────────────────────────────────────────────
# skills_called 收集（接 Trace 取值）
# ──────────────────────────────────────────────────────────────────


class TestSkillsCalled:
    """skills_called 由 backend.run_turn 给到 TurnResult，main_agent 透传给 sender.record_skills"""

    @pytest.mark.asyncio
    async def test_collects_skill_names_and_pushes_to_sender(self, tmp_path):
        sender = MagicMock()
        sender.record_skills = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(
            result=TurnResult(
                text="done",
                skills_called=["tavily_search", "memory-save"],
            )
        )

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_001", root_id="msg_root_001")

        sender.record_skills.assert_called_once_with(
            "msg_root_001", ["tavily_search", "memory-save"],
        )

    @pytest.mark.asyncio
    async def test_no_skill_calls_pushes_empty_list(self, tmp_path):
        sender = MagicMock()
        sender.record_skills = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(result=TurnResult(text="纯文本回复"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_001", root_id="msg_root_002")

        sender.record_skills.assert_called_once_with("msg_root_002", [])

    @pytest.mark.asyncio
    async def test_sender_without_record_skills_method_no_error(self, tmp_path):
        """sender 未实现 record_skills → 不报错"""
        sender = MagicMock(spec=[])
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(result=TurnResult(text="ok"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            result = await fn("hi", [], "sid_001", root_id="msg_root_003")

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_no_record_when_root_id_empty(self, tmp_path):
        sender = MagicMock()
        sender.record_skills = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(result=TurnResult(text="ok"))

        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_001", root_id="")

        sender.record_skills.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# P0-3：Response Finalizer pipeline
# ──────────────────────────────────────────────────────────────────


class _RecordingFinalizer:
    """记录 finalize 入参；按构造时给的 transform 改写文本。"""

    def __init__(self, transform=None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._transform = transform or (lambda t, ctx: t)

    async def finalize(self, text: str, context) -> str:
        self.calls.append((text, context))
        return self._transform(text, context)


class _RaisingFinalizer:
    async def finalize(self, text: str, context) -> str:  # noqa: ARG002
        raise RuntimeError("finalizer boom")


class TestResponseFinalizer:
    @pytest.mark.asyncio
    async def test_default_finalizer_passthrough(self, tmp_path):
        """未传 response_finalizer → 原 final_text 直接返回。"""
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)
        fake = FakeBackend(result=TurnResult(text="原始回复"))
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            out = await fn("hi", [], "sid_def")
        assert out == "原始回复"

    @pytest.mark.asyncio
    async def test_finalizer_replaces_text(self, tmp_path):
        """finalizer 改写文本 → 改写后文本被返回，且写入 ctx/raw。"""
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        finalizer = _RecordingFinalizer(transform=lambda t, _ctx: f"[FINALIZED] {t}")
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx,
            response_finalizer=finalizer,
        )
        fake = FakeBackend(result=TurnResult(text="原文"))
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            out = await fn("msg", [], "sid_fin", routing_key="p2p:u1", root_id="root1")
        assert out == "[FINALIZED] 原文"
        # finalizer 应被调用一次，且收到原始文本
        assert len(finalizer.calls) == 1
        seen_text, seen_ctx = finalizer.calls[0]
        assert seen_text == "原文"
        # context 字段透传正确
        assert seen_ctx.session_id == "sid_fin"
        assert seen_ctx.routing_key == "p2p:u1"
        assert seen_ctx.root_id == "root1"
        assert seen_ctx.role == "main"
        # raw.jsonl 中持久化的 assistant 文本应是 finalizer 改写后的版本
        raw_path = ctx / "sid_fin_raw.jsonl"
        content = raw_path.read_text(encoding="utf-8")
        assert "[FINALIZED] 原文" in content
        # ctx.json 同样
        ctx_path = ctx / "sid_fin_ctx.json"
        ctx_data = json.loads(ctx_path.read_text(encoding="utf-8"))
        assert any(m.get("content") == "[FINALIZED] 原文" for m in ctx_data)

    @pytest.mark.asyncio
    async def test_finalizer_exception_falls_back_to_original(self, tmp_path, caplog):
        """finalizer 抛错 → 主流程不崩，使用原始 final_text。"""
        import logging as _logging
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        # 用 _RaisingFinalizer 直接传入（不经 Composite，模拟违反协议的 finalizer）
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx,
            response_finalizer=_RaisingFinalizer(),
        )
        fake = FakeBackend(result=TurnResult(text="保底回复"))
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             caplog.at_level(_logging.WARNING, logger="evopaw.agents.main_agent"):
            out = await fn("hi", [], "sid_err")
        assert out == "保底回复"
        # 应当记录了一条 warning（并非沉默吞掉）
        assert "response_finalizer.finalize raised" in caplog.text

    @pytest.mark.asyncio
    async def test_finalizer_runs_when_verbose_off(self, tmp_path):
        """verbose=False 时 finalizer 仍然执行（不是 verbose 专用）。"""
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        finalizer = _RecordingFinalizer()
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx,
            response_finalizer=finalizer,
        )
        fake = FakeBackend(result=TurnResult(text="abc"))
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_vb_off", verbose=False)
        assert len(finalizer.calls) == 1

    @pytest.mark.asyncio
    async def test_finalizer_receives_skills_called(self, tmp_path):
        """ResponseFinalizeContext.skills_called 由 result.skills_called 决定。"""
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        finalizer = _RecordingFinalizer()
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx,
            response_finalizer=finalizer,
        )
        fake = FakeBackend(
            result=TurnResult(text="ok", skills_called=["pdf", "tavily_search"]),
        )
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], "sid_skills")
        _, ctx_obj = finalizer.calls[0]
        assert ctx_obj.skills_called == ["pdf", "tavily_search"]

    @pytest.mark.asyncio
    async def test_composite_finalizer_pipeline_in_order(self, tmp_path):
        """CompositeResponseFinalizer 串行执行，前一步输出作为后一步输入。"""
        from evopaw.agents.response_finalizer import CompositeResponseFinalizer
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        f1 = _RecordingFinalizer(transform=lambda t, _c: t + "+f1")
        f2 = _RecordingFinalizer(transform=lambda t, _c: t + "+f2")
        composite = CompositeResponseFinalizer([f1, f2])
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx, response_finalizer=composite,
        )
        fake = FakeBackend(result=TurnResult(text="base"))
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            out = await fn("hi", [], "sid_pipe")
        assert out == "base+f1+f2"
        # f2 看到的是 f1 的输出
        assert f2.calls[0][0] == "base+f1"

    @pytest.mark.asyncio
    async def test_composite_skips_failing_finalizer(self, tmp_path):
        """Composite 中的 finalizer 抛错时降级为上一步文本，不阻断后续。"""
        from evopaw.agents.response_finalizer import CompositeResponseFinalizer
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        good_before = _RecordingFinalizer(transform=lambda t, _c: t + "+ok")
        bad = _RaisingFinalizer()
        good_after = _RecordingFinalizer(transform=lambda t, _c: t + "+after")
        composite = CompositeResponseFinalizer([good_before, bad, good_after])
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx, response_finalizer=composite,
        )
        fake = FakeBackend(result=TurnResult(text="x"))
        with _patch_backend(fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            out = await fn("hi", [], "sid_compbad")
        # bad 抛错被吞，good_after 仍执行，其输入是 good_before 的输出
        assert out == "x+ok+after"


# ──────────────────────────────────────────────────────────────────
# import 卫士：本文件不允许直接 import claude_agent_sdk
# ──────────────────────────────────────────────────────────────────


def test_no_direct_sdk_import_in_this_file():
    """grep 校验：tests/unit/test_main_agent.py 中不能出现 `import claude_agent_sdk`。

    P2 验收门槛之一：主 Agent 的单测层不再耦合 SDK。
    """
    src = Path(__file__).read_text(encoding="utf-8")
    # 允许字符串里出现「claude_agent_sdk」（如本测试自身的解释文本），
    # 但不允许真实的 import 语句。
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("import claude_agent_sdk"):
            raise AssertionError(f"forbidden import: {line!r}")
        if stripped.startswith("from claude_agent_sdk"):
            raise AssertionError(f"forbidden import: {line!r}")
