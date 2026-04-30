"""Sub-Agent — 任务型 Skill 执行层

使用 Claude Agent SDK query() 为每个任务型 Skill 创建短生命周期的 Sub-Agent。
SKILL.md 正文作为 system_prompt，允许 Bash/Read/Write/Edit/Grep/Glob 工具。
每次调用创建独立 query() session，防止状态污染。

每次调用关联一个 8 字符 hex task_id，用于日志前缀（`[subagent#xxxxxxxx]`）
和错误回执（`task#xxxxxxxx`），便于定位和取消任务。
注意：`SkillDispatcher.dispatch -> str` 不变，task_id 仅在文本/日志中暴露。
"""

from __future__ import annotations

import logging
import uuid

from claude_agent_sdk import (
    CLIConnectionError,
    CLINotFoundError,
    ResultMessage,
    query,
)

from evopaw.llm.claude_client import DEFAULT_SUB_AGENT_MODEL, build_sub_agent_options

logger = logging.getLogger(__name__)


def _new_task_id() -> str:
    """生成 8 字符 hex task_id（来自 uuid4，不保证强随机但碰撞概率足够低）。"""
    return uuid.uuid4().hex[:8]


async def run_skill_agent(
    skill_name: str,
    skill_instructions: str,
    task_context: str,
    session_path: str,
    model: str = DEFAULT_SUB_AGENT_MODEL,
    max_turns: int = 20,
    task_id: str | None = None,
) -> str:
    """运行任务型 Skill 的 Sub-Agent。

    每次调用创建独立的 query() session，防止状态污染。
    SKILL.md 正文 + execution_directive 作为 system_prompt。

    Args:
        skill_name: Skill 名称（用于日志）
        skill_instructions: SKILL.md 正文 + execution_directive（完整指令）
        task_context: 用户任务描述（作为 prompt）
        session_path: session workspace 路径（作为 cwd）
        model: Sub-Agent 使用的模型
        max_turns: Sub-Agent 最大对话轮次
        task_id: 可选的 8 字符 hex 任务 id；缺省自动生成。dispatcher 透传时
            会传入同一个 id，便于跨日志追踪。

    Returns:
        Sub-Agent 的文本回复；异常时返回带 `task#xxxxxxxx` 的错误提示。
    """
    tid = task_id or _new_task_id()
    log_prefix = f"[subagent#{tid}]"
    logger.info(
        "%s run_skill_agent: skill=%s, cwd=%s, model=%s, max_turns=%d",
        log_prefix, skill_name, session_path, model, max_turns,
    )

    options = build_sub_agent_options(
        system_prompt=skill_instructions,
        cwd=session_path,
        model=model,
        max_turns=max_turns,
    )

    try:
        final_text = ""
        async for message in query(prompt=task_context, options=options):
            if isinstance(message, ResultMessage):
                final_text = message.result
    except (CLINotFoundError, CLIConnectionError) as exc:
        logger.error("%s Skill '%s' SDK error: %s", log_prefix, skill_name, exc)
        return f"⚠️ Skill '{skill_name}' (task#{tid}) 执行失败：{exc}"
    except Exception:  # noqa: BLE001
        logger.exception("%s Skill '%s' unexpected error", log_prefix, skill_name)
        return f"⚠️ Skill '{skill_name}' (task#{tid}) 发生内部错误，请稍后重试。"

    if not final_text:
        return f"⚠️ Skill '{skill_name}' (task#{tid}) 未返回有效结果。"

    return final_text
