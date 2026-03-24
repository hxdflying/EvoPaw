"""Main Crew — XiaoPaw 主协调 Crew（with Memory）

💡【第19课·@CrewBase + Hook】核心架构升级：
   _build_crew() 工厂函数 → MemoryAwareCrew @CrewBase 类

   为什么必须用 @CrewBase？
   @before_llm_call hook 只能绑定在 @CrewBase 类上。
   手动构造的 Crew 实例无法注册 hook。

💡【第19课·三层记忆叠加】
   L19 上下文生命周期层：
     - Bootstrap → workspace 文件注入 Agent.backstory
     - @before_llm_call → session 恢复 + prune + compress
     - ctx.json → 跨 session 压缩快照持久化
   L20 文件记忆层（通过 Skills）：memory-save / skill-creator / governance
   L21 搜索记忆层：每轮结束后 asyncio.create_task(async_index_turn)

历史注入（保留不变）：
   task.description 仍注入格式化历史，作为首次对话（无 ctx.json）的 fallback。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import yaml
from crewai import Agent, Crew, Process, Task
from crewai.agents.parser import AgentAction, AgentFinish
from crewai.hooks import LLMCallHookContext, before_llm_call
from crewai.project import CrewBase, agent, crew, task

from xiaopaw.agents.models import MainTaskOutput
from xiaopaw.llm.aliyun_llm import AliyunLLM
from xiaopaw.memory.bootstrap import build_bootstrap_prompt
from xiaopaw.memory.context_mgmt import (
    append_session_raw,
    load_session_ctx,
    maybe_compress,
    prune_tool_results,
    save_session_ctx,
)
from xiaopaw.memory.indexer import async_index_turn
from xiaopaw.models import SenderProtocol
from xiaopaw.runner import AgentFn
from xiaopaw.session.models import MessageEntry
from xiaopaw.tools.intermediate_tool import IntermediateTool

logger = logging.getLogger(__name__)

# SkillLoaderTool 在模块顶层导入，方便测试 mock
try:
    from xiaopaw.tools.skill_loader import SkillLoaderTool  # noqa: E402
except ImportError:
    SkillLoaderTool = None  # type: ignore[assignment,misc]

_CONFIG_DIR = Path(__file__).parent / "config"
_DEFAULT_MAX_HISTORY_TURNS = 20


# ── History formatting（保留不动）─────────────────────────────────


def _format_history(
    history: list[MessageEntry],
    max_turns: int = _DEFAULT_MAX_HISTORY_TURNS,
) -> str:
    """将对话历史格式化为 LLM 可读文本。（同原版）"""
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


# ── Verbose step_callback（保留不动）─────────────────────────────


def _make_step_callback(
    sender: SenderProtocol,
    routing_key: str,
    root_id: str,
) -> Any:
    async def callback(step: AgentAction | AgentFinish) -> None:
        if not isinstance(step, AgentAction):
            return
        thought = step.thought.strip()
        if not thought:
            return
        try:
            await sender.send(routing_key, f"💭 {thought}", root_id)
        except Exception:
            logger.warning("verbose callback: failed to send thought", exc_info=True)

    return callback


# ── YAML loader ──────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ── 核心：MemoryAwareCrew @CrewBase ──────────────────────────────


@CrewBase
class MemoryAwareCrew:
    """XiaoPaw 主 Crew，带三层记忆能力。

    💡【第19课·@CrewBase 必要性】手动构造 Crew 无法绑定 @before_llm_call hook，
    必须继承自 @CrewBase 装饰的类，hook 才能被 CrewAI 内部框架识别和调用。

    每次请求创建新实例（防止 CrewAI 内部状态污染跨 session）。
    """

    def __init__(
        self,
        session_id:    str,
        user_message:  str,
        routing_key:   str,
        workspace_dir: Path,
        ctx_dir:       Path,
        db_dsn:        str,
        step_callback: Any | None,
        verbose:       bool,
        history_all:   list,
        sandbox_url:   str,
        prune_keep_turns: int = 10,
    ) -> None:
        self.session_id      = session_id
        self.user_message    = user_message
        self.routing_key     = routing_key
        self._workspace_dir  = workspace_dir
        self._ctx_dir        = ctx_dir
        self._db_dsn         = db_dsn
        self._step_callback  = step_callback
        self._verbose        = verbose
        self._history_all    = history_all
        self._sandbox_url    = sandbox_url
        self._prune_keep_turns = prune_keep_turns

        self._session_loaded = False
        self._last_msgs: list[dict] = []
        self._history_len    = 0
        self._turn_start_ts  = int(time.time() * 1000)

    @agent
    def orchestrator(self) -> Agent:
        cfg = dict(_load_yaml(_CONFIG_DIR / "agents.yaml")["orchestrator"])

        # 💡【第19课·Bootstrap】动态构建 backstory，覆盖 YAML 静态内容
        cfg["backstory"] = build_bootstrap_prompt(self._workspace_dir)

        tools: list = []
        if SkillLoaderTool is not None:
            loader_kwargs: dict = {"session_id": self.session_id}
            if self.routing_key:
                loader_kwargs["routing_key"] = self.routing_key
            if self._history_all is not None:
                loader_kwargs["history_all"] = self._history_all
            if self._sandbox_url:
                loader_kwargs["sandbox_url"] = self._sandbox_url
            tools.append(SkillLoaderTool(**loader_kwargs))
        else:
            logger.warning("SkillLoaderTool not available")
        tools.append(IntermediateTool())

        return Agent(
            **cfg,
            llm     = AliyunLLM(model="qwen3-max", region="cn", temperature=0.3),
            tools   = tools,
            verbose = True,
        )

    @task
    def main_task(self) -> Task:
        task_cfg = dict(_load_yaml(_CONFIG_DIR / "tasks.yaml")["main_task"])
        return Task(
            **task_cfg,
            agent          = self.orchestrator(),
            output_pydantic = MainTaskOutput,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents        = self.agents,
            tasks         = self.tasks,
            process       = Process.sequential,
            verbose       = True,
            step_callback = self._step_callback,
        )

    # ── @before_llm_call Hook ────────────────────────────────────

    @before_llm_call
    def before_llm_hook(self, context: LLMCallHookContext) -> bool | None:
        """每次 LLM 调用前拦截 context.messages，做三件事：
        ① 首次：从 ctx.json 恢复历史 session
        ② 每次：prune 旧 tool result（in-place）
        ③ 每次：超阈值时 compress（in-place）

        💡 为什么不用 @after_llm_call 做持久化：
        CrewAI 的 after hook 存在时会把 answer 强制 str()，
        导致 executor 的 isinstance(answer, list) 判断失败，工具不执行。
        因此持久化改在 kickoff() 返回后（run_and_index 中）统一完成。
        """
        if not self._session_loaded:
            self._restore_session(context)
            self._session_loaded = True

        # 保存引用：run_and_index 结束后 _last_msgs 就是含最终回答的完整消息列表
        self._last_msgs = context.messages

        prune_tool_results(context.messages, keep_turns=self._prune_keep_turns)
        maybe_compress(context.messages, context)
        return None

    def _restore_session(self, context: LLMCallHookContext) -> None:
        """从 ctx.json 恢复历史 LLM messages，追加本轮 user 消息。

        💡【重建顺序】当前 system（新 bootstrap） → 历史对话 → 当前 user
            - 当前 system 消息：CrewAI 根据最新 backstory 注入，必须保留。
              若直接用 ctx.json 覆盖，旧 backstory 会替换掉最新的 Bootstrap 内容。
            - 历史中的旧 role-definition system 消息过滤掉（已被新 backstory 替代）。
            - <context_summary> 压缩摘要（也是 system role）须保留，否则丢失压缩历史。
        """
        history = load_session_ctx(self.session_id, ctx_dir=self._ctx_dir)

        if not history:
            self._history_len = 0
            return  # 全新 session，无历史可恢复

        # 当前轮：从 CrewAI 注入的 context 提取 system 消息（新 bootstrap）和 user 消息
        current_system_msgs = [m for m in context.messages if m.get("role") == "system"]
        current_user_msg    = next(
            (m for m in reversed(context.messages) if m.get("role") == "user"),
            {},
        )

        # 历史消息：过滤旧 role-definition system，保留 <context_summary> 压缩摘要
        hist_conv = [
            m for m in history
            if m.get("role") != "system"
            or "<context_summary>" in str(m.get("content", ""))
        ]

        # _history_len 用于 run_and_index 切出本轮新增消息写 raw.jsonl：
        # 重建后 context.messages = current_system_msgs + hist_conv + [user]，
        # 新消息从 len(current_system_msgs) + len(hist_conv) 位置开始。
        self._history_len = len(current_system_msgs) + len(hist_conv)

        context.messages.clear()
        context.messages.extend(current_system_msgs)  # 最新 bootstrap backstory
        context.messages.extend(hist_conv)             # 历史对话 + 压缩摘要
        if current_user_msg:
            context.messages.append(current_user_msg)

    # ── 主入口 ───────────────────────────────────────────────────

    async def run_and_index(self) -> str:
        """执行一轮对话，结束后：
        ① 保存 ctx.json（下次 session 恢复用）
        ② asyncio.create_task() 异步建索引（不阻塞返回）

        💡 _history_len 追踪：切出本轮新增消息写 raw.jsonl
        """
        result = await self.crew().akickoff(
            inputs={
                "user_message": self.user_message,
                "history":      _format_history(self._history_all),
            }
        )

        # ① 持久化 ctx.json + raw.jsonl
        if self._last_msgs:
            new_msgs = list(self._last_msgs)[self._history_len:]
            append_session_raw(self.session_id, new_msgs, ctx_dir=self._ctx_dir)
            save_session_ctx(self.session_id, list(self._last_msgs), ctx_dir=self._ctx_dir)

        # ② 提取 reply
        assistant_reply = result.raw or str(result)
        if result.pydantic and hasattr(result.pydantic, "reply"):
            assistant_reply = str(result.pydantic.reply)

        # ③ 异步建索引（不阻塞主流程返回）
        if self._db_dsn:
            # 💡 必须持有 task 引用：未持有的任务可能被 GC 取消，
            #    Python 3.12+ 会打印 "Task was destroyed but it is pending!" 警告。
            _task = asyncio.create_task(  # noqa: RUF006
                async_index_turn(
                    session_id      = self.session_id,
                    routing_key     = self.routing_key,
                    user_message    = self.user_message,
                    assistant_reply = assistant_reply,
                    turn_ts         = self._turn_start_ts,
                    db_dsn          = self._db_dsn,
                )
            )

        return assistant_reply


# ── 公共工厂（新签名，兼容 Runner）─────────────────────────────


def build_agent_fn(
    sender:           SenderProtocol,
    workspace_dir:    Path,
    ctx_dir:          Path,
    db_dsn:           str    = "",
    max_history_turns: int   = _DEFAULT_MAX_HISTORY_TURNS,
    sandbox_url:       str   = "",
) -> AgentFn:
    """工厂：返回 Runner 可用的 agent_fn 闭包。

    Args:
        sender:        Feishu Sender（verbose 模式推送推理过程）
        workspace_dir: Bootstrap 读取的 workspace 目录
        ctx_dir:       ctx.json / raw.jsonl 存储目录
        db_dsn:        pgvector 连接串（空字符串时跳过搜索记忆索引）
        max_history_turns: 注入 task 的最大历史条数
        sandbox_url:   AIO-Sandbox MCP 端点 URL
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
        step_cb = (
            _make_step_callback(sender, routing_key, root_id) if verbose else None
        )
        crew_instance = MemoryAwareCrew(
            session_id    = session_id,
            user_message  = user_message,
            routing_key   = routing_key,
            workspace_dir = workspace_dir,
            ctx_dir       = ctx_dir,
            db_dsn        = db_dsn,
            step_callback = step_cb,
            verbose       = verbose,
            history_all   = history,
            sandbox_url   = sandbox_url,
        )
        return await crew_instance.run_and_index()

    return agent_fn
