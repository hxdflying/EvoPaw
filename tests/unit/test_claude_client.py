"""evopaw.llm.claude_client 单元测试。

只覆盖 build_sub_agent_options 的工具白名单逻辑——这是 B1 修复
（SKILL.md `allowed-tools` 真正下发到 ClaudeAgentOptions）的关键接合点。
"""

from __future__ import annotations

from evopaw.llm.claude_client import (
    _DEFAULT_SUB_AGENT_TOOLS,
    build_sub_agent_options,
)


class TestBuildSubAgentOptionsAllowedTools:
    def test_default_tool_set(self):
        opts = build_sub_agent_options(
            system_prompt="prompt",
            cwd="/workspace",
        )
        assert list(opts.allowed_tools) == _DEFAULT_SUB_AGENT_TOOLS

    def test_none_uses_default(self):
        opts = build_sub_agent_options(
            system_prompt="prompt",
            cwd="/workspace",
            allowed_tools=None,
        )
        assert list(opts.allowed_tools) == _DEFAULT_SUB_AGENT_TOOLS

    def test_explicit_subset_wins(self):
        opts = build_sub_agent_options(
            system_prompt="prompt",
            cwd="/workspace",
            allowed_tools=["Read", "Write"],
        )
        assert list(opts.allowed_tools) == ["Read", "Write"]

    def test_options_holds_copy_not_reference(self):
        """传入的 list 不应被改写或与默认全集共享引用。"""
        custom = ["Read"]
        opts1 = build_sub_agent_options(
            system_prompt="p", cwd="/workspace", allowed_tools=custom,
        )
        opts2 = build_sub_agent_options(
            system_prompt="p", cwd="/workspace",
        )
        # 修改 default 全集 / custom 都不应反向污染已构造的 options
        custom.append("Bash")
        _DEFAULT_SUB_AGENT_TOOLS.append("__poisoned__")  # 暂时改默认
        try:
            assert list(opts1.allowed_tools) == ["Read"]
            # opts2 取的是 list(_DEFAULT_SUB_AGENT_TOOLS) 副本，不会跟着默认变
            assert "__poisoned__" not in list(opts2.allowed_tools)
        finally:
            _DEFAULT_SUB_AGENT_TOOLS.remove("__poisoned__")
