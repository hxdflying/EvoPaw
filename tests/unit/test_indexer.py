"""indexer 单元测试

💡【第21课·搜索记忆】测试 pgvector 写入 pipeline 的各个环节。
所有 LLM 调用和 DB 连接全部 mock，只测函数逻辑。

pipeline 顺序：
  extract_summary_and_tags → embed_texts → upsert_memory
  └── 由 async_index_turn 编排，在 asyncio.create_task() 中后台运行

# 注意：asyncio_mode = "auto" 已在 pyproject.toml 全局开启，
# async def test_* 方法无需显式 @pytest.mark.asyncio。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from xiaopaw.memory.indexer import (
    async_index_turn,
    embed_texts,
    extract_summary_and_tags,
    upsert_memory,
)


# ── extract_summary_and_tags ────────────────────────────────────


class TestExtractSummaryAndTags:
    def test_returns_summary_and_tags_on_valid_json(self):
        """LLM 返回合法 JSON → 正确解析 summary 和 tags"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"summary": "用户问课程进度", "tags": ["课程", "进度"]}'

        with patch("xiaopaw.memory.indexer._llm_client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_resp
            summary, tags = extract_summary_and_tags("课程到哪了？", "第21课完成了。")

        assert summary == "用户问课程进度"
        assert tags == ["课程", "进度"]

    def test_strips_markdown_code_block(self):
        """LLM 返回 ```json ... ``` 包裹时，正确剥离代码块"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = (
            '```json\n{"summary": "查询日程", "tags": ["日程"]}\n```'
        )
        with patch("xiaopaw.memory.indexer._llm_client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_resp
            summary, tags = extract_summary_and_tags("今天有什么安排？", "今天下午3点开会。")

        assert summary == "查询日程"
        assert tags == ["日程"]

    def test_malformed_json_falls_back_to_user_message(self):
        """LLM 返回非 JSON 时，summary 兜底为 user_message 前50字，tags 为空"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "这不是 JSON 内容"

        with patch("xiaopaw.memory.indexer._llm_client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_resp
            summary, tags = extract_summary_and_tags("这是用户消息", "这是助手回复")

        assert summary == "这是用户消息"
        assert tags == []

    def test_long_message_truncated_in_prompt(self):
        """超长 user_message 在调用 LLM 时被截断（不超过 500 字符传入 prompt）"""
        # 前 500 个 'A'，后 500 个 'B'：截断后只有 A，没有 B
        long_msg = "A" * 500 + "B" * 500
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"summary": "s", "tags": []}'

        with patch("xiaopaw.memory.indexer._llm_client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_resp
            extract_summary_and_tags(long_msg, "reply")

        call_args = mock_client.chat.completions.create.call_args
        prompt_content = call_args.kwargs["messages"][0]["content"]
        # 截断后只有 A 部分，B 不应出现在 prompt 中
        assert "A" * 10 in prompt_content  # A 部分存在
        assert "B" not in prompt_content   # B 部分被截断

    def test_empty_summary_field_returns_empty_string(self):
        """JSON 中 summary 字段缺失时，返回空字符串"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"tags": ["工作"]}'

        with patch("xiaopaw.memory.indexer._llm_client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_resp
            summary, tags = extract_summary_and_tags("msg", "reply")

        assert summary == ""
        assert tags == ["工作"]


# ── embed_texts ─────────────────────────────────────────────────


class TestEmbedTexts:
    def test_empty_input_returns_empty_list(self):
        """空列表直接返回 []，不调用 API"""
        with patch("xiaopaw.memory.indexer._get_embed_client") as mock_client:
            result = embed_texts([])
        assert result == []
        mock_client.assert_not_called()

    def test_returns_embeddings_for_each_text(self):
        """返回与输入等长的向量列表"""
        mock_item1 = MagicMock()
        mock_item1.embedding = [0.1] * 1024
        mock_item2 = MagicMock()
        mock_item2.embedding = [0.2] * 1024
        mock_resp = MagicMock()
        mock_resp.data = [mock_item1, mock_item2]

        with patch("xiaopaw.memory.indexer._get_embed_client") as mock_client:
            mock_client.return_value.embeddings.create.return_value = mock_resp
            result = embed_texts(["文本1", "文本2"])

        assert len(result) == 2
        assert result[0] == [0.1] * 1024
        assert result[1] == [0.2] * 1024

    def test_api_error_propagates(self):
        """API 调用失败时异常向上传播（_index_single_turn 统一 try/except 兜底）"""
        with patch("xiaopaw.memory.indexer._get_embed_client") as mock_client:
            mock_client.return_value.embeddings.create.side_effect = RuntimeError("API down")
            with pytest.raises(RuntimeError, match="API down"):
                embed_texts(["文本"])

    def test_uses_correct_model_and_dim(self):
        """调用时传入正确的 model 和 dimensions 参数"""
        mock_resp = MagicMock()
        mock_resp.data = []

        with patch("xiaopaw.memory.indexer._get_embed_client") as mock_client:
            mock_client.return_value.embeddings.create.return_value = mock_resp
            embed_texts(["x"])

        call_kwargs = mock_client.return_value.embeddings.create.call_args.kwargs
        assert call_kwargs["model"] == "text-embedding-v3"
        assert call_kwargs["dimensions"] == 1024


# ── upsert_memory ───────────────────────────────────────────────


class TestUpsertMemory:
    def _make_record(self) -> dict:
        return {
            "id":              "test-id-001",
            "session_id":      "s-001",
            "routing_key":     "p2p:ou_test",
            "user_message":    "测试消息",
            "assistant_reply": "测试回复",
            "summary":         "测试摘要",
            "tags":            ["测试"],
            "turn_ts":         1000,
            "summary_vec":     [0.1] * 1024,
            "message_vec":     [0.2] * 1024,
            "search_text":     "测试消息 测试",
        }

    def test_execute_called_once(self):
        """execute 被调用一次"""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        upsert_memory(mock_conn, self._make_record())

        mock_cur.execute.assert_called_once()

    def test_sql_contains_insert_and_on_conflict(self):
        """SQL 包含 INSERT 和 ON CONFLICT DO NOTHING（幂等）"""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        upsert_memory(mock_conn, self._make_record())

        sql = mock_cur.execute.call_args[0][0]
        assert "INSERT" in sql.upper()
        assert "ON CONFLICT" in sql.upper()
        assert "DO NOTHING" in sql.upper()

    def test_commit_called_after_execute(self):
        """execute 之后 commit 被调用"""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        upsert_memory(mock_conn, self._make_record())

        mock_conn.commit.assert_called_once()

    def test_vectors_converted_to_string_for_psycopg2(self):
        """summary_vec 和 message_vec 以字符串形式传给 execute（pgvector 格式）"""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        upsert_memory(mock_conn, self._make_record())

        params = mock_cur.execute.call_args[0][1]
        assert isinstance(params["summary_vec"], str)
        assert isinstance(params["message_vec"], str)


# ── async_index_turn ────────────────────────────────────────────


class TestAsyncIndexTurn:
    async def test_empty_db_dsn_skips_all_processing(self):
        """db_dsn 为空时，直接返回，不调用 LLM 或 DB"""
        with patch("xiaopaw.memory.indexer.extract_summary_and_tags") as mock_extract, \
             patch("xiaopaw.memory.indexer.embed_texts") as mock_embed, \
             patch("xiaopaw.memory.indexer._connect_db") as mock_connect:
            await async_index_turn(
                session_id="s-001", routing_key="p2p:ou_test",
                user_message="hi", assistant_reply="hello",
                turn_ts=1000, db_dsn=""
            )
        mock_extract.assert_not_called()
        mock_embed.assert_not_called()
        mock_connect.assert_not_called()

    async def test_calls_extract_then_embed_then_upsert(self):
        """正常流程：extract → embed → upsert，顺序正确"""
        call_order: list[str] = []

        def fake_extract(user_msg, asst_reply):
            call_order.append("extract")
            return "摘要", ["tag"]

        def fake_embed(texts):
            call_order.append("embed")
            return [[0.1] * 1024, [0.2] * 1024]

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        def fake_upsert(conn, record):
            call_order.append("upsert")

        with patch("xiaopaw.memory.indexer.extract_summary_and_tags", fake_extract), \
             patch("xiaopaw.memory.indexer.embed_texts", fake_embed), \
             patch("xiaopaw.memory.indexer.upsert_memory", fake_upsert), \
             patch("xiaopaw.memory.indexer._connect_db", return_value=mock_conn):
            await async_index_turn(
                session_id="s-001", routing_key="p2p:ou_test",
                user_message="用户消息", assistant_reply="助手回复",
                turn_ts=1000, db_dsn="postgresql://test"
            )

        assert call_order == ["extract", "embed", "upsert"]

    async def test_db_connection_error_does_not_propagate(self):
        """DB 连接失败时，只 log warning，不把异常传播给调用方"""
        with patch("xiaopaw.memory.indexer._connect_db",
                   side_effect=Exception("DB down")):
            # 不应抛异常
            await async_index_turn(
                session_id="s-001", routing_key="p2p:ou_test",
                user_message="hi", assistant_reply="hello",
                turn_ts=1000, db_dsn="postgresql://test"
            )

    async def test_record_includes_correct_session_and_routing(self):
        """upsert_memory 收到的 record 包含正确的 session_id 和 routing_key"""
        captured_record: dict = {}

        def fake_upsert(conn, record):
            captured_record.update(record)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("xiaopaw.memory.indexer.extract_summary_and_tags",
                   return_value=("摘要", ["tag"])), \
             patch("xiaopaw.memory.indexer.embed_texts",
                   return_value=[[0.1] * 1024, [0.2] * 1024]), \
             patch("xiaopaw.memory.indexer.upsert_memory", fake_upsert), \
             patch("xiaopaw.memory.indexer._connect_db", return_value=mock_conn):
            await async_index_turn(
                session_id="s-check",
                routing_key="p2p:ou_check",
                user_message="用户消息",
                assistant_reply="助手回复",
                turn_ts=9999,
                db_dsn="postgresql://test"
            )

        assert captured_record.get("session_id") == "s-check"
        assert captured_record.get("routing_key") == "p2p:ou_check"
        assert captured_record.get("user_message") == "用户消息"
        assert captured_record.get("assistant_reply") == "助手回复"
        assert captured_record.get("turn_ts") == 9999
        assert captured_record.get("summary") == "摘要"
        assert captured_record.get("tags") == ["tag"]

    async def test_record_id_is_stable_hash(self):
        """相同输入生成相同 id（幂等性）"""
        ids: list[str] = []

        def capture_upsert(conn, record):
            ids.append(record["id"])

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        params = dict(
            session_id="s-001", routing_key="p2p:test",
            user_message="hello", assistant_reply="hi",
            turn_ts=1000, db_dsn="postgresql://test"
        )
        for _ in range(2):
            with patch("xiaopaw.memory.indexer.extract_summary_and_tags",
                       return_value=("s", [])), \
                 patch("xiaopaw.memory.indexer.embed_texts",
                       return_value=[[0.1] * 1024, [0.2] * 1024]), \
                 patch("xiaopaw.memory.indexer.upsert_memory", capture_upsert), \
                 patch("xiaopaw.memory.indexer._connect_db", return_value=mock_conn):
                await async_index_turn(**params)

        assert len(ids) == 2
        assert ids[0] == ids[1]  # 相同输入 → 相同 id

    async def test_connection_closed_after_success(self):
        """成功完成后，DB 连接被关闭"""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("xiaopaw.memory.indexer.extract_summary_and_tags",
                   return_value=("s", [])), \
             patch("xiaopaw.memory.indexer.embed_texts",
                   return_value=[[0.1] * 1024, [0.2] * 1024]), \
             patch("xiaopaw.memory.indexer.upsert_memory"), \
             patch("xiaopaw.memory.indexer._connect_db", return_value=mock_conn):
            await async_index_turn(
                session_id="s-001", routing_key="p2p:test",
                user_message="hi", assistant_reply="hello",
                turn_ts=1000, db_dsn="postgresql://test"
            )

        mock_conn.close.assert_called_once()
