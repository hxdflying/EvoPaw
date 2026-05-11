"""claude_mcp adapter —— 把 SkillDispatcher 包成 Claude Agent SDK MCP server。

迁移自 `tools/skill_loader.py:build_skill_loader_server`。行为字节级一致：

- 工具名固定 `skill_loader`，参数 `{skill_name: str, task_context: str}`
- description 来自 `dispatcher.get_description()`（渐进披露阶段一）
- @tool 主体调用 `await dispatcher.dispatch(skill_name, task_context)`，把字符串结果
  包成 `{"content":[{"type":"text","text":...}]}`

只在本模块 import claude_agent_sdk；其它 backend 部署不会触发本 import。
"""

from __future__ import annotations

import logging
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from evopaw.llm.claude_client import DEFAULT_SUB_AGENT_MODEL
from evopaw.session.models import MessageEntry

from ..dispatcher import ResultCallback, SkillDispatcher
from ..tool_schema import SKILL_TOOL_CLAUDE_MCP_ARGS, SKILL_TOOL_NAME

logger = logging.getLogger(__name__)


def build_skill_loader_server(
    session_id: str,
    routing_key: str = "",
    history_all: list[MessageEntry] | None = None,
    skills_dir: Path | None = None,
    sub_agent_model: str = DEFAULT_SUB_AGENT_MODEL,
    sub_agent_max_turns: int = 20,
    result_callback: ResultCallback | None = None,
    workspace_root: str = "/workspace",
):
    """工厂：构建 skill_loader MCP server，绑定当前 session。

    每次对话创建一个新的 dispatcher + MCP server 实例，绑定 session_id 和 history_all，
    实现会话隔离。

    Args:
        session_id: 当前 session ID
        routing_key: 飞书消息路由键（供 feishu_ops 等 Skill 使用）
        history_all: 完整对话历史（供 history_reader 内联读取）
        skills_dir: skills 目录路径（测试时覆盖）
        sub_agent_model: 任务型 Skill 的 Sub-Agent 模型（透传至 run_skill_agent）
        sub_agent_max_turns: Sub-Agent 最大对话轮次（透传至 run_skill_agent）
        result_callback: background skill 完成后的结果回调；缺省 None 时
            background 任务结果只写日志、不推送给用户。

    Returns:
        Claude Agent SDK MCP server 配置对象
    """
    dispatcher = SkillDispatcher(
        session_id=session_id,
        routing_key=routing_key,
        history_all=history_all,
        skills_dir=skills_dir,
        sub_agent_model=sub_agent_model,
        sub_agent_max_turns=sub_agent_max_turns,
        result_callback=result_callback,
        workspace_root=workspace_root,
    )
    description = dispatcher.get_description()

    @tool(
        SKILL_TOOL_NAME,
        description,
        SKILL_TOOL_CLAUDE_MCP_ARGS,
    )
    async def skill_loader(args):
        skill_name = args["skill_name"]
        task_context = args.get("task_context", "")
        result_text = await dispatcher.dispatch(skill_name, task_context)
        return {"content": [{"type": "text", "text": result_text}]}

    return create_sdk_mcp_server(
        "evopaw",
        tools=[skill_loader],
    )
