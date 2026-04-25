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

from evopaw.memory.indexer import (
    async_index_turn,
    embed_texts,
    extract_summary_and_tags,
    shutdown_index_clients,
    upsert_memory,
)


# ── extract_summary_and_tags ────────────────────────────────────


class TestExtractSummaryAndTags:
    def test_returns_summary_and_tags_on_valid_json(self):
        """LLM 返回合法 JSON → 正确解析 summary 和 tags"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"summary": "用户问课程进度", "tags": ["课程", "进度"]}'

        with patch("evopaw.memory.indexer._llm_client") as mock_client:
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
        with patch("evopaw.memory.indexer._llm_client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_resp
            summary, tags = extract_summary_and_tags("今天有什么安排？", "今天下午3点开会。")

        assert summary == "查询日程"
        assert tags == ["日程"]

    def test_malformed_json_falls_back_to_user_message(self):
        """LLM 返回非 JSON 时，summary 兜底为 user_message 前50字，tags 为空"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "这不是 JSON 内容"

        with patch("evopaw.memory.indexer._llm_client") as mock_client:
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

        with patch("evopaw.memory.indexer._llm_client") as mock_client:
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

        with patch("evopaw.memory.indexer._llm_client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_resp
            summary, tags = extract_summary_and_tags("msg", "reply")

        assert summary == ""
        assert tags == ["工作"]


# ── embed_texts ─────────────────────────────────────────────────


class TestEmbedTexts:
    def test_empty_input_returns_empty_list(self):
        """空列表直接返回 []，不调用 API"""
        with patch("evopaw.memory.indexer._get_embed_client") as mock_client:
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

        with patch("evopaw.memory.indexer._get_embed_client") as mock_client:
            mock_client.return_value.embeddings.create.return_value = mock_resp
            result = embed_texts(["文本1", "文本2"])

        assert len(result) == 2
        assert result[0] == [0.1] * 1024
        assert result[1] == [0.2] * 1024

    def test_api_error_propagates(self):
        """API 调用失败时异常向上传播（_index_single_turn 统一 try/except 兜底）"""
        with patch("evopaw.memory.indexer._get_embed_client") as mock_client:
            mock_client.return_value.embeddings.create.side_effect = RuntimeError("API down")
            with pytest.raises(RuntimeError, match="API down"):
                embed_texts(["文本"])

    def test_uses_correct_model_and_dim(self):
        """调用时传入正确的 model 和 dimensions 参数（取自模块级常量）"""
        import evopaw.memory.indexer as idx
        mock_resp = MagicMock()
        mock_resp.data = []

        with patch("evopaw.memory.indexer._get_embed_client") as mock_client:
            mock_client.return_value.embeddings.create.return_value = mock_resp
            embed_texts(["x"])

        call_kwargs = mock_client.return_value.embeddings.create.call_args.kwargs
        # 默认值仍是 text-embedding-v3 / 1024，但通过模块常量读取，
        # 避免硬编码字面值导致环境变量覆盖时测试反而失败。
        assert call_kwargs["model"] == idx._EMBED_MODEL
        assert call_kwargs["dimensions"] == idx._EMBED_DIM


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
        with patch("evopaw.memory.indexer.extract_summary_and_tags") as mock_extract, \
             patch("evopaw.memory.indexer.embed_texts") as mock_embed, \
             patch("evopaw.memory.indexer._connect_db") as mock_connect:
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

        with patch("evopaw.memory.indexer.extract_summary_and_tags", fake_extract), \
             patch("evopaw.memory.indexer.embed_texts", fake_embed), \
             patch("evopaw.memory.indexer.upsert_memory", fake_upsert), \
             patch("evopaw.memory.indexer._connect_db", return_value=mock_conn):
            await async_index_turn(
                session_id="s-001", routing_key="p2p:ou_test",
                user_message="用户消息", assistant_reply="助手回复",
                turn_ts=1000, db_dsn="postgresql://test"
            )

        assert call_order == ["extract", "embed", "upsert"]

    async def test_db_connection_error_does_not_propagate(self):
        """DB 连接失败时，只 log warning，不把异常传播给调用方"""
        with patch("evopaw.memory.indexer._connect_db",
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

        with patch("evopaw.memory.indexer.extract_summary_and_tags",
                   return_value=("摘要", ["tag"])), \
             patch("evopaw.memory.indexer.embed_texts",
                   return_value=[[0.1] * 1024, [0.2] * 1024]), \
             patch("evopaw.memory.indexer.upsert_memory", fake_upsert), \
             patch("evopaw.memory.indexer._connect_db", return_value=mock_conn):
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
            with patch("evopaw.memory.indexer.extract_summary_and_tags",
                       return_value=("s", [])), \
                 patch("evopaw.memory.indexer.embed_texts",
                       return_value=[[0.1] * 1024, [0.2] * 1024]), \
                 patch("evopaw.memory.indexer.upsert_memory", capture_upsert), \
                 patch("evopaw.memory.indexer._connect_db", return_value=mock_conn):
                await async_index_turn(**params)

        assert len(ids) == 2
        assert ids[0] == ids[1]  # 相同输入 → 相同 id

    async def test_connection_closed_after_success(self):
        """成功完成后，DB 连接被关闭"""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("evopaw.memory.indexer.extract_summary_and_tags",
                   return_value=("s", [])), \
             patch("evopaw.memory.indexer.embed_texts",
                   return_value=[[0.1] * 1024, [0.2] * 1024]), \
             patch("evopaw.memory.indexer.upsert_memory"), \
             patch("evopaw.memory.indexer._connect_db", return_value=mock_conn):
            await async_index_turn(
                session_id="s-001", routing_key="p2p:test",
                user_message="hi", assistant_reply="hello",
                turn_ts=1000, db_dsn="postgresql://test"
            )

        mock_conn.close.assert_called_once()


# ── shutdown_index_clients ─────────────────────────────────────


class TestShutdownIndexClients:
    """关闭模块级 LLM/embedding client（M-3 修复）"""

    def setup_method(self):
        """每个 case 前清空模块级单例，避免相互污染"""
        import evopaw.memory.indexer as idx
        idx._llm_client = None
        idx._embed_client = None

    def teardown_method(self):
        """收尾：再次清空，避免影响后续测试"""
        import evopaw.memory.indexer as idx
        idx._llm_client = None
        idx._embed_client = None

    def test_shutdown_calls_close_on_both_clients(self):
        """两个 client 都被实例化时，shutdown 调用各自 close()"""
        import evopaw.memory.indexer as idx

        mock_llm = MagicMock()
        mock_embed = MagicMock()
        idx._llm_client = mock_llm
        idx._embed_client = mock_embed

        shutdown_index_clients()

        mock_llm.close.assert_called_once()
        mock_embed.close.assert_called_once()
        assert idx._llm_client is None
        assert idx._embed_client is None

    def test_shutdown_when_clients_none_no_error(self):
        """两个 client 都未实例化时，shutdown 不抛异常"""
        # setup_method 已确保是 None
        shutdown_index_clients()  # 不应抛异常

    def test_shutdown_only_one_client_set(self):
        """只有一个 client 实例化时，另一个保持 None 不报错"""
        import evopaw.memory.indexer as idx

        mock_llm = MagicMock()
        idx._llm_client = mock_llm
        idx._embed_client = None

        shutdown_index_clients()

        mock_llm.close.assert_called_once()
        assert idx._llm_client is None
        assert idx._embed_client is None

    def test_shutdown_swallows_close_exception(self):
        """client.close() 抛异常时被吞掉，不向上抛（避免阻塞进程退出）"""
        import evopaw.memory.indexer as idx

        bad_client = MagicMock()
        bad_client.close.side_effect = RuntimeError("network broken")
        idx._llm_client = bad_client

        # 不应抛异常
        shutdown_index_clients()
        assert idx._llm_client is None

    def test_shutdown_then_lazy_recreate(self):
        """shutdown 后，下次调用 _get_*_client() 应重新惰性创建"""
        import evopaw.memory.indexer as idx

        mock_llm = MagicMock()
        idx._llm_client = mock_llm
        shutdown_index_clients()
        assert idx._llm_client is None

        # 重新调用 _get_llm_client() 应触发 _make_llm_client()
        new_client = MagicMock()
        with patch("evopaw.memory.indexer._make_llm_client", return_value=new_client):
            result = idx._get_llm_client()
        assert result is new_client
        assert idx._llm_client is new_client


# ── m-7：模型可配置（环境变量覆盖默认值）───────────────────────


class TestModelEnvOverride:
    """验证模块级模型常量从环境变量读取，未设置时回退到默认值"""

    def test_default_models_when_env_unset(self):
        """未设置任何环境变量时使用默认值"""
        import importlib
        import evopaw.memory.indexer as idx
        with patch.dict("os.environ", {}, clear=False):
            for k in ("EVOPAW_MEMORY_EMBED_MODEL", "EVOPAW_MEMORY_EMBED_DIM",
                      "EVOPAW_MEMORY_EXTRACT_MODEL"):
                if k in __import__("os").environ:
                    del __import__("os").environ[k]
            importlib.reload(idx)
            assert idx._EMBED_MODEL == "text-embedding-v3"
            assert idx._EMBED_DIM == 1024
            assert idx._EXTRACT_MODEL == "qwen3-max"
        # reload 一次以恢复（不影响其它测试，因为下次 import 仍取当前 env）
        importlib.reload(idx)

    def test_env_override_indexer_models(self):
        """环境变量设置后模块级常量被覆盖"""
        import importlib
        import os as _os
        import evopaw.memory.indexer as idx
        with patch.dict(_os.environ, {
            "EVOPAW_MEMORY_EMBED_MODEL": "custom-embed",
            "EVOPAW_MEMORY_EMBED_DIM": "768",
            "EVOPAW_MEMORY_EXTRACT_MODEL": "custom-extract",
        }):
            importlib.reload(idx)
            assert idx._EMBED_MODEL == "custom-embed"
            assert idx._EMBED_DIM == 768
            assert idx._EXTRACT_MODEL == "custom-extract"
        importlib.reload(idx)  # 恢复默认

    def test_env_override_summary_model(self):
        """context_mgmt._SUMMARY_MODEL 受环境变量控制"""
        import importlib
        import os as _os
        import evopaw.memory.context_mgmt as ctx
        with patch.dict(_os.environ, {"EVOPAW_MEMORY_SUMMARY_MODEL": "custom-summary"}):
            importlib.reload(ctx)
            assert ctx._SUMMARY_MODEL == "custom-summary"
        importlib.reload(ctx)  # 恢复默认
