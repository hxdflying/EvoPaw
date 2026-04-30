"""context_mgmt 单元测试

测试四组核心函数：
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

from unittest.mock import MagicMock, patch

from evopaw.memory.context_mgmt import (
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


class TestMaybeCompress:
    def test_below_threshold_no_compression(self):
        """token 使用率低于阈值时不压缩"""
        msgs = _make_turns(2)  # 少量消息
        original = [m.copy() for m in msgs]
        with patch("evopaw.memory.context_mgmt._summarize_chunk") as mock_sum:
            maybe_compress(msgs, model_ctx_limit=32000, compress_threshold=0.45)
        mock_sum.assert_not_called()
        assert msgs == original

    def test_above_threshold_triggers_compression(self):
        """token 使用率超过阈值时，_summarize_chunk 被调用"""
        msgs = [{"role": "user",      "content": "x" * 10000} for _ in range(5)] + \
               [{"role": "assistant", "content": "y" * 10000} for _ in range(5)]
        with patch("evopaw.memory.context_mgmt._summarize_chunk", return_value="摘要") as mock_sum:
            maybe_compress(msgs, model_ctx_limit=1000, fresh_keep_turns=2, compress_threshold=0.01)
        mock_sum.assert_called()

    def test_system_messages_preserved_after_compression(self):
        """压缩后 system 消息（框架注入的）仍在结果中"""
        system_msg = {"role": "system", "content": "你是 EvoPaw。"}
        user_msgs  = [{"role": "user",      "content": "q" * 5000} for _ in range(6)]
        asst_msgs  = [{"role": "assistant", "content": "a" * 5000} for _ in range(6)]
        msgs = [system_msg] + [m for pair in zip(user_msgs, asst_msgs) for m in pair]
        with patch("evopaw.memory.context_mgmt._summarize_chunk", return_value="摘要"):
            maybe_compress(msgs, model_ctx_limit=100, fresh_keep_turns=2, compress_threshold=0.01)
        assert any(m.get("content") == "你是 EvoPaw。" for m in msgs)

    def test_fresh_turns_preserved_verbatim(self):
        """最近 fresh_keep_turns 轮的 user 消息原文保留"""
        turns = _make_turns(8)
        with patch("evopaw.memory.context_mgmt._summarize_chunk", return_value="摘要"):
            maybe_compress(turns, model_ctx_limit=100, fresh_keep_turns=3, compress_threshold=0.01)
        user_msgs = [m for m in turns if m.get("role") == "user"]
        assert user_msgs[-1]["content"] == "问题7"
        assert user_msgs[-2]["content"] == "问题6"
        assert user_msgs[-3]["content"] == "问题5"

    def test_too_few_turns_skips_compression(self):
        """轮数 ≤ fresh_keep_turns 时不压缩（无法划分"旧区"）"""
        msgs = _make_turns(2)
        original = [m.copy() for m in msgs]
        with patch("evopaw.memory.context_mgmt._summarize_chunk") as mock_sum:
            maybe_compress(msgs, model_ctx_limit=1, fresh_keep_turns=5, compress_threshold=0.0)
        mock_sum.assert_not_called()

    def test_summary_messages_not_counted_in_threshold(self):
        """已有 <context_summary> system 消息不应使阈值判断失效（防雪崩）"""
        summary_msgs = [
            {"role": "system", "content": "<context_summary>\n" + "s" * 10000 + "\n</context_summary>"}
            for _ in range(10)
        ]
        small_conv = _make_turns(2)
        msgs = summary_msgs + small_conv
        with patch("evopaw.memory.context_mgmt._summarize_chunk") as mock_sum:
            maybe_compress(msgs, model_ctx_limit=32000, compress_threshold=0.45)
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


# ── configure_memory_runtime 仅接受 openai_chat ─────────────────


class TestConfigureMemoryRuntime:
    """memory_summary 当前仅支持 OpenAI-compatible 端点。

    若 resolver 解析出 anthropic_messages / claude_sdk_compat，client 工厂
    `make_openai_client` 仍会返回 OpenAI SDK 实例 —— 运行时调用 chat.completions.create
    必失败。在启动期 fail-fast，避免「配置静态合法、运行时崩溃」。
    """

    def setup_method(self):
        import evopaw.memory.context_mgmt as ctx
        ctx._resolved_summary = None

    def teardown_method(self):
        self.setup_method()

    def test_default_dashscope_passes(self):
        """默认配置走 dashscope（openai_chat），通过校验。"""
        from evopaw.memory.context_mgmt import configure_memory_runtime
        configure_memory_runtime({})  # 不抛异常

    def test_summary_on_anthropic_provider_rejected(self):
        """memory_summary 配到 anthropic_messages 时启动期 raise ResolveError。"""
        from evopaw.memory.context_mgmt import configure_memory_runtime
        from evopaw.provider_runtime import ResolveError
        cfg = {"roles": {"memory_summary": {"provider": "anthropic"}}}
        with pytest.raises(ResolveError, match="openai_chat"):
            configure_memory_runtime(cfg)

    def test_summary_on_claude_sdk_provider_rejected(self):
        """claude_sdk_compat 也不行（client 走 OpenAI SDK，runtime 不兼容）。"""
        from evopaw.memory.context_mgmt import configure_memory_runtime
        from evopaw.provider_runtime import ResolveError
        cfg = {"roles": {"memory_summary": {"provider": "claude_sdk"}}}
        with pytest.raises(ResolveError, match="openai_chat"):
            configure_memory_runtime(cfg)


# ── _summarize_chunk 接入 record_llm_call ──────────────────────


class TestSummarizeChunkMetrics:
    """_summarize_chunk 成功 / 失败两路都打 record_llm_call(role=memory_summary)。"""

    def test_success_records_role_memory_summary(self):
        from evopaw.memory.context_mgmt import _summarize_chunk

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "摘要"
        mock_resp.usage.prompt_tokens = 30
        mock_resp.usage.completion_tokens = 8

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("evopaw.memory.context_mgmt._make_summary_client",
                   return_value=mock_client), \
             patch("evopaw.memory.context_mgmt.record_llm_call") as mock_rec:
            out = _summarize_chunk([{"role": "user", "content": "hi"}])

        assert out == "摘要"
        mock_rec.assert_called_once()
        kw = mock_rec.call_args.kwargs
        assert kw["role"] == "memory_summary"
        assert kw["outcome"] == "success"
        assert kw["input_tokens"] == 30
        assert kw["output_tokens"] == 8

    def test_error_records_outcome_error_and_returns_fallback(self):
        from evopaw.memory.context_mgmt import _summarize_chunk

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")

        with patch("evopaw.memory.context_mgmt._make_summary_client",
                   return_value=mock_client), \
             patch("evopaw.memory.context_mgmt.record_llm_call") as mock_rec:
            out = _summarize_chunk([{"role": "user", "content": "hi"}])

        assert "压缩失败" in out
        mock_rec.assert_called_once()
        assert mock_rec.call_args.kwargs["role"] == "memory_summary"
        assert mock_rec.call_args.kwargs["outcome"] == "error"
