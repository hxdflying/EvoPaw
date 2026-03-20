"""main_crew 单元测试"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xiaopaw.agents.main_crew import (
    _build_crew,
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


# ── build_agent_fn ─────────────────────────────────────────────


class TestBuildAgentFn:
    def test_returns_callable(self):
        sender = MagicMock()
        fn = build_agent_fn(sender)
        assert callable(fn)

    async def test_verbose_false_no_step_callback_to_crew(self):
        """verbose=False 时 _build_crew 收到 step_callback=None"""
        sender = MagicMock()
        fn = build_agent_fn(sender)

        mock_result = MagicMock()
        mock_result.pydantic = None
        mock_result.raw = "test reply"
        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value=mock_result)

        with patch(
            "xiaopaw.agents.main_crew._build_crew", return_value=mock_crew
        ) as mock_build:
            await fn("hello", [], "s-001", "p2p:ou_test", "om_001", False)

        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs.get("step_callback") is None

    async def test_verbose_true_step_callback_provided(self):
        """verbose=True 时 _build_crew 收到非 None step_callback"""
        sender = MagicMock()
        fn = build_agent_fn(sender)

        mock_result = MagicMock()
        mock_result.pydantic = None
        mock_result.raw = "test reply"
        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value=mock_result)

        with patch(
            "xiaopaw.agents.main_crew._build_crew", return_value=mock_crew
        ) as mock_build:
            await fn("hello", [], "s-001", "p2p:ou_test", "om_001", True)

        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs.get("step_callback") is not None

    async def test_returns_pydantic_reply_when_available(self):
        """结果有 pydantic.reply 时优先返回它"""
        sender = MagicMock()
        fn = build_agent_fn(sender)

        mock_pydantic = MagicMock()
        mock_pydantic.reply = "pydantic 回复内容"
        mock_result = MagicMock()
        mock_result.pydantic = mock_pydantic
        mock_result.raw = "raw 回复"
        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value=mock_result)

        with patch("xiaopaw.agents.main_crew._build_crew", return_value=mock_crew):
            result = await fn("hello", [], "s-001", "p2p:ou_test", "om_001", False)

        assert result == "pydantic 回复内容"

    async def test_falls_back_to_raw_when_no_pydantic(self):
        """pydantic 为 None 时 fallback 到 raw"""
        sender = MagicMock()
        fn = build_agent_fn(sender)

        mock_result = MagicMock()
        mock_result.pydantic = None
        mock_result.raw = "raw text reply"
        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value=mock_result)

        with patch("xiaopaw.agents.main_crew._build_crew", return_value=mock_crew):
            result = await fn("hello", [], "s-001", "p2p:ou_test", "om_001", False)

        assert result == "raw text reply"

    async def test_crew_called_with_correct_inputs(self):
        """akickoff 收到正确的 inputs 字典"""
        sender = MagicMock()
        fn = build_agent_fn(sender)

        mock_result = MagicMock()
        mock_result.pydantic = None
        mock_result.raw = "reply"
        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value=mock_result)

        history = [
            MessageEntry(role="user", content="earlier message", ts=1000),
            MessageEntry(role="assistant", content="earlier answer", ts=2000),
        ]

        with patch("xiaopaw.agents.main_crew._build_crew", return_value=mock_crew) as mock_build:
            await fn("user input", history, "s-123", "p2p:ou_abc", "om_123", False)

        # _build_crew 应收到完整历史（供 history_reader 内联使用）和 routing_key
        build_kwargs = mock_build.call_args.kwargs
        assert build_kwargs["session_id"] == "s-123"
        assert build_kwargs["routing_key"] == "p2p:ou_abc"
        assert build_kwargs["history_all"] is history

        inputs = mock_crew.akickoff.call_args.kwargs["inputs"]
        assert inputs["user_message"] == "user input"
        # session_id 不再通过 akickoff inputs 传入 LLM（安全隔离，由系统内部管理）
        assert "session_id" not in inputs
        assert "earlier message" in inputs["history"]

    async def test_history_truncation_applied(self):
        """超过 max_history_turns 的历史被截断"""
        sender = MagicMock()
        fn = build_agent_fn(sender, max_history_turns=2)

        mock_result = MagicMock()
        mock_result.pydantic = None
        mock_result.raw = "reply"
        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value=mock_result)

        history = [
            MessageEntry(role="user", content=f"msg{i}", ts=i * 1000)
            for i in range(10)
        ]

        with patch("xiaopaw.agents.main_crew._build_crew", return_value=mock_crew):
            await fn("new msg", history, "s-001", "p2p:ou_test", "om_001", False)

        inputs = mock_crew.akickoff.call_args.kwargs["inputs"]
        # 最新的应该在
        assert "msg9" in inputs["history"]
        # 早期的应该不在
        assert "msg0" not in inputs["history"]


# ── _load_yaml ──────────────────────────────────────────────────


class TestLoadYaml:
    """_load_yaml 单元测试（覆盖 line 114）"""

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


# ── _build_crew ─────────────────────────────────────────────────


class TestBuildCrew:
    """_build_crew 单元测试（覆盖 lines 131-163）"""

    def _mock_context(self):
        """返回一组 patch 上下文，避免 CrewAI / LLM 初始化副作用。"""
        return [
            patch("xiaopaw.agents.main_crew.AliyunLLM"),
            patch("xiaopaw.agents.main_crew.Agent"),
            patch("xiaopaw.agents.main_crew.Task"),
            patch("xiaopaw.agents.main_crew.Crew"),
            patch("xiaopaw.agents.main_crew.IntermediateTool"),
            patch("xiaopaw.tools.skill_loader.SkillLoaderTool"),
        ]

    def test_build_crew_returns_crew_instance(self):
        with patch("xiaopaw.agents.main_crew.AliyunLLM"), \
             patch("xiaopaw.agents.main_crew.Agent"), \
             patch("xiaopaw.agents.main_crew.Task"), \
             patch("xiaopaw.agents.main_crew.Crew") as mock_crew_cls, \
             patch("xiaopaw.agents.main_crew.IntermediateTool"), \
             patch("xiaopaw.tools.skill_loader.SkillLoaderTool"):
            result = _build_crew("test-session")
            mock_crew_cls.assert_called_once()
            assert result == mock_crew_cls.return_value

    def test_build_crew_passes_session_id_to_skill_loader(self):
        with patch("xiaopaw.agents.main_crew.AliyunLLM"), \
             patch("xiaopaw.agents.main_crew.Agent"), \
             patch("xiaopaw.agents.main_crew.Task"), \
             patch("xiaopaw.agents.main_crew.Crew"), \
             patch("xiaopaw.agents.main_crew.IntermediateTool"), \
             patch("xiaopaw.tools.skill_loader.SkillLoaderTool") as mock_loader:
            _build_crew("my-session-id")
            # SkillLoaderTool 应以 session_id 构建
            call_kwargs = mock_loader.call_args.kwargs
            assert call_kwargs.get("session_id") == "my-session-id"

    def test_build_crew_passes_sandbox_url_when_provided(self):
        with patch("xiaopaw.agents.main_crew.AliyunLLM"), \
             patch("xiaopaw.agents.main_crew.Agent"), \
             patch("xiaopaw.agents.main_crew.Task"), \
             patch("xiaopaw.agents.main_crew.Crew"), \
             patch("xiaopaw.agents.main_crew.IntermediateTool"), \
             patch("xiaopaw.tools.skill_loader.SkillLoaderTool") as mock_loader:
            _build_crew("s-001", sandbox_url="http://sandbox:8022/mcp")
            call_kwargs = mock_loader.call_args.kwargs
            assert call_kwargs.get("sandbox_url") == "http://sandbox:8022/mcp"

    def test_build_crew_no_sandbox_url_not_passed(self):
        with patch("xiaopaw.agents.main_crew.AliyunLLM"), \
             patch("xiaopaw.agents.main_crew.Agent"), \
             patch("xiaopaw.agents.main_crew.Task"), \
             patch("xiaopaw.agents.main_crew.Crew"), \
             patch("xiaopaw.agents.main_crew.IntermediateTool"), \
             patch("xiaopaw.tools.skill_loader.SkillLoaderTool") as mock_loader:
            _build_crew("s-001", sandbox_url="")
            call_kwargs = mock_loader.call_args.kwargs
            # sandbox_url 为空时不传
            assert "sandbox_url" not in call_kwargs

    def test_build_crew_with_extra_tools(self):
        extra_tool = MagicMock()
        with patch("xiaopaw.agents.main_crew.AliyunLLM"), \
             patch("xiaopaw.agents.main_crew.Agent") as mock_agent_cls, \
             patch("xiaopaw.agents.main_crew.Task"), \
             patch("xiaopaw.agents.main_crew.Crew"), \
             patch("xiaopaw.agents.main_crew.IntermediateTool"), \
             patch("xiaopaw.tools.skill_loader.SkillLoaderTool"):
            _build_crew("s-001", extra_tools=[extra_tool])
            # extra_tool 应出现在 Agent tools 中
            tools_arg = mock_agent_cls.call_args.kwargs.get("tools") or \
                        mock_agent_cls.call_args.args[0] if mock_agent_cls.call_args.args else []
            agent_tools = mock_agent_cls.call_args.kwargs.get("tools", [])
            assert extra_tool in agent_tools

    def test_build_crew_with_step_callback(self):
        step_cb = MagicMock()
        with patch("xiaopaw.agents.main_crew.AliyunLLM"), \
             patch("xiaopaw.agents.main_crew.Agent"), \
             patch("xiaopaw.agents.main_crew.Task"), \
             patch("xiaopaw.agents.main_crew.Crew") as mock_crew_cls, \
             patch("xiaopaw.agents.main_crew.IntermediateTool"), \
             patch("xiaopaw.tools.skill_loader.SkillLoaderTool"):
            _build_crew("s-001", step_callback=step_cb)
            crew_kwargs = mock_crew_cls.call_args.kwargs
            assert crew_kwargs.get("step_callback") == step_cb
