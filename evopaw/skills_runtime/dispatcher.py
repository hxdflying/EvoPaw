"""SkillDispatcher —— 跨 backend 共享的 Skill 分发器。

调度规则：

1. 未知 Skill → 友好错误（含可用列表）
2. `history_reader` → 内联从 `history_all` 分页读取，不创建 Sub-Agent
3. reference 型 → 返回 `<skill_instructions>...</skill_instructions>` 包裹的 SKILL.md
4. task 型 → 调用 `evopaw.agents.skill_agent.run_skill_agent(...)` 触发 Sub-Agent

返回类型统一为 `str`：OpenAI 路径直接塞回 `messages` 作为 `role=tool` content，
SDK MCP adapter 包成 `{"content":[{"type":"text","text":...}]}`。

task skill 在 SKILL.md frontmatter 显式声明 `execution.mode: background` 时，
走「立即返回 + 后台执行 + 完成后推送」路径。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from evopaw.llm.claude_client import DEFAULT_SUB_AGENT_MODEL
from evopaw.session.models import MessageEntry

from .instructions import _build_description_xml, _get_skill_instructions
from .registry import _build_skill_registry

logger = logging.getLogger(__name__)

# 后台任务完成后的结果回调，通常由 main_agent 注入 sender.send 闭包。
# dispatcher 只做兜底异常隔离，避免推送失败影响任务清理。
ResultCallback = Callable[[str, str, str], Awaitable[None]]

# Skills 目录默认值（与 tools/skill_loader.py 一致）
_SKILLS_DIR = Path(__file__).parents[1] / "skills"


def _handle_history_reader(
    history_all: list[MessageEntry],
    task_context: str,
) -> str:
    """内联处理 history_reader：从 history_all 分页读取。"""
    try:
        params = json.loads(task_context) if task_context.strip().startswith("{") else {}
    except (json.JSONDecodeError, Exception):
        params = {}

    page = max(1, int(params.get("page", 1)))
    page_size = max(1, min(50, int(params.get("page_size", 20))))

    total = len(history_all)
    total_pages = max(1, (total + page_size - 1) // page_size)

    start = (page - 1) * page_size
    end = start + page_size
    page_msgs = history_all[start:end]

    messages = [
        {"role": m.role, "content": m.content}
        for m in page_msgs
    ]

    result = {
        "errcode": 0,
        "message": f"成功读取第 {page} 页，共 {total} 条消息，本页 {len(messages)} 条",
        "data": {
            "messages": messages,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        },
    }
    return json.dumps(result, ensure_ascii=False)


def _normalize_task_context(task_context: Any) -> str:
    """把 task_context 统一为字符串：dict/list → json.dumps，None → ""。"""
    if isinstance(task_context, (dict, list)):
        return json.dumps(task_context, ensure_ascii=False)
    if task_context is None:
        return ""
    return str(task_context)


class SkillDispatcher:
    """跨 backend 共享的 skill 分发器。

    使用方式：
      ```
      dispatcher = SkillDispatcher(
          session_id="sid",
          routing_key="p2p:ou_xxx",
          history_all=history,
      )
      desc_xml = dispatcher.get_description()  # 渐进披露阶段一
      result = await dispatcher.dispatch("ref_skill", "")  # 同步调用
      ```

    线程安全性：单 dispatcher 实例对应单次主 Agent 轮次（与现有 MCP server 形态一致），
    instruction_cache 不需要外部锁。
    """

    def __init__(
        self,
        session_id: str,
        routing_key: str = "",
        history_all: list[MessageEntry] | None = None,
        skills_dir: Path | None = None,
        sub_agent_model: str = DEFAULT_SUB_AGENT_MODEL,
        sub_agent_max_turns: int = 20,
        result_callback: ResultCallback | None = None,
    ) -> None:
        self.session_id = session_id
        self.routing_key = routing_key
        self.history_all: list[MessageEntry] = list(history_all) if history_all else []
        self.skills_dir = skills_dir or _SKILLS_DIR
        self.sub_agent_model = sub_agent_model
        self.sub_agent_max_turns = sub_agent_max_turns
        # 后台任务完成时通过 callback 推送结果；foreground 路径不消费此字段。
        self.result_callback = result_callback

        self.registry = _build_skill_registry(self.skills_dir)
        self._instruction_cache: dict[str, str] = {}

    def get_description(self) -> str:
        """阶段一：返回 <available_skills> XML，作为 OpenAI tool description /
        SDK MCP description 注入。"""
        if not self.registry:
            return "SkillLoaderTool 已初始化，但暂无可用 Skill。"
        return _build_description_xml(self.registry, self.session_id)

    def list_skill_names(self) -> list[str]:
        return list(self.registry.keys())

    async def dispatch(self, skill_name: str, task_context: Any = "") -> str:
        """业务核心：把 (skill_name, task_context) 映射为字符串结果。

        返回值始终是字符串，便于不同 backend 用统一工具结果协议消费。
        """
        ctx_str = _normalize_task_context(task_context)

        # 未知 Skill
        if skill_name not in self.registry:
            available = list(self.registry.keys())
            return (
                f"错误：未找到 Skill '{skill_name}'。\n"
                f"可用 Skill：{available}\n"
                f"请从以上列表中选择正确的 skill_name 重新调用。"
            )

        skill_info = self.registry[skill_name]

        # 依赖 / 平台不满足时硬拦截，避免启动必然失败的 Sub-Agent。
        # 旧 registry 没有 available 字段时默认视为可用，保持向后兼容。
        if not skill_info.get("available", True):
            reason = skill_info.get("unavailable_reason", "未知原因")
            return (
                f"错误：Skill '{skill_name}' 当前不可用（{reason}）。\n"
                f"请联系管理员补齐依赖后重试，或换用其它 Skill。"
            )

        # history_reader 内联
        if skill_name == "history_reader":
            return _handle_history_reader(self.history_all, ctx_str)

        if skill_info["type"] == "reference":
            instructions = _get_skill_instructions(
                self.registry, skill_name, self.session_id,
                self.routing_key, self._instruction_cache,
            )
            return f"<skill_instructions>\n{instructions}\n</skill_instructions>"

        # task 型：触发 Sub-Agent
        instructions = _get_skill_instructions(
            self.registry, skill_name, self.session_id,
            self.routing_key, self._instruction_cache,
        )
        # Sub-Agent cwd 固定为 /workspace（容器内路径），与主 Agent 的 session_cwd
        # 不同。理由：
        #   1. Skill 脚本（pdf/docx/feishu_ops/scheduler_mgr 等）以及 SKILL.md
        #      指令普遍约定相对路径解析自 /workspace（如 .config/feishu.json、
        #      cron/tasks.json、sessions/{sid}/...）；改成 session_cwd 会让
        #      跨 session 的全局资源（凭证、cron 元数据）解析失败。
        #   2. Sub-Agent 跨 session 写共享数据（cron/、workspace/.config/）的
        #      场景由 docker-compose 把 workspace_dir 挂载到 /workspace 实现，
        #      session 隔离则由 SKILL.md 内显式拼 sessions/{session_dir}/ 实现。
        _workspace_root = "/workspace"

        # 延迟 import 避免循环（agents.skill_agent → claude SDK；让本模块保持纯逻辑）
        from evopaw.agents.skill_agent import _new_task_id, run_skill_agent  # noqa: PLC0415

        # 每次 task 分发都生成 task_id，用于日志、错误文本和后台任务取消。
        task_id = _new_task_id()

        execution_mode = skill_info.get("execution_mode", "foreground")

        # background 路径：立即注册任务并返回 task_id；完成后通过 callback 推送结果。
        if execution_mode == "background":
            return await self._spawn_background_task(
                skill_name=skill_name,
                instructions=instructions,
                task_context=ctx_str,
                workspace_root=_workspace_root,
                task_id=task_id,
                run_skill_agent=run_skill_agent,
            )

        logger.info(
            "dispatch task skill: skill=%s, routing_key=%s, session_id=%s, task_id=%s, mode=foreground",
            skill_name, self.routing_key, self.session_id, task_id,
        )

        result_text = await run_skill_agent(
            skill_name=skill_name,
            skill_instructions=instructions,
            task_context=ctx_str,
            session_path=_workspace_root,
            model=self.sub_agent_model,
            max_turns=self.sub_agent_max_turns,
            task_id=task_id,
        )
        return result_text

    async def _spawn_background_task(
        self,
        *,
        skill_name: str,
        instructions: str,
        task_context: str,
        workspace_root: str,
        task_id: str,
        run_skill_agent: Any,
    ) -> str:
        """把 task skill 放进后台运行，立即返回 task_id 提示给 LLM。

        - 用 `asyncio.create_task` spawn `_run_and_callback`；
        - 注册到进程级 `SubAgentRegistry`，让 `/stop` 能 cancel；
        - 任务自然结束 → 调用 `self.result_callback` 推送结果文本；
        - 任务被 cancel（CancelledError）→ 不调用 callback，仅记录日志；
        - 任意异常都被吞，避免影响 _spawn_background_task 自身的返回路径。
        """
        # 延迟 import 避免循环：sub_agent_registry 仅在 background 路径需要。
        from evopaw.agents.sub_agent_registry import (  # noqa: PLC0415
            get_default_registry,
        )

        registry = get_default_registry()
        callback = self.result_callback

        async def _run_and_callback() -> None:
            try:
                result_text = await run_skill_agent(
                    skill_name=skill_name,
                    skill_instructions=instructions,
                    task_context=task_context,
                    session_path=workspace_root,
                    model=self.sub_agent_model,
                    max_turns=self.sub_agent_max_turns,
                    task_id=task_id,
                )
            except asyncio.CancelledError:
                logger.info(
                    "[subagent#%s] background task cancelled (skill=%s, routing_key=%s)",
                    task_id, skill_name, self.routing_key,
                )
                raise
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[subagent#%s] background task crashed (skill=%s)",
                    task_id, skill_name,
                )
                result_text = (
                    f"⚠️ 后台任务 task#{task_id}（{skill_name}）执行失败，请稍后重试。"
                )

            if callback is None:
                logger.info(
                    "[subagent#%s] background task done but no result_callback configured; "
                    "result_text length=%d",
                    task_id, len(result_text),
                )
                return
            try:
                await callback(task_id, skill_name, result_text)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[subagent#%s] result_callback raised; result not delivered",
                    task_id, exc_info=True,
                )

        task = asyncio.create_task(_run_and_callback())

        async def _on_done(_t: asyncio.Task) -> None:
            try:
                await registry.unregister(self.routing_key, task_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[subagent#%s] registry.unregister failed", task_id, exc_info=True,
                )

        # asyncio.Task.add_done_callback 接受同步 callback；包一层 fire-and-forget。
        task.add_done_callback(
            lambda t: asyncio.create_task(_on_done(t)),
        )
        await registry.register(self.routing_key, task_id, task)

        logger.info(
            "dispatch task skill: skill=%s, routing_key=%s, session_id=%s, "
            "task_id=%s, mode=background",
            skill_name, self.routing_key, self.session_id, task_id,
        )

        return (
            f"已启动后台任务 task#{task_id}：{skill_name}。"
            "完成后我会在当前会话回复结果。"
        )
