"""context_mgmt 单元测试

💡【第19课·上下文生命周期】测试四组核心函数：
  1. prune_tool_results  — 剪枝旧 tool result（in-place）
  2. chunk_by_tokens     — 按近似 token 数切分消息
  3. maybe_compress      — 超阈值时分块摘要压缩（in-place）
  4. ctx.json 持久化     — save / load / append_raw roundtrip

# 注意：asyncio_mode = "auto" 已在 pyproject.toml 全局开启，
# async def test_* 方法无需显式 @pytest.mark.asyncio。
"""

from __future__ import annotations

import json

import pytest

from unittest.mock import patch

from xiaopaw.memory.context_mgmt import (
    append_session_raw,
    chunk_by_tokens,
    load_session_ctx,
    maybe_compress,
    prune_tool_results,
    save_session_ctx,
)


# ── helpers ─────────────────────────────────────────────────────


def _make_turns(n: int) -> list[dict]:
    """生成 n 轮对话消息（每轮：user + assistant + tool），共 3n 条"""
    msgs = []
    for i in range(n):
        msgs.append({"role": "user",      "content": f"问题{i}"})
        msgs.append({"role": "assistant", "content": f"调用工具{i}"})
        msgs.append({"role": "tool",      "content": f"工具返回很长的内容{i}" * 20})
    return msgs


# ── prune_tool_results ──────────────────────────────────────────


class TestPruneToolResults:
    def test_returns_none(self):
        """in-place 操作，返回 None"""
        msgs = _make_turns(3)
        result = prune_tool_results(msgs, keep_turns=2)
        assert result is None

    def test_recent_tool_results_preserved(self):
        """keep_turns=2 时，最近 2 轮的 tool 消息保留原文"""
        msgs = _make_turns(5)
        prune_tool_results(msgs, keep_turns=2)
        # 最后 2 轮 = 最后 6 条消息（2轮×3条）
        recent_tools = [m for m in msgs[-6:] if m["role"] == "tool"]
        assert all("[已剪枝]" not in m["content"] for m in recent_tools)

    def test_old_tool_results_replaced(self):
        """超出 keep_turns 的 tool 消息内容替换为 [已剪枝]"""
        msgs = _make_turns(5)
        prune_tool_results(msgs, keep_turns=2)
        # 前 3 轮（前 9 条）中的 tool 消息应被替换
        old_tools = [m for m in msgs[:-6] if m["role"] == "tool"]
        assert len(old_tools) == 3  # 前 3 轮各有 1 个 tool 消息
        assert all(m["content"] == "[已剪枝]" for m in old_tools)

    def test_user_assistant_messages_untouched(self):
        """user / assistant 消息不被剪枝"""
        msgs = _make_turns(5)
        original = {(m["role"], m["content"]) for m in msgs if m["role"] != "tool"}
        prune_tool_results(msgs, keep_turns=2)
        after = {(m["role"], m["content"]) for m in msgs if m["role"] != "tool"}
        assert original == after

    def test_fewer_turns_than_keep_no_prune(self):
        """轮数 ≤ keep_turns 时不做任何修改"""
        msgs = _make_turns(2)
        original_contents = [m["content"] for m in msgs]
        prune_tool_results(msgs, keep_turns=5)
        assert [m["content"] for m in msgs] == original_contents

    def test_empty_messages_no_error(self):
        """空列表不抛异常"""
        prune_tool_results([], keep_turns=5)  # should not raise

    def test_no_tool_messages_noop(self):
        """没有 tool 消息时，不做修改"""
        msgs = [
            {"role": "user",      "content": f"u{i}"}
            for i in range(10)
        ]
        original = [m.copy() for m in msgs]
        prune_tool_results(msgs, keep_turns=3)
        assert msgs == original

    def test_keep_turns_exactly_boundary(self):
        """keep_turns 等于总轮数时，不剪任何内容"""
        msgs = _make_turns(3)
        original_tools = [m["content"] for m in msgs if m["role"] == "tool"]
        prune_tool_results(msgs, keep_turns=3)
        after_tools = [m["content"] for m in msgs if m["role"] == "tool"]
        assert original_tools == after_tools


