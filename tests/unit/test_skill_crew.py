"""skill_crew 单元测试"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from xiaopaw.agents.skill_crew import build_skill_crew


# ── build_skill_crew ────────────────────────────────────────────


class TestBuildSkillCrew:
    """build_skill_crew 工厂函数测试（覆盖 lines 64-112）"""

    def _patched(self, **overrides):
        """返回带有标准 mock 的 patch 上下文管理器字典。"""
        defaults = {
            "xiaopaw.agents.skill_crew.MCPServerHTTP": MagicMock(),
            "xiaopaw.agents.skill_crew.AliyunLLM": MagicMock(),
            "xiaopaw.agents.skill_crew.Agent": MagicMock(),
            "xiaopaw.agents.skill_crew.Task": MagicMock(),
            "xiaopaw.agents.skill_crew.Crew": MagicMock(),
        }
        defaults.update(overrides)
        return defaults

    def test_returns_crew_instance(self):
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent"), \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew") as mock_crew_cls:
            result = build_skill_crew(
                skill_name="test_skill",
                skill_instructions="do the thing",
            )
            mock_crew_cls.assert_called_once()
            assert result == mock_crew_cls.return_value

    def test_mcp_server_uses_sandbox_url(self):
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP") as mock_mcp, \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent"), \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="test_skill",
                skill_instructions="instructions",
                sandbox_mcp_url="http://my-sandbox:8099/mcp",
            )
            call_kwargs = mock_mcp.call_args.kwargs
            assert call_kwargs["url"] == "http://my-sandbox:8099/mcp"

    def test_aliyun_llm_uses_sub_agent_model(self):
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM") as mock_llm, \
             patch("xiaopaw.agents.skill_crew.Agent"), \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="test_skill",
                skill_instructions="instructions",
                sub_agent_model="qwen3-turbo",
            )
            call_kwargs = mock_llm.call_args.kwargs
            assert call_kwargs["model"] == "qwen3-turbo"

    def test_session_id_in_agent_backstory(self):
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent") as mock_agent, \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="pdf",
                skill_instructions="pdf instructions",
                session_id="sess-abc123",
            )
            agent_kwargs = mock_agent.call_args.kwargs
            backstory = agent_kwargs.get("backstory", "")
            assert "sess-abc123" in backstory

    def test_no_session_id_uses_placeholder(self):
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent") as mock_agent, \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="pdf",
                skill_instructions="pdf instructions",
                session_id="",
            )
            agent_kwargs = mock_agent.call_args.kwargs
            backstory = agent_kwargs.get("backstory", "")
            assert "<session_id>" in backstory

    def test_agent_max_iter_applied(self):
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent") as mock_agent, \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="test_skill",
                skill_instructions="instructions",
                max_iter=15,
            )
            agent_kwargs = mock_agent.call_args.kwargs
            assert agent_kwargs.get("max_iter") == 15

    def test_task_has_session_dir_in_description(self):
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent"), \
             patch("xiaopaw.agents.skill_crew.Task") as mock_task, \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="test_skill",
                skill_instructions="instructions",
                session_id="sess-xyz",
            )
            task_kwargs = mock_task.call_args.kwargs
            desc = task_kwargs.get("description", "")
            assert "sess-xyz" in desc

    def test_crew_built_with_sequential_process(self):
        from crewai import Process

        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent"), \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew") as mock_crew_cls:
            build_skill_crew(
                skill_name="test_skill",
                skill_instructions="instructions",
            )
            crew_kwargs = mock_crew_cls.call_args.kwargs
            assert crew_kwargs.get("process") == Process.sequential

    def test_skill_name_in_agent_role(self):
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent") as mock_agent, \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="feishu_ops",
                skill_instructions="instructions",
            )
            agent_kwargs = mock_agent.call_args.kwargs
            role = agent_kwargs.get("role", "")
            assert "FEISHU_OPS" in role

    def test_default_sandbox_url_used_when_not_specified(self):
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP") as mock_mcp, \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent"), \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="test_skill",
                skill_instructions="instructions",
            )
            call_kwargs = mock_mcp.call_args.kwargs
            # 默认 URL 指向本地 sandbox
            assert "localhost:8022" in call_kwargs["url"]

    def test_mcp_server_has_no_tool_filter(self):
        """移除白名单后，MCPServerHTTP 不应携带 tool_filter 参数。"""
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP") as mock_mcp, \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent"), \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="test_skill",
                skill_instructions="instructions",
            )
            call_kwargs = mock_mcp.call_args.kwargs
            assert "tool_filter" not in call_kwargs, "不应传入 tool_filter，已开放全部 MCP 工具"

    def test_backstory_warns_skill_name_not_a_tool(self):
        """backstory 应包含明确警告：skill_name 不是工具名，禁止直接调用。"""
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent") as mock_agent, \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="baidu_search",
                skill_instructions="instructions",
            )
            agent_kwargs = mock_agent.call_args.kwargs
            backstory = agent_kwargs.get("backstory", "")
            assert "baidu_search" in backstory
            assert "不是任何工具" in backstory
            assert "sandbox_execute_bash" in backstory

    def test_backstory_skill_name_warning_uses_current_name(self):
        """不同 skill_name 时，警告中的工具名禁用提示应随之更新。"""
        with patch("xiaopaw.agents.skill_crew.MCPServerHTTP"), \
             patch("xiaopaw.agents.skill_crew.AliyunLLM"), \
             patch("xiaopaw.agents.skill_crew.Agent") as mock_agent, \
             patch("xiaopaw.agents.skill_crew.Task"), \
             patch("xiaopaw.agents.skill_crew.Crew"):
            build_skill_crew(
                skill_name="web_browse",
                skill_instructions="instructions",
            )
            agent_kwargs = mock_agent.call_args.kwargs
            backstory = agent_kwargs.get("backstory", "")
            assert "web_browse" in backstory
            assert "不是任何工具" in backstory
