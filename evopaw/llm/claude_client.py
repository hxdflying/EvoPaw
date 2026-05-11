"""Claude Agent SDK 客户端封装

提供 Claude Agent SDK 的配置构建：
  - ClaudeSDKClient（多轮对话）或 query()（单次）
  - @tool 装饰器 + create_sdk_mcp_server 构建 MCP 工具
  - PreToolUse / PostToolUse hooks 做 verbose 推送
  - Subagent + SubagentStart/SubagentStop hooks 做 Sub-Agent 隔离
"""

from __future__ import annotations

import logging
import shutil

from claude_agent_sdk import ClaudeAgentOptions

logger = logging.getLogger(__name__)

# 默认模型配置
DEFAULT_PLANNER_MODEL = "claude-sonnet-4-6"
DEFAULT_SUB_AGENT_MODEL = "claude-haiku-4-5"


def check_claude_cli() -> bool:
    """检测 Claude Code CLI 是否可用（Claude Agent SDK 依赖它）。

    Returns:
        True 如果 CLI 可用
    """
    return shutil.which("claude") is not None


def build_main_agent_options(
    system_prompt: str,
    cwd: str,
    model: str = DEFAULT_PLANNER_MODEL,
    max_turns: int = 50,
    hooks: dict | None = None,
    mcp_servers: dict | None = None,
    allowed_tools: list[str] | None = None,
) -> ClaudeAgentOptions:
    """构建主 Agent 的 ClaudeAgentOptions。

    Args:
        system_prompt: 系统提示词（含 Bootstrap 上下文）
        cwd: 工作目录（session workspace 路径）
        model: 模型名称
        max_turns: 最大对话轮次
        hooks: 事件钩子（PreToolUse / PostToolUse 等）
        mcp_servers: MCP 服务器配置
        allowed_tools: 允许使用的工具列表
    """
    return ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools or [],
        cwd=cwd,
        max_turns=max_turns,
        permission_mode="bypassPermissions",
        hooks=hooks or {},
        mcp_servers=mcp_servers or {},
    )


# Sub-Agent 默认开放的工具集合。SKILL.md frontmatter 没声明 `allowed-tools`
# 时使用这一组；与 evopaw/skills_runtime/registry.py:_VALID_SUB_AGENT_TOOLS
# 必须保持一致（registry 的白名单校验只允许这些值）。
_DEFAULT_SUB_AGENT_TOOLS: list[str] = ["Bash", "Read", "Write", "Edit", "Grep", "Glob"]


def build_sub_agent_options(
    system_prompt: str,
    cwd: str,
    model: str = DEFAULT_SUB_AGENT_MODEL,
    max_turns: int = 20,
    allowed_tools: list[str] | None = None,
) -> ClaudeAgentOptions:
    """构建 Sub-Agent（任务型 Skill 执行）的 ClaudeAgentOptions。

    Args:
        system_prompt: SKILL.md 正文作为系统提示词
        cwd: session workspace 路径
        model: Sub-Agent 使用的模型
        max_turns: 最大对话轮次
        allowed_tools: SKILL.md frontmatter 声明的最小权限工具子集；
            None 时退回 `_DEFAULT_SUB_AGENT_TOOLS`（最大允许集）。
    """
    tools = list(allowed_tools) if allowed_tools else list(_DEFAULT_SUB_AGENT_TOOLS)
    return ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        allowed_tools=tools,
        cwd=cwd,
        max_turns=max_turns,
        permission_mode="bypassPermissions",
    )