# ── chunk_by_tokens ─────────────────────────────────────────────


class TestChunkByTokens:
    def test_empty_returns_empty(self):
        assert chunk_by_tokens([]) == []

    def test_single_small_message_one_chunk(self):
        msgs = [{"role": "user", "content": "hello"}]
        chunks = chunk_by_tokens(msgs, chunk_tokens=1000)
        assert len(chunks) == 1
        assert chunks[0] == msgs

    def test_messages_split_by_token_limit(self):
        """超出 chunk_tokens 时，切成多个 chunk"""
        # 每条消息约 500 chars → 250 tokens，chunk_tokens=300 时每个 chunk 最多 1 条
        msgs = [{"role": "user", "content": "x" * 600} for _ in range(3)]
        chunks = chunk_by_tokens(msgs, chunk_tokens=300)
        assert len(chunks) == 3  # 每条独立成 chunk

    def test_all_messages_in_chunks(self):
        """所有消息都出现在某个 chunk 中"""
        msgs = _make_turns(5)
        chunks = chunk_by_tokens(msgs, chunk_tokens=500)
        flattened = [m for chunk in chunks for m in chunk]
        assert len(flattened) == len(msgs)

    def test_small_messages_grouped(self):
        """小消息合并到同一 chunk"""
        msgs = [{"role": "user", "content": "hi"} for _ in range(10)]
        chunks = chunk_by_tokens(msgs, chunk_tokens=10000)
        # 所有消息都应在同一个 chunk 中
        assert len(chunks) == 1
        assert len(chunks[0]) == 10

    def test_oversized_single_message_standalone(self):
        """单条消息超过 chunk_tokens 时，独立成 chunk（不截断）"""
        big = {"role": "user", "content": "x" * 10000}
        small = {"role": "assistant", "content": "ok"}
        chunks = chunk_by_tokens([big, small], chunk_tokens=100)
        # big 单独一 chunk，small 单独一 chunk
        assert len(chunks) == 2
        assert big in chunks[0]


# ── maybe_compress ─────────────────────────────────────────────


def _make_context_mock(ctx_window: int = 32000):
    """构造 LLMCallHookContext mock，支持 context.llm.context_window_size"""
    from unittest.mock import MagicMock  # noqa: PLC0415
    ctx = MagicMock()
    ctx.llm.context_window_size = ctx_window
    return ctx


