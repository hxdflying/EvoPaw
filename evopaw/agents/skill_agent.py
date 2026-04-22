"""Sub-Agent — 任务型 Skill 执行层

使用 Claude Agent SDK query() 为每个任务型 Skill 创建短生命周期的 Sub-Agent。
SKILL.md 正文作为 system_prompt，允许 Bash/Read/Write/Edit/Grep/Glob 工具。
每次调用创建独立 query() session，防止状态污染。
"""

from __future__ import annotations

import logging

from claude_agent_sdk import (
    CLIConnectionError,
    CLINotFoundError,
    ResultMessage,
    query,
)

from evopaw.llm.claude_client import build_sub_agent_options

logger = logging.getLogger(__name__)


async def run_skill_agent(
    skill_name: str,
    skill_instructions: str,
    task_context: str,
    session_path: str,
) -> str:
    """运行任务型 Skill 的 Sub-Agent。

    每次调用创建独立的 query() session，防止状态污染。
    SKILL.md 正文 + execution_directive 作为 system_prompt。

    Args:
        skill_name: Skill 名称（用于日志）
        skill_instructions: SKILL.md 正文 + execution_directive（完整指令）
        task_context: 用户任务描述（作为 prompt）
        session_path: session workspace 路径（作为 cwd）

    Returns:
        Sub-Agent 的文本回复；异常时返回错误提示。
    """
    logger.info("run_skill_agent: skill=%s, cwd=%s", skill_name, session_path)

    options = build_sub_agent_options(
        system_prompt=skill_instructions,
        cwd=session_path,
    )

    try:
        final_text = ""
        async for message in query(prompt=task_context, options=options):
            if isinstance(message, ResultMessage):
                final_text = message.result
    except (CLINotFoundError, CLIConnectionError) as exc:
        logger.error("Skill '%s' SDK error: %s", skill_name, exc)
        return f"⚠️ Skill '{skill_name}' 执行失败：{exc}"
    except Exception:  # noqa: BLE001
        logger.exception("Skill '%s' unexpected error", skill_name)
        return f"⚠️ Skill '{skill_name}' 发生内部错误，请稍后重试。"

    if not final_text:
        return f"⚠️ Skill '{skill_name}' 未返回有效结果。"

    return final_text
