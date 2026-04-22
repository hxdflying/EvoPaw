"""Main Agent — 使用 Claude Agent SDK 的主 Agent 入口

Phase 7：集成三层记忆（Bootstrap + ctx.json + pgvector）。
SDK 每次 query 是独立 session，历史通过 _format_history() 拼入 prompt。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from claude_agent_sdk import (
    CLIConnectionError,
    CLINotFoundError,
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

from evopaw.agents.hooks import build_verbose_hooks
from evopaw.llm.claude_client import build_main_agent_options
from evopaw.memory.bootstrap import build_bootstrap_prompt
from evopaw.memory.context_mgmt import (
    append_session_raw,
    load_session_ctx,
    maybe_compress,
    save_session_ctx,
)
from evopaw.memory.indexer import async_index_turn
from evopaw.models import SenderProtocol
from evopaw.runner import AgentFn
from evopaw.session.models import MessageEntry
from evopaw.tools.add_image_tool_local import extract_image_path, load_image_for_claude
from evopaw.tools.skill_loader import build_skill_loader_server

logger = logging.getLogger(__name__)

_DEFAULT_MAX_HISTORY_TURNS = 20


def _format_ctx_summaries(ctx_messages: list[dict]) -> str:
    """将 ctx.json 中的摘要格式化为可注入 prompt 的文本。"""
    summaries = []
    for msg in ctx_messages:
        content = msg.get("content", "")
        if "<context_summary>" in content:
            summaries.append(content)
    if not summaries:
        return ""
    return "\n".join(summaries)


def _format_history(
    history: list[MessageEntry],
    max_turns: int = _DEFAULT_MAX_HISTORY_TURNS,
) -> str:
    """将对话历史格式化为 LLM 可读文本。"""
    if not history:
        return "（无历史记录）"

    truncated = len(history) > max_turns
    recent = history[-max_turns:] if truncated else history

    role_map = {"user": "用户", "assistant": "助手"}
    lines = [
        f"{role_map.get(entry.role, entry.role)}: {entry.content}"
        for entry in recent
    ]

    if truncated:
        omitted = len(history) - max_turns
        lines.insert(0, f"（已省略更早的 {omitted} 条消息。如需查阅，可通过 history_reader Skill 按页读取完整历史。）")

    return "\n".join(lines)


def build_agent_fn(
    sender:            SenderProtocol,
    workspace_dir:     Path,
    ctx_dir:           Path,
    db_dsn:            str   = "",
    max_history_turns: int   = _DEFAULT_MAX_HISTORY_TURNS,
    planner_model:     str   = "claude-sonnet-4-6",
    agent_max_turns:   int   = 50,
) -> AgentFn:
    """工厂：返回 Runner 可用的 agent_fn 闭包。

    Args:
        sender:            Feishu Sender（verbose 模式推送推理过程）
        workspace_dir:     Bootstrap 读取的 workspace 目录
        ctx_dir:           ctx.json / raw.jsonl 存储目录
        db_dsn:            pgvector 连接串（为空时跳过索引）
        max_history_turns: 注入 prompt 的最大历史条数
        planner_model:     主 Agent 模型名称
        agent_max_turns:   主 Agent 最大对话轮次
    """
    ctx_dir.mkdir(parents=True, exist_ok=True)

    async def agent_fn(
        user_message: str,
        history:      list[MessageEntry],
        session_id:   str,
        routing_key:  str  = "",
        root_id:      str  = "",
        verbose:      bool = False,
    ) -> str:
        logger.info(
            "agent_fn called: session=%s, msg=%s",
            session_id, user_message[:50],
        )

        # 1. 构建 system prompt
        system_prompt = build_bootstrap_prompt(workspace_dir)

        # 1b. 注入工具约束：禁止使用 Claude CLI 内置 skill，只用 skill_loader
        system_prompt += (
            "\n\n<tool_constraint>\n"
            "你唯一的外部能力接口是 skill_loader MCP 工具。"
            "严禁使用 Claude Code CLI 的任何内置 skill（如 schedule、loop、init、update-config 等）。"
            "所有能力（搜索、定时任务、文件处理、记忆管理等）都通过 skill_loader 调用对应 Skill。"
            "例如：创建定时任务 → skill_loader(skill_name='scheduler_mgr', ...)。"
            "\n</tool_constraint>"
        )

        # 2. 加载 ctx.json 摘要（长期上下文恢复）
        ctx_messages = load_session_ctx(session_id, ctx_dir)
        ctx_summary_text = _format_ctx_summaries(ctx_messages)

        # 3. 拼接历史 + 用户消息（含图片多模态检测）
        history_text = _format_history(history, max_turns=max_history_turns)
        text_parts = []
        if ctx_summary_text:
            text_parts.append(f"<long_term_context>\n{ctx_summary_text}\n</long_term_context>")
        text_parts.append(f"<conversation_history>\n{history_text}\n</conversation_history>")
        text_parts.append(user_message)
        full_message = "\n\n".join(text_parts)

        # 3b. 检测图片附件，构建多模态 prompt
        image_path = extract_image_path(user_message)
        if image_path:
            # 将沙盒路径转换为本地路径
            local_path = str(workspace_dir / image_path.removeprefix("/workspace/"))
            image_block = load_image_for_claude(local_path, workspace_root=workspace_dir)
            if image_block is not None:
                # Claude SDK 多模态 prompt：list of content blocks
                full_message = [
                    {"type": "text", "text": full_message},
                    image_block,
                ]

        # 4. 构建 session workspace 目录
        session_cwd = workspace_dir / "sessions" / session_id
        session_cwd.mkdir(parents=True, exist_ok=True)

        # 5. 构建 skill_loader MCP server（绑定当前 session）
        skill_server = build_skill_loader_server(
            session_id=session_id,
            routing_key=routing_key,
            history_all=history,
        )

        # 6. 构建 options（verbose 且非 thread 时推送飞书）
        if verbose:
            verbose_cb = None
            if not routing_key.startswith("thread:"):
                async def verbose_cb(text: str) -> None:
                    await sender.send_text(routing_key, text, root_id)
            hooks = build_verbose_hooks(callback=verbose_cb)
        else:
            hooks = {}
        options = build_main_agent_options(
            system_prompt=system_prompt,
            cwd=str(session_cwd),
            model=planner_model,
            max_turns=agent_max_turns,
            hooks=hooks,
            mcp_servers={"evopaw": skill_server},
        )

        # 7. 调用 Claude Agent SDK
        try:
            final_text = ""
            async for message in query(prompt=full_message, options=options):
                if isinstance(message, ResultMessage):
                    final_text = message.result
        except (CLINotFoundError, CLIConnectionError) as exc:
            logger.error("Claude SDK error: %s", exc)
            return f"⚠️ Claude 调用失败：{exc}"
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error in agent_fn")
            return "⚠️ Agent 发生内部错误，请稍后重试。"

        if not final_text:
            return "⚠️ Claude 未返回有效回复，请重试。"

        # 8. 持久化：ctx.json 快照 + raw.jsonl 审计日志
        turn_messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": final_text},
        ]
        try:
            # 保留已有摘要 + 追加本轮，超阈值时 in-place 压缩
            updated_ctx = ctx_messages + turn_messages
            maybe_compress(updated_ctx)
            save_session_ctx(session_id, updated_ctx, ctx_dir)
            append_session_raw(session_id, turn_messages, ctx_dir)
        except Exception:  # noqa: BLE001
            logger.warning("ctx persistence failed for session=%s", session_id, exc_info=True)

        # 9. 异步写入 pgvector（不阻塞主流程）
        if db_dsn:
            turn_ts = int(time.time() * 1000)
            asyncio.create_task(
                async_index_turn(
                    session_id=session_id,
                    routing_key=routing_key,
                    user_message=user_message,
                    assistant_reply=final_text,
                    turn_ts=turn_ts,
                    db_dsn=db_dsn,
                )
            )

        return final_text

    return agent_fn
