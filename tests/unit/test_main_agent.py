"""main_agent 单元测试"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from evopaw.agents.main_agent import _format_ctx_summaries, _format_history, build_agent_fn
from evopaw.session.models import MessageEntry


# ── _format_history ────────────────────────────────────────────


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


# ── build_agent_fn ─────────────────────────────────────────────


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
        """验证 build_bootstrap_prompt() 被调用"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "Claude 回复"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query) as mock_q, \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value="<soul>test</soul>") as mock_bp, \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            # 让 mock_result 被 isinstance(ResultMessage) 识别
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                result = await fn("你好", [], "sid_001")

            mock_bp.assert_called_once_with(ws)

    @pytest.mark.asyncio
    async def test_cwd_points_to_session_dir(self, tmp_path):
        """验证 cwd 指向 {workspace_dir}/sessions/{session_id}/"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("hi", [], "sid_123")

            call_kwargs = mock_opts.call_args
            assert call_kwargs[1]["cwd"] == str(ws / "sessions" / "sid_123")

    @pytest.mark.asyncio
    async def test_verbose_passes_hooks(self, tmp_path):
        """验证 verbose=True 时 hooks 被传入 options"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.build_verbose_hooks", return_value={"PreToolUse": []}) as mock_hooks:
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("hi", [], "sid_001", verbose=True)

            mock_hooks.assert_called_once()
            call_kwargs = mock_opts.call_args
            assert call_kwargs[1]["hooks"] == {"PreToolUse": []}

    @pytest.mark.asyncio
    async def test_verbose_thread_no_callback(self, tmp_path):
        """verbose=True + thread routing_key → callback=None（不推送飞书）"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.build_verbose_hooks") as mock_hooks:
            mock_hooks.return_value = {"PreToolUse": []}
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("hi", [], "sid_thr", routing_key="thread:chat1:thr1", verbose=True)

            mock_hooks.assert_called_once_with(callback=None)

    @pytest.mark.asyncio
    async def test_verbose_p2p_passes_callback(self, tmp_path):
        """verbose=True + p2p routing_key → callback 非 None"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.build_verbose_hooks") as mock_hooks:
            mock_hooks.return_value = {"PreToolUse": []}
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("hi", [], "sid_p2p", routing_key="p2p:user1", verbose=True)

            mock_hooks.assert_called_once()
            cb_arg = mock_hooks.call_args[1]["callback"]
            assert cb_arg is not None
            assert callable(cb_arg)

    @pytest.mark.asyncio
    async def test_verbose_group_passes_callback(self, tmp_path):
        """verbose=True + group routing_key → callback 非 None"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.build_verbose_hooks") as mock_hooks:
            mock_hooks.return_value = {"PreToolUse": []}
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("hi", [], "sid_grp", routing_key="group:chat1", verbose=True)

            mock_hooks.assert_called_once()
            cb_arg = mock_hooks.call_args[1]["callback"]
            assert cb_arg is not None

    @pytest.mark.asyncio
    async def test_sdk_error_returns_friendly_message(self, tmp_path):
        """验证 SDK 异常时返回错误提示文本"""
        from claude_agent_sdk import CLIConnectionError

        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        async def failing_query(**kwargs):
            raise CLIConnectionError("connection refused")
            yield  # noqa: unreachable — make it an async generator

        with patch("evopaw.agents.main_agent.query", side_effect=failing_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            result = await fn("hi", [], "sid_err")

        assert "调用失败" in result

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_friendly_message(self, tmp_path):
        """验证意外异常时返回内部错误提示"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        async def exploding_query(**kwargs):
            raise RuntimeError("boom")
            yield  # noqa: unreachable

        with patch("evopaw.agents.main_agent.query", side_effect=exploding_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            result = await fn("hi", [], "sid_boom")

        assert "内部错误" in result

    @pytest.mark.asyncio
    async def test_history_included_in_prompt(self, tmp_path):
        """验证历史消息被拼入 prompt"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "reply"

        captured_prompt = {}

        async def capture_query(prompt, options):
            captured_prompt["value"] = prompt
            yield mock_result

        history = [
            MessageEntry(role="user", content="prev question", ts=1000),
            MessageEntry(role="assistant", content="prev answer", ts=2000),
        ]

        with patch("evopaw.agents.main_agent.query", side_effect=capture_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("new question", history, "sid_hist")

        assert "prev question" in captured_prompt["value"]
        assert "prev answer" in captured_prompt["value"]
        assert "new question" in captured_prompt["value"]


# ── hooks ──────────────────────────────────────────────────────


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


# ── _format_ctx_summaries ─────────────────────────────────────


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


# ── 记忆集成 ──────────────────────────────────────────────────


class TestMemoryIntegration:
    @pytest.mark.asyncio
    async def test_ctx_json_loaded_into_prompt(self, tmp_path):
        """验证 ctx.json 中的摘要被注入到 prompt"""
        import json

        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()

        # 写入 ctx.json
        ctx_data = [
            {"role": "system", "content": "<context_summary>\n之前讨论了机器学习\n</context_summary>"},
        ]
        (ctx / "sid_mem_ctx.json").write_text(json.dumps(ctx_data), encoding="utf-8")

        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "ok"
        captured_prompt = {}

        async def capture_query(prompt, options):
            captured_prompt["value"] = prompt
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=capture_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("new question", [], "sid_mem")

        assert "机器学习" in captured_prompt["value"]
        assert "<long_term_context>" in captured_prompt["value"]

    @pytest.mark.asyncio
    async def test_ctx_json_saved_after_query(self, tmp_path):
        """验证 query 完成后 ctx.json 被更新"""
        import json

        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "agent reply"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("hello", [], "sid_ctx_save")

        ctx_path = ctx / "sid_ctx_save_ctx.json"
        assert ctx_path.exists()
        saved = json.loads(ctx_path.read_text(encoding="utf-8"))
        assert any(m.get("content") == "hello" for m in saved)
        assert any(m.get("content") == "agent reply" for m in saved)

    @pytest.mark.asyncio
    async def test_raw_jsonl_appended_after_query(self, tmp_path):
        """验证 query 完成后 raw.jsonl 被追加"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "reply"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("msg", [], "sid_raw")

        raw_path = ctx / "sid_raw_raw.jsonl"
        assert raw_path.exists()
        lines = raw_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_pgvector_index_triggered_when_db_dsn_set(self, tmp_path):
        """验证 db_dsn 非空时 async_index_turn 被触发"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, db_dsn="postgresql://localhost/test")

        mock_result = MagicMock()
        mock_result.result = "reply"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.async_index_turn", new_callable=AsyncMock) as mock_idx:
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("hi", [], "sid_idx", routing_key="p2p:user1")

            # async_index_turn 通过 create_task 调用，需要等一下 event loop
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
        """验证 db_dsn 为空时不触发 async_index_turn"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, db_dsn="")

        mock_result = MagicMock()
        mock_result.result = "reply"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.async_index_turn", new_callable=AsyncMock) as mock_idx:
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("hi", [], "sid_no_idx")

            await asyncio.sleep(0)
            mock_idx.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_long_term_context_when_ctx_empty(self, tmp_path):
        """验证 ctx.json 不存在时不注入 long_term_context"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "ok"
        captured_prompt = {}

        async def capture_query(prompt, options):
            captured_prompt["value"] = prompt
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=capture_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            with patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
                await fn("hi", [], "sid_no_ctx")

        assert "<long_term_context>" not in captured_prompt["value"]


# ── skills_called 收集（接 Trace 取值）────────────────────────────


class _FakeToolUseBlock:
    """模拟 ToolUseBlock：name + input"""

    def __init__(self, name: str, input_data: dict):
        self.name = name
        self.input = input_data


class _FakeAssistantMessage:
    """模拟 AssistantMessage：content 为 block 列表"""

    def __init__(self, content: list):
        self.content = content


class TestSkillsCalled:
    """主 Agent 在 SDK 消息流里收集 skill_loader tool_use，并通过 record_skills 上报"""

    @pytest.mark.asyncio
    async def test_collects_skill_names_and_pushes_to_sender(self, tmp_path):
        """skill_loader 的 tool_use 块按调用顺序累积，pushed via sender.record_skills"""
        sender = MagicMock()
        sender.record_skills = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        tool_use_a = _FakeToolUseBlock(
            "mcp__evopaw__skill_loader", {"skill_name": "tavily_search"}
        )
        tool_use_b = _FakeToolUseBlock(
            "mcp__evopaw__skill_loader", {"skill_name": "memory-save"}
        )
        non_skill_tool = _FakeToolUseBlock("Bash", {"command": "ls"})
        assistant_msg = _FakeAssistantMessage([tool_use_a, non_skill_tool, tool_use_b])

        mock_result = MagicMock()
        mock_result.result = "done"

        async def fake_query(**kwargs):
            yield assistant_msg
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.AssistantMessage", _FakeAssistantMessage), \
             patch("evopaw.agents.main_agent.ToolUseBlock", _FakeToolUseBlock), \
             patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            await fn("hi", [], "sid_001", root_id="msg_root_001")

        sender.record_skills.assert_called_once_with(
            "msg_root_001", ["tavily_search", "memory-save"]
        )

    @pytest.mark.asyncio
    async def test_no_skill_calls_pushes_empty_list(self, tmp_path):
        """无 skill 调用时仍上报空列表"""
        sender = MagicMock()
        sender.record_skills = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "纯文本回复"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            await fn("hi", [], "sid_001", root_id="msg_root_002")

        sender.record_skills.assert_called_once_with("msg_root_002", [])

    @pytest.mark.asyncio
    async def test_sender_without_record_skills_method_no_error(self, tmp_path):
        """sender 不实现 record_skills 时（普通 FeishuSender），不报错"""
        sender = MagicMock(spec=[])  # 完全没有 record_skills 属性
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            # 不应抛异常
            result = await fn("hi", [], "sid_001", root_id="msg_root_003")

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_no_record_when_root_id_empty(self, tmp_path):
        """root_id 为空时跳过 record_skills（避免污染存储）"""
        sender = MagicMock()
        sender.record_skills = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx)

        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(**kwargs):
            yield mock_result

        with patch("evopaw.agents.main_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch("evopaw.agents.main_agent.build_main_agent_options") as mock_opts, \
             patch("evopaw.agents.main_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            await fn("hi", [], "sid_001", root_id="")

        sender.record_skills.assert_not_called()
