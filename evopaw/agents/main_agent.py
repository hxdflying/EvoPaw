"""Main Agent — 主 Agent 入口。

本模块负责拼装 system prompt、历史上下文、附件内容、Skill 调度入口、
provider backend 请求，以及会话上下文和语义记忆的写入。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from evopaw.agent_backends import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderMaxTurnsExceeded,
    ProviderRateLimited,
    ProviderTransientError,
    ProviderUnknownError,
    TurnRequest,
    get_backend,
)
from evopaw.agents.hooks import FeishuStreamSink
from evopaw.agents.response_finalizer import (
    CompositeResponseFinalizer,
    ResponseFinalizeContext,
    ResponseFinalizer,
)
from evopaw.content_builders import pick_content_builder
from evopaw.memory.bootstrap import build_bootstrap_prompt
from evopaw.memory.context_mgmt import (
    append_session_raw,
    load_session_ctx,
    maybe_compress,
    save_session_ctx,
)
from evopaw.memory.indexer import async_index_turn
from evopaw.models import SenderProtocol
from evopaw.provider_runtime import ResolvedRuntime
from evopaw.runner import AgentFn
from evopaw.session.models import MessageEntry
from evopaw.skills_runtime.adapters.claude_mcp import build_skill_loader_server
from evopaw.skills_runtime.dispatcher import SkillDispatcher
from evopaw.tools.add_image_tool_local import extract_image_path, load_image_data

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
    sender:              SenderProtocol,
    workspace_dir:       Path,
    ctx_dir:             Path,
    *,
    main_runtime:        ResolvedRuntime,
    sub_runtime:         ResolvedRuntime,
    db_dsn:              str  = "",
    max_history_turns:   int  = _DEFAULT_MAX_HISTORY_TURNS,
    agent_max_turns:     int  = 50,
    sub_agent_max_turns: int  = 20,
    agent_timeout_s:     float = 120.0,
    agent_max_tokens:    int | None = None,
    agent_temperature:   float | None = None,
    agent_top_p:         float | None = None,
    response_finalizer:  ResponseFinalizer | None = None,
) -> AgentFn:
    """工厂：返回 Runner 可用的 agent_fn 闭包。

    Args:
        sender:              Feishu Sender（verbose 模式推送推理过程）
        workspace_dir:       Bootstrap 读取的 workspace 目录
        ctx_dir:             ctx.json / raw.jsonl 存储目录
        main_runtime:        主 Agent 的 ResolvedRuntime（必填，由 resolve_runtime 产生）
        sub_runtime:         Sub-Agent 的 ResolvedRuntime（必填）
        db_dsn:              pgvector 连接串（为空时跳过索引）
        max_history_turns:   注入 prompt 的最大历史条数
        agent_max_turns:     主 Agent 最大对话轮次
        sub_agent_max_turns: Sub-Agent 最大对话轮次
        agent_timeout_s:     HTTP backend 单次请求超时秒数（claude_sdk_compat 不消费）
        agent_max_tokens:    通用 generation 参数；HTTP backend 消费，None=backend 默认
        agent_temperature:   通用 generation 参数；HTTP backend 消费，None=不下发
        agent_top_p:         通用 generation 参数；HTTP backend 消费，None=不下发
        response_finalizer:  最终回复改写器；None 时使用空 Composite，
                             等价于直接返回原 final_text。verbose 关闭时仍执行。
    """
    ctx_dir.mkdir(parents=True, exist_ok=True)
    finalizer: ResponseFinalizer = response_finalizer or CompositeResponseFinalizer()

    # Sub-Agent 模型名以 resolver 产出的 sub_runtime 为准。
    effective_sub_model = sub_runtime.model

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
            extra={"routing_key": routing_key, "session_id": session_id},
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

        # 3b. 检测图片附件，并按 provider schema 构建 content blocks。
        # 不支持 vision 的 runtime 降级为纯文本提示，避免构建非法 image block。
        builder = pick_content_builder(main_runtime.runtime_family)
        image_b64: str | None = None
        image_mime: str | None = None
        image_path = extract_image_path(user_message)
        if image_path:
            if main_runtime.supports_vision:
                local_path = str(workspace_dir / image_path.removeprefix("/workspace/"))
                data = load_image_data(local_path, workspace_root=workspace_dir)
                if data is not None:
                    image_b64, image_mime = data
            else:
                logger.info(
                    "image attachment dropped: runtime %s/%s does not support vision",
                    main_runtime.provider_id, main_runtime.model,
                    extra={"routing_key": routing_key, "session_id": session_id},
                )
                full_message += (
                    f"\n\n[附件图片：{image_path}，当前模型不支持图像理解，"
                    "已降级为纯文本——请基于其它上下文回答；如需识图请切换支持 vision 的 provider。]"
                )
        user_content: str | list[dict] = builder.build_user_content(
            full_message, image_b64=image_b64, mime_type=image_mime,
        )

        # 4. 构建 session workspace 目录
        session_cwd = workspace_dir / "sessions" / session_id
        session_cwd.mkdir(parents=True, exist_ok=True)

        # 5. 按 runtime_family 准备 backend_hints：
        #    - claude_sdk_compat: 注入 SDK MCP server。
        #    - openai_chat / anthropic_messages: 注入纯逻辑 SkillDispatcher。
        # background task skill 完成后直接推送结果，不回注 main agent context。
        # 闭包捕获 sender / routing_key / root_id；SDK / HTTP 两条路径共用同一个。
        async def _bg_result_callback(
            task_id: str, skill_name: str, result_text: str,
        ) -> None:
            msg = (
                f"📌 后台任务 task#{task_id}（{skill_name}）已完成：\n\n"
                f"{result_text}"
            )
            try:
                await sender.send(routing_key, msg, root_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "background result push failed (task_id=%s, skill=%s)",
                    task_id, skill_name,
                    extra={"routing_key": routing_key, "session_id": session_id},
                    exc_info=True,
                )

        # Sub-Agent cwd / requires.files 解析的 workspace 根。
        # 容器：workspace_dir 通常就是 /workspace，本机直跑时是仓库下的真实路径，
        # 两种场景统一从这里透传，避免 dispatcher 硬编码 /workspace 在本机失真。
        skills_workspace_root = str(workspace_dir)
        backend_hints: dict[str, object] = {}
        if main_runtime.runtime_family == "claude_sdk_compat":
            skill_server = build_skill_loader_server(
                session_id=session_id,
                routing_key=routing_key,
                history_all=history,
                sub_agent_model=effective_sub_model,
                sub_agent_max_turns=sub_agent_max_turns,
                result_callback=_bg_result_callback,
                workspace_root=skills_workspace_root,
            )
            backend_hints = {"mcp_servers": {"evopaw": skill_server}}
        elif main_runtime.runtime_family in ("openai_chat", "anthropic_messages"):
            dispatcher = SkillDispatcher(
                session_id=session_id,
                routing_key=routing_key,
                history_all=history,
                sub_agent_model=effective_sub_model,
                sub_agent_max_turns=sub_agent_max_turns,
                result_callback=_bg_result_callback,
                workspace_root=skills_workspace_root,
            )
            backend_hints = {"skill_dispatcher": dispatcher}
        else:
            raise ValueError(
                f"未支持的 runtime_family: {main_runtime.runtime_family!r}（"
                f"当前支持 claude_sdk_compat / openai_chat / anthropic_messages）"
            )

        # 6. verbose 模式下构建 StreamSink（thread 场景与 sub-agent 不推送）
        stream_sink = None
        if verbose and not routing_key.startswith("thread:"):
            async def _send(text: str) -> None:
                await sender.send_text(routing_key, text, root_id)
            stream_sink = FeishuStreamSink(send=_send)

        # 7. 构造 TurnRequest 调用 backend
        req = TurnRequest(
            role="main",
            runtime=main_runtime,
            system_prompt=system_prompt,
            user_content=user_content,
            cwd=str(session_cwd),
            max_turns=agent_max_turns,
            timeout_s=agent_timeout_s,
            max_tokens=agent_max_tokens,
            temperature=agent_temperature,
            top_p=agent_top_p,
            stream_sink=stream_sink,
            backend_hints=backend_hints,
        )

        try:
            backend = get_backend(main_runtime)
            result = await backend.run_turn(req)
            final_text = result.text
            skills_called = list(result.skills_called)
        except ProviderAuthError as exc:
            # 401/403：凭证错误是配置问题，不可重试，向上层暴露 provider 信息。
            logger.error(
                "Backend auth error (provider=%s): %s",
                main_runtime.provider_id, exc,
                extra={"routing_key": routing_key, "session_id": session_id},
            )
            return (
                f"⚠️ {main_runtime.provider_id} 凭证无效或未授权，请联系管理员检查 API Key。"
            )
        except ProviderRateLimited as exc:
            logger.warning(
                "Backend rate limited (provider=%s): %s",
                main_runtime.provider_id, exc,
                extra={"routing_key": routing_key, "session_id": session_id},
            )
            return f"⚠️ {main_runtime.provider_id} 被限流，请稍后重试。"
        except ProviderInvalidRequest as exc:
            # 4xx 非鉴权类：通常是请求体 / 模型不支持的字段，重试无意义。
            logger.error(
                "Backend invalid request (provider=%s model=%s): %s",
                main_runtime.provider_id, main_runtime.model, exc,
                extra={"routing_key": routing_key, "session_id": session_id},
            )
            return f"⚠️ 请求被 provider 拒绝（{exc}）。"
        except ProviderMaxTurnsExceeded as exc:
            # 工具调用循环耗尽，与「provider 返回空回复」分开提示。
            logger.warning(
                "Backend max_turns exceeded (provider=%s max_turns=%s): %s",
                main_runtime.provider_id, agent_max_turns, exc,
                extra={"routing_key": routing_key, "session_id": session_id},
            )
            return (
                f"⚠️ Agent 工具调用轮次达到上限（max_turns={agent_max_turns}），"
                "请缩小任务范围或在配置里提高 agent.max_turns。"
            )
        except ProviderTransientError as exc:
            logger.error(
                "Backend transient error: %s", exc,
                extra={"routing_key": routing_key, "session_id": session_id},
            )
            return f"⚠️ {main_runtime.provider_id} 调用失败：{exc}"
        except ProviderUnknownError:
            logger.exception(
                "Backend unexpected error",
                extra={"routing_key": routing_key, "session_id": session_id},
            )
            return "⚠️ Agent 发生内部错误，请稍后重试。"
        except Exception:  # noqa: BLE001
            logger.exception(
                "Unexpected error in agent_fn",
                extra={"routing_key": routing_key, "session_id": session_id},
            )
            return "⚠️ Agent 发生内部错误，请稍后重试。"

        # 7a. Response Finalizer pipeline
        # 在空文本判断之前执行：finalizer 可能把空文本补成默认提示，也可能把非空文本
        # redact 成空（虽然不推荐）；二者都应当被 7b 的空文本检查覆盖。
        # finalizer 本身有 try/except 兜底，CompositeResponseFinalizer 单 finalizer
        # 抛错时沿用上一步文本，所以这里不再额外包 try。
        try:
            final_text = await finalizer.finalize(
                final_text,
                ResponseFinalizeContext(
                    session_id=session_id,
                    routing_key=routing_key,
                    root_id=root_id,
                    skills_called=skills_called,
                    role="main",
                ),
            )
        except Exception:  # noqa: BLE001
            # 双保险：finalizer 协议要求自己不抛错；若实现违反协议，仍不应崩主流程。
            logger.warning(
                "response_finalizer.finalize raised; using original text",
                extra={"routing_key": routing_key, "session_id": session_id},
                exc_info=True,
            )

        if not final_text:
            return "⚠️ Claude 未返回有效回复，请重试。"

        # 上报当前轮次的 skills_called 到 sender（仅 CaptureSender 实现，TestAPI 用）。
        record_skills = getattr(sender, "record_skills", None)
        if record_skills is not None and root_id:
            try:
                record_skills(root_id, skills_called)
            except Exception:  # noqa: BLE001
                logger.warning("record_skills failed for root_id=%s", root_id, exc_info=True)

        # 8. 持久化：ctx.json 快照 + raw.jsonl 审计日志
        turn_messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": final_text},
        ]
        try:
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
