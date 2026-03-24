"""main_crew 单元测试"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xiaopaw.agents.main_crew import (
    _format_history,
    _load_yaml,
    _make_step_callback,
    build_agent_fn,
)
from xiaopaw.session.models import MessageEntry


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
        """超出 max_turns 时，只保留最后 N 条"""
        history = [
            MessageEntry(role="user", content=f"msg{i}", ts=i * 1000)
            for i in range(10)
        ]
        result = _format_history(history, max_turns=4)
        # 最后 4 条应存在
        assert "msg9" in result
        assert "msg8" in result
        assert "msg7" in result
        assert "msg6" in result
        # 更早的不应存在
        assert "msg0" not in result
        assert "msg5" not in result

    def test_truncated_history_adds_note(self):
        """截断时应包含提示，告知 agent 可通过 Skill 查询完整历史"""
        history = [
            MessageEntry(role="user", content=f"msg{i}", ts=i * 1000)
            for i in range(10)
        ]
        result = _format_history(history, max_turns=4)
        assert "历史" in result  # 包含存档/历史说明
        assert "history_reader" in result  # 提示使用 Skill

    def test_max_turns_exact_boundary_no_note(self):
        """恰好等于 max_turns 时，不需要截断提示"""
        history = [
            MessageEntry(role="user", content="q", ts=1000),
            MessageEntry(role="assistant", content="a", ts=2000),
        ]
        result = _format_history(history, max_turns=2)
        assert "history_reader" not in result

    def test_default_max_turns_is_20(self):
        """默认 max_turns=20，19 条不截断"""
        history = [
            MessageEntry(role="user", content=f"m{i}", ts=i * 1000)
            for i in range(19)
        ]
        result = _format_history(history)
        assert "m0" in result
        assert "history_reader" not in result


# ── _make_step_callback ────────────────────────────────────────


class TestStepCallback:
    async def test_agent_action_with_thought_sends_to_feishu(self):
        """AgentAction 有 thought → 发送 💭 前缀消息"""
        from crewai.agents.parser import AgentAction

        sender = MagicMock()
        sender.send = AsyncMock()
        cb = _make_step_callback(sender, "p2p:ou_test", "om_001")

        step = AgentAction(
            thought="分析用户需求，应调用 SkillLoaderTool",
            tool="SkillLoaderTool",
            tool_input="{}",
            text="...",
        )
        await cb(step)

        sender.send.assert_awaited_once()
        args = sender.send.call_args[0]
        assert args[0] == "p2p:ou_test"
        assert "💭" in args[1]
        assert "分析用户需求" in args[1]
        assert args[2] == "om_001"

    async def test_agent_action_with_empty_thought_not_sent(self):
        """AgentAction thought 为空 → 不发送"""
        from crewai.agents.parser import AgentAction

        sender = MagicMock()
        sender.send = AsyncMock()
        cb = _make_step_callback(sender, "p2p:ou_test", "om_001")

        step = AgentAction(thought="", tool="t", tool_input="{}", text="...")
        await cb(step)

        sender.send.assert_not_awaited()

    async def test_agent_action_with_whitespace_thought_not_sent(self):
        """thought 只有空白字符 → 不发送"""
        from crewai.agents.parser import AgentAction

        sender = MagicMock()
        sender.send = AsyncMock()
        cb = _make_step_callback(sender, "p2p:ou_test", "om_001")

        step = AgentAction(thought="   \n  ", tool="t", tool_input="{}", text="...")
        await cb(step)

        sender.send.assert_not_awaited()

    async def test_sender_failure_does_not_propagate(self):
        """sender.send 抛异常时不影响主流程"""
        from crewai.agents.parser import AgentAction

        sender = MagicMock()
        sender.send = AsyncMock(side_effect=RuntimeError("Feishu down"))
        cb = _make_step_callback(sender, "p2p:ou_test", "om_001")

        step = AgentAction(thought="有思考内容", tool="t", tool_input="{}", text="...")
        # 不应抛出异常
        await cb(step)

    async def test_agent_finish_thought_not_sent(self):
        """AgentFinish 不发送（verbose 只推理步骤）"""
        from crewai.agents.parser import AgentFinish

        sender = MagicMock()
        sender.send = AsyncMock()
        cb = _make_step_callback(sender, "p2p:ou_test", "om_001")

        step = AgentFinish(thought="最终思考", output="完成", text="...")
        await cb(step)

        sender.send.assert_not_awaited()


# ── build_agent_fn（新签名）─────────────────────────────────────


class TestBuildAgentFn:
    def test_returns_callable(self, tmp_path):
        """build_agent_fn 返回可调用的 agent_fn"""
        sender = MagicMock()
        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, db_dsn="")
        assert callable(fn)


# ── _load_yaml ──────────────────────────────────────────────────


class TestLoadYaml:
    def test_loads_yaml_file(self, tmp_path):
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("key: value\nnum: 42", encoding="utf-8")
        result = _load_yaml(yaml_file)
        assert result == {"key": "value", "num": 42}

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("", encoding="utf-8")
        result = _load_yaml(yaml_file)
        assert result == {}

    def test_nested_yaml(self, tmp_path):
        yaml_file = tmp_path / "nested.yaml"
        yaml_file.write_text("parent:\n  child: hello", encoding="utf-8")
        result = _load_yaml(yaml_file)
        assert result == {"parent": {"child": "hello"}}