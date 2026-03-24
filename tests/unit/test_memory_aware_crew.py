"""MemoryAwareCrew 单元测试

💡【第19课·@before_llm_call Hook】测试核心改造点：
  1. hook 首次调用：从 ctx.json 恢复历史 messages
  2. hook 重复调用：不重复恢复（_session_loaded 标志）
  3. hook 每次调用：触发 prune 和 maybe_compress
  4. run_and_index：完成后写 ctx.json + 触发 asyncio.create_task
  5. build_agent_fn：新签名包含 workspace_dir / ctx_dir / db_dsn
  6. _restore_session：保留当前 system 消息（CrewAI 注入的新 backstory）

# 注意：asyncio_mode = "auto" 已在 pyproject.toml 全局开启，
# async def test_* 方法无需显式 @pytest.mark.asyncio。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xiaopaw.memory.context_mgmt import save_session_ctx


# ── helpers ─────────────────────────────────────────────────────


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    for name in ["soul.md", "user.md", "agent.md", "memory.md"]:
        (ws / name).write_text(f"# {name}", encoding="utf-8")
    return ws


def _make_hook_context(messages: list[dict]) -> MagicMock:
    ctx = MagicMock()
    ctx.messages = messages
    return ctx


def _make_crew_instance(tmp_path: Path, db_dsn: str = "") -> "MemoryAwareCrew":  # noqa: F821
    """构造 MemoryAwareCrew 实例（mock 掉 LLM/Tool，只测 hook 逻辑）"""
    from xiaopaw.agents.main_crew import MemoryAwareCrew  # noqa: PLC0415

    workspace = _make_workspace(tmp_path)
    ctx_dir = tmp_path / "ctx"
    ctx_dir.mkdir()

    with patch("xiaopaw.agents.main_crew.AliyunLLM"), \
         patch("xiaopaw.agents.main_crew.SkillLoaderTool"), \
         patch("xiaopaw.agents.main_crew.IntermediateTool"):
        return MemoryAwareCrew(
            session_id    = "s-test",
            user_message  = "测试消息",
            routing_key   = "p2p:ou_test",
            workspace_dir = workspace,
            ctx_dir       = ctx_dir,
            db_dsn        = db_dsn,
            step_callback = None,
            verbose       = False,
            history_all   = [],
            sandbox_url   = "",
        )


# ── @before_llm_call hook ───────────────────────────────────────


class TestBeforeLlmHook:
    def test_first_call_loads_session_and_sets_flag(self, tmp_path):
        """首次调用：_session_loaded 变为 True"""
        crew = _make_crew_instance(tmp_path)
        context = _make_hook_context([{"role": "user", "content": "消息"}])
        with patch("xiaopaw.agents.main_crew.prune_tool_results"), \
             patch("xiaopaw.agents.main_crew.maybe_compress"):
            crew.before_llm_hook(context)
        assert crew._session_loaded is True

    def test_second_call_skips_restore(self, tmp_path):
        """第二次调用不重复恢复（_session_loaded=True）"""
        crew = _make_crew_instance(tmp_path)
        crew._session_loaded = True

        with patch.object(crew, "_restore_session") as mock_restore, \
             patch("xiaopaw.agents.main_crew.prune_tool_results"), \
             patch("xiaopaw.agents.main_crew.maybe_compress"):
            crew.before_llm_hook(_make_hook_context([{"role": "user", "content": "msg"}]))

        mock_restore.assert_not_called()

    def test_hook_always_calls_prune(self, tmp_path):
        """每次调用都触发 prune_tool_results"""
        crew = _make_crew_instance(tmp_path)
        context = _make_hook_context([{"role": "user", "content": "msg"}])
        with patch("xiaopaw.agents.main_crew.prune_tool_results") as mock_prune, \
             patch("xiaopaw.agents.main_crew.maybe_compress"):
            crew.before_llm_hook(context)
        mock_prune.assert_called_once_with(context.messages, keep_turns=10)

    def test_hook_always_calls_compress(self, tmp_path):
        """每次调用都触发 maybe_compress"""
        crew = _make_crew_instance(tmp_path)
        context = _make_hook_context([{"role": "user", "content": "msg"}])
        with patch("xiaopaw.agents.main_crew.prune_tool_results"), \
             patch("xiaopaw.agents.main_crew.maybe_compress") as mock_compress:
            crew.before_llm_hook(context)
        mock_compress.assert_called_once()

    def test_hook_returns_none(self, tmp_path):
        """hook 返回 None（继续 LLM 调用；返回 False 则会阻止调用）"""
        crew = _make_crew_instance(tmp_path)
        context = _make_hook_context([{"role": "user", "content": "msg"}])
        with patch("xiaopaw.agents.main_crew.prune_tool_results"), \
             patch("xiaopaw.agents.main_crew.maybe_compress"):
            result = crew.before_llm_hook(context)
        assert result is None

    def test_hook_saves_messages_reference(self, tmp_path):
        """hook 保存 context.messages 引用到 _last_msgs"""
        crew = _make_crew_instance(tmp_path)
        msgs = [{"role": "user", "content": "msg"}]
        context = _make_hook_context(msgs)
        with patch("xiaopaw.agents.main_crew.prune_tool_results"), \
             patch("xiaopaw.agents.main_crew.maybe_compress"):
            crew.before_llm_hook(context)
        assert crew._last_msgs is msgs


# ── _restore_session ────────────────────────────────────────────


class TestRestoreSession:
    def test_no_ctx_json_messages_unchanged(self, tmp_path):
        """ctx.json 不存在时，context.messages 保持不变"""
        crew = _make_crew_instance(tmp_path)
        msgs = [{"role": "user", "content": "第一次消息"}]
        context = _make_hook_context(list(msgs))
        crew._restore_session(context)
        assert len(context.messages) == 1
        assert context.messages[0]["content"] == "第一次消息"

    def test_ctx_json_exists_prepends_history(self, tmp_path):
        """ctx.json 存在时，历史 messages 注入，本轮 user 消息追加到末尾"""
        crew = _make_crew_instance(tmp_path)
        history = [
            {"role": "user",      "content": "历史问题"},
            {"role": "assistant", "content": "历史回答"},
        ]
        save_session_ctx("s-test", history, ctx_dir=tmp_path / "ctx")

        context = _make_hook_context([{"role": "user", "content": "当前问题"}])
        crew._restore_session(context)

        contents = [m["content"] for m in context.messages]
        assert "历史问题" in contents
        assert "历史回答" in contents
        assert context.messages[-1]["content"] == "当前问题"
        assert context.messages[-1]["role"] == "user"

    def test_restore_sets_history_len(self, tmp_path):
        """_history_len 等于恢复的历史消息数"""
        crew = _make_crew_instance(tmp_path)
        history = [
            {"role": "user",      "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        save_session_ctx("s-test", history, ctx_dir=tmp_path / "ctx")

        context = _make_hook_context([{"role": "user", "content": "q2"}])
        crew._restore_session(context)

        assert crew._history_len == 2

    def test_restore_with_empty_history_keeps_zero_len(self, tmp_path):
        """无历史时 _history_len = 0"""
        crew = _make_crew_instance(tmp_path)
        context = _make_hook_context([{"role": "user", "content": "首次"}])
        crew._restore_session(context)
        assert crew._history_len == 0

    def test_restore_preserves_current_system_messages(self, tmp_path):
        """恢复后，当前轮 system 消息（新 bootstrap backstory）仍在 context 中。

        💡 回归测试（H3 fix）：旧实现 clear() 后只追回 user 消息，
        导致 CrewAI 注入的 system prompt 被永久丢弃，Agent 身份约束失效。
        修复后：system 消息应出现在恢复结果的头部。
        """
        crew = _make_crew_instance(tmp_path)
        history = [
            {"role": "user",      "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
        ]
        save_session_ctx("s-test", history, ctx_dir=tmp_path / "ctx")

        # 模拟 CrewAI 注入的 system 消息（含最新 backstory）
        current_system = {"role": "system", "content": "你是 XiaoPaw，最新版 backstory。"}
        context = _make_hook_context([
            current_system,
            {"role": "user", "content": "当前问题"},
        ])
        crew._restore_session(context)

        # system 消息必须保留（不被 clear 丢弃）
        system_msgs = [m for m in context.messages if m.get("role") == "system"]
        assert len(system_msgs) >= 1
        assert any(m["content"] == "你是 XiaoPaw，最新版 backstory。" for m in system_msgs)

        # user 消息在末尾
        assert context.messages[-1]["content"] == "当前问题"

    def test_restore_filters_old_role_system_keeps_summary(self, tmp_path):
        """历史中的旧 role system 消息被过滤，<context_summary> 压缩摘要保留。"""
        crew = _make_crew_instance(tmp_path)
        history = [
            {"role": "system",    "content": "旧 backstory（应被过滤）"},
            {"role": "system",    "content": "<context_summary>\n旧摘要\n</context_summary>"},
            {"role": "user",      "content": "历史问题"},
            {"role": "assistant", "content": "历史回答"},
        ]
        save_session_ctx("s-test", history, ctx_dir=tmp_path / "ctx")

        current_system = {"role": "system", "content": "新 backstory"}
        context = _make_hook_context([
            current_system,
            {"role": "user", "content": "当前问题"},
        ])
        crew._restore_session(context)

        contents = [m.get("content", "") for m in context.messages]
        # 旧 backstory 被过滤
        assert "旧 backstory（应被过滤）" not in contents
        # context_summary 保留
        assert any("<context_summary>" in c for c in contents)
        # 当前 backstory 保留
        assert "新 backstory" in contents


# ── run_and_index ───────────────────────────────────────────────


class TestRunAndIndex:
    async def test_saves_ctx_json_after_kickoff(self, tmp_path):
        """kickoff 完成后，ctx.json 被写入"""
        crew = _make_crew_instance(tmp_path)
        crew._last_msgs = [
            {"role": "user",      "content": "问"},
            {"role": "assistant", "content": "答"},
        ]
        crew._history_len = 0

        mock_result = MagicMock()
        mock_result.pydantic = None
        mock_result.raw = "回复"
        mock_crew_obj = MagicMock()
        mock_crew_obj.akickoff = AsyncMock(return_value=mock_result)

        with patch.object(crew, "crew", return_value=mock_crew_obj):
            await crew.run_and_index()

        ctx_file = tmp_path / "ctx" / "s-test_ctx.json"
        assert ctx_file.exists()

    async def test_create_task_called_when_db_dsn_set(self, tmp_path):
        """db_dsn 非空时，asyncio.create_task 被调用"""
        crew = _make_crew_instance(tmp_path, db_dsn="postgresql://test")
        crew._last_msgs = [{"role": "user", "content": "问"}]
        crew._history_len = 0

        mock_result = MagicMock()
        mock_result.pydantic = None
        mock_result.raw = "回复"
        mock_crew_obj = MagicMock()
        mock_crew_obj.akickoff = AsyncMock(return_value=mock_result)

        with patch.object(crew, "crew", return_value=mock_crew_obj), \
             patch("xiaopaw.agents.main_crew.asyncio") as mock_asyncio, \
             patch("xiaopaw.agents.main_crew.async_index_turn"):
            await crew.run_and_index()

        mock_asyncio.create_task.assert_called_once()

    async def test_create_task_not_called_when_db_dsn_empty(self, tmp_path):
        """db_dsn 为空时，create_task 不被调用"""
        crew = _make_crew_instance(tmp_path, db_dsn="")
        crew._last_msgs = []

        mock_result = MagicMock()
        mock_result.pydantic = None
        mock_result.raw = "回复"
        mock_crew_obj = MagicMock()
        mock_crew_obj.akickoff = AsyncMock(return_value=mock_result)

        with patch.object(crew, "crew", return_value=mock_crew_obj), \
             patch("xiaopaw.agents.main_crew.asyncio") as mock_asyncio:
            await crew.run_and_index()

        mock_asyncio.create_task.assert_not_called()

    async def test_returns_pydantic_reply_when_available(self, tmp_path):
        """result.pydantic.reply 存在时优先返回"""
        crew = _make_crew_instance(tmp_path)
        crew._last_msgs = []

        mock_pydantic = MagicMock()
        mock_pydantic.reply = "pydantic 回复"
        mock_result = MagicMock()
        mock_result.pydantic = mock_pydantic
        mock_result.raw = "raw 回复"
        mock_crew_obj = MagicMock()
        mock_crew_obj.akickoff = AsyncMock(return_value=mock_result)

        with patch.object(crew, "crew", return_value=mock_crew_obj):
            result = await crew.run_and_index()

        assert result == "pydantic 回复"

    async def test_falls_back_to_raw_when_no_pydantic(self, tmp_path):
        """pydantic 为 None 时 fallback 到 raw"""
        crew = _make_crew_instance(tmp_path)
        crew._last_msgs = []

        mock_result = MagicMock()
        mock_result.pydantic = None
        mock_result.raw = "raw text"
        mock_crew_obj = MagicMock()
        mock_crew_obj.akickoff = AsyncMock(return_value=mock_result)

        with patch.object(crew, "crew", return_value=mock_crew_obj):
            result = await crew.run_and_index()

        assert result == "raw text"


# ── build_agent_fn（新签名）─────────────────────────────────────


class TestBuildAgentFnNewSignature:
    def test_accepts_workspace_dir_ctx_dir_db_dsn(self, tmp_path):
        """build_agent_fn 接受新的三个 memory 参数"""
        from xiaopaw.agents.main_crew import build_agent_fn  # noqa: PLC0415

        sender = MagicMock()
        workspace_dir = _make_workspace(tmp_path)
        ctx_dir = tmp_path / "ctx"
        ctx_dir.mkdir()

        # 不应抛 TypeError
        fn = build_agent_fn(
            sender        = sender,
            workspace_dir = workspace_dir,
            ctx_dir       = ctx_dir,
            db_dsn        = "",
        )
        assert callable(fn)

    async def test_agent_fn_creates_memory_aware_crew(self, tmp_path):
        """agent_fn 调用时走 MemoryAwareCrew.run_and_index（而不是 _build_crew）"""
        from xiaopaw.agents.main_crew import build_agent_fn, MemoryAwareCrew  # noqa: PLC0415

        sender = MagicMock()
        workspace_dir = _make_workspace(tmp_path)
        ctx_dir = tmp_path / "ctx"
        ctx_dir.mkdir()

        fn = build_agent_fn(
            sender        = sender,
            workspace_dir = workspace_dir,
            ctx_dir       = ctx_dir,
            db_dsn        = "",
        )

        # mock run_and_index 而不是 crew()（@crew 是 CrewAI 描述符，直接 patch 有副作用）
        with patch.object(MemoryAwareCrew, "run_and_index", new_callable=lambda: lambda self: AsyncMock(return_value="回复")()):
            result = await fn(
                user_message = "测试",
                history      = [],
                session_id   = "s-001",
                routing_key  = "p2p:ou_test",
                root_id      = "om_001",
                verbose      = False,
            )

        assert result == "回复"