class TestMaybeCompress:
    def test_below_threshold_no_compression(self):
        """token 使用率低于阈值时不压缩"""
        msgs = _make_turns(2)  # 少量消息
        original = [m.copy() for m in msgs]
        ctx = _make_context_mock(ctx_window=32000)
        with patch("xiaopaw.memory.context_mgmt._summarize_chunk") as mock_sum:
            maybe_compress(msgs, ctx, compress_threshold=0.45)
        mock_sum.assert_not_called()
        assert msgs == original

    def test_above_threshold_triggers_compression(self):
        """token 使用率超过阈值时，_summarize_chunk 被调用"""
        # 造一批大消息使 token 超阈值
        msgs = [{"role": "user",      "content": "x" * 10000} for _ in range(5)] + \
               [{"role": "assistant", "content": "y" * 10000} for _ in range(5)]
        ctx = _make_context_mock(ctx_window=1000)  # 极小的 ctx_window → 必超阈值
        with patch("xiaopaw.memory.context_mgmt._summarize_chunk", return_value="摘要") as mock_sum:
            maybe_compress(msgs, ctx, fresh_keep_turns=2, compress_threshold=0.01)
        mock_sum.assert_called()

    def test_system_messages_preserved_after_compression(self):
        """压缩后 system 消息（框架注入的）仍在结果中"""
        system_msg = {"role": "system", "content": "你是 XiaoPaw。"}
        user_msgs  = [{"role": "user",      "content": "q" * 5000} for _ in range(6)]
        asst_msgs  = [{"role": "assistant", "content": "a" * 5000} for _ in range(6)]
        msgs = [system_msg] + [m for pair in zip(user_msgs, asst_msgs) for m in pair]
        ctx = _make_context_mock(ctx_window=100)  # 强制超阈值
        with patch("xiaopaw.memory.context_mgmt._summarize_chunk", return_value="摘要"):
            maybe_compress(msgs, ctx, fresh_keep_turns=2, compress_threshold=0.01)
        assert any(m.get("content") == "你是 XiaoPaw。" for m in msgs)

    def test_fresh_turns_preserved_verbatim(self):
        """最近 fresh_keep_turns 轮的 user 消息原文保留"""
        turns = _make_turns(8)
        ctx = _make_context_mock(ctx_window=100)
        with patch("xiaopaw.memory.context_mgmt._summarize_chunk", return_value="摘要"):
            maybe_compress(turns, ctx, fresh_keep_turns=3, compress_threshold=0.01)
        # 最后 3 个 user 消息内容（问题5、6、7）不应被替换
        user_msgs = [m for m in turns if m.get("role") == "user"]
        assert user_msgs[-1]["content"] == "问题7"
        assert user_msgs[-2]["content"] == "问题6"
        assert user_msgs[-3]["content"] == "问题5"

    def test_too_few_turns_skips_compression(self):
        """轮数 ≤ fresh_keep_turns 时不压缩（无法划分"旧区"）"""
        msgs = _make_turns(2)
        original = [m.copy() for m in msgs]
        ctx = _make_context_mock(ctx_window=1)  # 极小，理论上会超阈值
        with patch("xiaopaw.memory.context_mgmt._summarize_chunk") as mock_sum:
            maybe_compress(msgs, ctx, fresh_keep_turns=5, compress_threshold=0.0)
        mock_sum.assert_not_called()

    def test_summary_messages_not_counted_in_threshold(self):
        """已有 <context_summary> system 消息不应使阈值判断失效（防雪崩）"""
        # 插入大量 context_summary system 消息，模拟多轮压缩后的状态
        summary_msgs = [
            {"role": "system", "content": "<context_summary>\n" + "s" * 10000 + "\n</context_summary>"}
            for _ in range(10)
        ]
        # 少量真正的对话消息（不足以单独触发阈值）
        small_conv = _make_turns(2)  # token 很小
        msgs = summary_msgs + small_conv
        ctx = _make_context_mock(ctx_window=32000)
        with patch("xiaopaw.memory.context_mgmt._summarize_chunk") as mock_sum:
            maybe_compress(msgs, ctx, compress_threshold=0.45)
        # 非 system 消息 token 远低于阈值，不应触发压缩
        mock_sum.assert_not_called()


# ── ctx.json 持久化 ─────────────────────────────────────────────


