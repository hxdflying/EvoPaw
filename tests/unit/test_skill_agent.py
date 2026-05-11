"""skill_agent 单元测试"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from evopaw.agents.skill_agent import _new_task_id, run_skill_agent


class TestRunSkillAgent:
    """run_skill_agent() 测试，mock SDK query()。"""

    @pytest.mark.asyncio
    async def test_returns_result_text(self):
        """正常调用应返回 ResultMessage.result。"""
        mock_result = MagicMock()
        mock_result.result = "PDF 内容已提取：第一章..."

        async def fake_query(prompt, options):
            yield mock_result

        with patch("evopaw.agents.skill_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts, \
             patch("evopaw.agents.skill_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            result = await run_skill_agent(
                skill_name="pdf",
                skill_instructions="# PDF Skill\n处理 PDF 文件",
                task_context="读取上传的 report.pdf",
                session_path="/workspace/sessions/sid-123",
            )

        assert "PDF 内容已提取" in result

    @pytest.mark.asyncio
    async def test_system_prompt_is_skill_instructions(self):
        """system_prompt 应为 SKILL.md 正文。"""
        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(prompt, options):
            yield mock_result

        with patch("evopaw.agents.skill_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts, \
             patch("evopaw.agents.skill_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            await run_skill_agent(
                skill_name="pdf",
                skill_instructions="# PDF 操作指南\n步骤1...",
                task_context="do it",
                session_path="/workspace/sessions/sid-001",
            )

        mock_opts.assert_called_once()
        call_kwargs = mock_opts.call_args
        assert call_kwargs[1]["system_prompt"] == "# PDF 操作指南\n步骤1..."

    @pytest.mark.asyncio
    async def test_cwd_is_session_path(self):
        """cwd 应指向 session_path。"""
        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(prompt, options):
            yield mock_result

        with patch("evopaw.agents.skill_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts, \
             patch("evopaw.agents.skill_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            await run_skill_agent(
                skill_name="pdf",
                skill_instructions="instructions",
                task_context="do it",
                session_path="/workspace/sessions/sid-xyz",
            )

        call_kwargs = mock_opts.call_args
        assert call_kwargs[1]["cwd"] == "/workspace/sessions/sid-xyz"

    @pytest.mark.asyncio
    async def test_model_and_max_turns_are_threaded(self):
        """自定义 model / max_turns 应透传给 build_sub_agent_options（F4 接通验证）。"""
        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(prompt, options):
            yield mock_result

        with patch("evopaw.agents.skill_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts, \
             patch("evopaw.agents.skill_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            await run_skill_agent(
                skill_name="pdf",
                skill_instructions="instructions",
                task_context="do it",
                session_path="/workspace/sessions/sid",
                model="claude-opus-4-7",
                max_turns=42,
            )

        call_kwargs = mock_opts.call_args
        assert call_kwargs[1]["model"] == "claude-opus-4-7"
        assert call_kwargs[1]["max_turns"] == 42

    @pytest.mark.asyncio
    async def test_prompt_is_task_context(self):
        """prompt 应为 task_context。"""
        mock_result = MagicMock()
        mock_result.result = "done"
        captured = {}

        async def fake_query(prompt, options):
            captured["prompt"] = prompt
            yield mock_result

        with patch("evopaw.agents.skill_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts, \
             patch("evopaw.agents.skill_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            await run_skill_agent(
                skill_name="pdf",
                skill_instructions="instructions",
                task_context="读取 report.pdf 的第一页",
                session_path="/workspace/sessions/sid",
            )

        assert captured["prompt"] == "读取 report.pdf 的第一页"

    @pytest.mark.asyncio
    async def test_cli_error_returns_friendly_message(self):
        """CLIConnectionError 应返回友好提示。"""
        from claude_agent_sdk import CLIConnectionError

        async def failing_query(prompt, options):
            raise CLIConnectionError("connection refused")
            yield  # noqa: unreachable

        with patch("evopaw.agents.skill_agent.query", side_effect=failing_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            result = await run_skill_agent(
                skill_name="pdf",
                skill_instructions="instructions",
                task_context="do it",
                session_path="/workspace/sessions/sid",
            )

        assert "执行失败" in result
        assert "pdf" in result

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_friendly_message(self):
        """意外异常应返回内部错误提示。"""
        async def exploding_query(prompt, options):
            raise RuntimeError("boom")
            yield  # noqa: unreachable

        with patch("evopaw.agents.skill_agent.query", side_effect=exploding_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            result = await run_skill_agent(
                skill_name="xlsx",
                skill_instructions="instructions",
                task_context="process",
                session_path="/workspace/sessions/sid",
            )

        assert "内部错误" in result
        assert "xlsx" in result

    @pytest.mark.asyncio
    async def test_empty_result_returns_warning(self):
        """SDK 返回空结果应返回警告。"""
        async def empty_query(prompt, options):
            return
            yield  # noqa: unreachable — make it async generator

        with patch("evopaw.agents.skill_agent.query", side_effect=empty_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            result = await run_skill_agent(
                skill_name="docx",
                skill_instructions="instructions",
                task_context="do it",
                session_path="/workspace/sessions/sid",
            )

        assert "未返回有效结果" in result
        assert "docx" in result


# ──────────────────────────────────────────────────────────────────
# task_id 与日志/错误文本关联
# ──────────────────────────────────────────────────────────────────


_TASK_ID_RE = re.compile(r"^[0-9a-f]{8}$")


class TestNewTaskId:
    def test_returns_8_hex(self):
        tid = _new_task_id()
        assert _TASK_ID_RE.match(tid), f"unexpected task_id format: {tid!r}"

    def test_returns_unique(self):
        ids = {_new_task_id() for _ in range(100)}
        # 100 个 uuid4[:8] 碰撞概率极低（~ 1.2e-7）
        assert len(ids) == 100


class TestTaskIdInRunSkillAgent:
    @pytest.mark.asyncio
    async def test_passed_task_id_appears_in_error_text(self):
        """显式传入 task_id 时，错误返回应包含 task#<id>。"""
        from claude_agent_sdk import CLIConnectionError

        async def failing_query(prompt, options):
            raise CLIConnectionError("connection refused")
            yield  # noqa

        with patch("evopaw.agents.skill_agent.query", side_effect=failing_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            result = await run_skill_agent(
                skill_name="pdf",
                skill_instructions="instructions",
                task_context="do it",
                session_path="/workspace/sessions/sid",
                task_id="abcd1234",
            )

        assert "task#abcd1234" in result
        assert "执行失败" in result

    @pytest.mark.asyncio
    async def test_default_task_id_is_8_hex(self):
        """缺省 task_id 自动生成 8 字符 hex，并出现在 empty 警告里。"""
        async def empty_query(prompt, options):
            return
            yield  # noqa

        with patch("evopaw.agents.skill_agent.query", side_effect=empty_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            result = await run_skill_agent(
                skill_name="docx",
                skill_instructions="instructions",
                task_context="do it",
                session_path="/workspace/sessions/sid",
            )

        m = re.search(r"task#([0-9a-f]+)", result)
        assert m is not None, f"task#... missing in result: {result!r}"
        assert _TASK_ID_RE.match(m.group(1)), m.group(1)

    @pytest.mark.asyncio
    async def test_unexpected_error_includes_task_id(self):
        async def exploding_query(prompt, options):
            raise RuntimeError("boom")
            yield  # noqa

        with patch("evopaw.agents.skill_agent.query", side_effect=exploding_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts:
            mock_opts.return_value = MagicMock()
            result = await run_skill_agent(
                skill_name="xlsx",
                skill_instructions="instructions",
                task_context="process",
                session_path="/workspace/sessions/sid",
                task_id="deadbeef",
            )

        assert "task#deadbeef" in result
        assert "内部错误" in result

    @pytest.mark.asyncio
    async def test_allowed_tools_passthrough(self):
        """allowed_tools 必须被透传到 build_sub_agent_options，缺省为 None。"""
        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(prompt, options):
            yield mock_result

        # 显式传入子集
        with patch("evopaw.agents.skill_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts, \
             patch("evopaw.agents.skill_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            await run_skill_agent(
                skill_name="memory-save",
                skill_instructions="instructions",
                task_context="save it",
                session_path="/workspace/sessions/sid",
                allowed_tools=["Read", "Write"],
            )
        assert mock_opts.call_args[1]["allowed_tools"] == ["Read", "Write"]

        # 不传 → 走 None（build_sub_agent_options 内部退回默认全集）
        with patch("evopaw.agents.skill_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts, \
             patch("evopaw.agents.skill_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            await run_skill_agent(
                skill_name="pdf",
                skill_instructions="instructions",
                task_context="do it",
                session_path="/workspace/sessions/sid",
            )
        assert mock_opts.call_args[1]["allowed_tools"] is None

    @pytest.mark.asyncio
    async def test_log_prefix_contains_task_id(self, caplog):
        """日志前缀应为 [subagent#<id>]。"""
        import logging

        mock_result = MagicMock()
        mock_result.result = "ok"

        async def fake_query(prompt, options):
            yield mock_result

        with patch("evopaw.agents.skill_agent.query", side_effect=fake_query), \
             patch("evopaw.agents.skill_agent.build_sub_agent_options") as mock_opts, \
             patch("evopaw.agents.skill_agent.ResultMessage", type(mock_result)):
            mock_opts.return_value = MagicMock()
            with caplog.at_level(logging.INFO, logger="evopaw.agents.skill_agent"):
                await run_skill_agent(
                    skill_name="pdf",
                    skill_instructions="instructions",
                    task_context="do it",
                    session_path="/workspace/sessions/sid",
                    task_id="cafe1234",
                )

        msgs = [r.getMessage() for r in caplog.records]
        assert any("[subagent#cafe1234]" in m for m in msgs), msgs
