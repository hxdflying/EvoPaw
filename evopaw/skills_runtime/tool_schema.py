"""Skill tool schema 单一事实源。

本模块集中：
- 工具名 `skill_loader`
- 字段 properties（OpenAI / Anthropic 共用 JSON Schema 形态）
- claude_mcp 用的简化类型签名 `{name: type}`
- required 列表

description 仍然由 `SkillDispatcher.get_description()` 动态产生（含 `<available_skills>`），
不在这里硬编码。
"""

from __future__ import annotations

from typing import Any

SKILL_TOOL_NAME = "skill_loader"

SKILL_TOOL_PROPERTIES: dict[str, dict[str, str]] = {
    "skill_name": {
        "type": "string",
        "description": "要调用的 Skill 名称，必须来自 <available_skills> 列表。",
    },
    "task_context": {
        "type": "string",
        "description": (
            "传给 Skill 的任务上下文。task 类型 Skill 推荐 JSON 字符串；"
            "history_reader 接受 {\"page\":1,\"page_size\":20} 形式。"
        ),
    },
}

SKILL_TOOL_REQUIRED: list[str] = ["skill_name"]

# claude_agent_sdk @tool 接收 `{name: type}` 的 Python 类型形态，与 JSON Schema 不同。
SKILL_TOOL_CLAUDE_MCP_ARGS: dict[str, type] = {
    "skill_name": str,
    "task_context": str,
}


def build_input_schema() -> dict[str, Any]:
    """返回 JSON Schema dict（type:object + properties + required）。"""
    return {
        "type": "object",
        "properties": SKILL_TOOL_PROPERTIES,
        "required": SKILL_TOOL_REQUIRED,
    }