class TestCtxPersistence:
    def test_save_then_load_roundtrip(self, tmp_path):
        """save → load 数据一致"""
        messages = [
            {"role": "user",      "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        save_session_ctx("s-001", messages, ctx_dir=tmp_path)
        loaded = load_session_ctx("s-001", ctx_dir=tmp_path)
        assert loaded == messages

    def test_load_nonexistent_returns_empty(self, tmp_path):
        """ctx.json 不存在时返回空列表，不抛异常"""
        result = load_session_ctx("s-missing", ctx_dir=tmp_path)
        assert result == []

    def test_save_overwrites_previous(self, tmp_path):
        """第二次 save 覆盖第一次"""
        save_session_ctx("s-001", [{"role": "user", "content": "旧"}], ctx_dir=tmp_path)
        save_session_ctx("s-001", [{"role": "user", "content": "新"}], ctx_dir=tmp_path)
        loaded = load_session_ctx("s-001", ctx_dir=tmp_path)
        assert len(loaded) == 1
        assert loaded[0]["content"] == "新"

    def test_save_creates_parent_dir_if_missing(self, tmp_path):
        """ctx_dir 不存在时自动创建"""
        ctx_dir = tmp_path / "nested" / "ctx"
        save_session_ctx("s-001", [{"role": "user", "content": "x"}], ctx_dir=ctx_dir)
        assert (ctx_dir / "s-001_ctx.json").exists()

    def test_different_session_ids_separate_files(self, tmp_path):
        """不同 session_id 各自独立文件"""
        save_session_ctx("s-aaa", [{"role": "user", "content": "A"}], ctx_dir=tmp_path)
        save_session_ctx("s-bbb", [{"role": "user", "content": "B"}], ctx_dir=tmp_path)
        assert load_session_ctx("s-aaa", ctx_dir=tmp_path)[0]["content"] == "A"
        assert load_session_ctx("s-bbb", ctx_dir=tmp_path)[0]["content"] == "B"

    def test_preserves_message_order(self, tmp_path):
        """消息顺序保持不变"""
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        save_session_ctx("s-001", messages, ctx_dir=tmp_path)
        loaded = load_session_ctx("s-001", ctx_dir=tmp_path)
        assert [m["content"] for m in loaded] == [m["content"] for m in messages]

    def test_save_handles_unicode(self, tmp_path):
        """中文内容正常保存和读取"""
        messages = [{"role": "user", "content": "你好，世界！🐾"}]
        save_session_ctx("s-001", messages, ctx_dir=tmp_path)
        loaded = load_session_ctx("s-001", ctx_dir=tmp_path)
        assert loaded[0]["content"] == "你好，世界！🐾"


# ── append_session_raw ──────────────────────────────────────────


class TestAppendSessionRaw:
    def test_appends_to_jsonl(self, tmp_path):
        """多次 append，JSONL 行数累积"""
        for i in range(3):
            append_session_raw("s-001", [{"role": "user", "content": f"turn{i}"}], ctx_dir=tmp_path)

        raw_path = tmp_path / "s-001_raw.jsonl"
        assert raw_path.exists()
        lines = raw_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_each_line_valid_json(self, tmp_path):
        """每行都是合法 JSON"""
        append_session_raw(
            "s-001",
            [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
            ctx_dir=tmp_path,
        )
        raw_path = tmp_path / "s-001_raw.jsonl"
        for line in raw_path.read_text(encoding="utf-8").strip().split("\n"):
            parsed = json.loads(line)
            assert "role" in parsed
            assert "content" in parsed

    def test_ts_field_added(self, tmp_path):
        """每条记录附加 ts 时间戳字段"""
        append_session_raw("s-001", [{"role": "user", "content": "x"}], ctx_dir=tmp_path)
        line = (tmp_path / "s-001_raw.jsonl").read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert "ts" in parsed

    def test_multiple_messages_per_call(self, tmp_path):
        """单次 append 多条消息，每条单独一行"""
        msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
        append_session_raw("s-001", msgs, ctx_dir=tmp_path)
        lines = (tmp_path / "s-001_raw.jsonl").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_append_creates_parent_dir(self, tmp_path):
        """ctx_dir 不存在时自动创建"""
        ctx_dir = tmp_path / "deep" / "ctx"
        append_session_raw("s-001", [{"role": "user", "content": "x"}], ctx_dir=ctx_dir)
        assert (ctx_dir / "s-001_raw.jsonl").exists()

    def test_empty_messages_no_write(self, tmp_path):
        """传入空列表时，文件不创建（或无内容）"""
        append_session_raw("s-001", [], ctx_dir=tmp_path)
        raw_path = tmp_path / "s-001_raw.jsonl"
        if raw_path.exists():
            assert raw_path.read_text(encoding="utf-8").strip() == ""
