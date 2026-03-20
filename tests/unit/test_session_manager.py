"""SessionManager 单元测试"""

from __future__ import annotations

import asyncio
import json

import pytest

from xiaopaw.session.manager import SessionManager
from xiaopaw.session.models import MessageEntry, SessionEntry


@pytest.fixture
def mgr(tmp_path):
    """每个测试独立的 SessionManager，使用 tmp_path 隔离文件系统"""
    return SessionManager(data_dir=tmp_path)


# ── get_or_create ──────────────────────────────────────────────


class TestGetOrCreate:
    async def test_new_routing_key_creates_session(self, mgr):
        """首次访问自动创建 routing entry + session"""
        entry = await mgr.get_or_create("p2p:ou_test001")
        assert isinstance(entry, SessionEntry)
        assert entry.id.startswith("s-")
        assert entry.verbose is False
        assert entry.message_count == 0

    async def test_existing_routing_key_returns_active(self, mgr):
        """已存在的 routing_key 返回同一 active session"""
        e1 = await mgr.get_or_create("p2p:ou_test001")
        e2 = await mgr.get_or_create("p2p:ou_test001")
        assert e1.id == e2.id

    async def test_different_routing_keys_create_separate_sessions(self, mgr):
        """不同 routing_key 创建独立 session"""
        e1 = await mgr.get_or_create("p2p:ou_a")
        e2 = await mgr.get_or_create("group:oc_b")
        assert e1.id != e2.id

    async def test_persists_to_index_json(self, mgr, tmp_path):
        """get_or_create 后 index.json 应存在且内容正确"""
        await mgr.get_or_create("p2p:ou_test001")
        index_path = tmp_path / "sessions" / "index.json"
        assert index_path.exists()
        data = json.loads(index_path.read_text())
        assert "p2p:ou_test001" in data


# ── create_new_session ─────────────────────────────────────────


class TestCreateNewSession:
    async def test_switches_active_session(self, mgr):
        """/new 创建新 session，active 切换"""
        old = await mgr.get_or_create("p2p:ou_test001")
        new = await mgr.create_new_session("p2p:ou_test001")
        assert new.id != old.id

        # 再次 get 应返回新 session
        current = await mgr.get_or_create("p2p:ou_test001")
        assert current.id == new.id

    async def test_preserves_old_session(self, mgr, tmp_path):
        """旧 session 仍在 sessions 列表中"""
        old = await mgr.get_or_create("p2p:ou_test001")
        await mgr.create_new_session("p2p:ou_test001")

        data = json.loads(
            (tmp_path / "sessions" / "index.json").read_text()
        )
        session_ids = [s["id"] for s in data["p2p:ou_test001"]["sessions"]]
        assert old.id in session_ids

    async def test_creates_jsonl_with_meta_line(self, mgr, tmp_path):
        """新 session 的 JSONL 文件应包含 meta 行"""
        entry = await mgr.get_or_create("p2p:ou_test001")
        jsonl_path = tmp_path / "sessions" / f"{entry.id}.jsonl"
        assert jsonl_path.exists()
        first_line = json.loads(jsonl_path.read_text().strip().split("\n")[0])
        assert first_line["type"] == "meta"
        assert first_line["session_id"] == entry.id


# ── update_verbose ─────────────────────────────────────────────


class TestUpdateVerbose:
    async def test_toggle_verbose(self, mgr):
        """修改 verbose 标志并持久化"""
        await mgr.get_or_create("p2p:ou_test001")
        await mgr.update_verbose("p2p:ou_test001", True)

        entry = await mgr.get_or_create("p2p:ou_test001")
        assert entry.verbose is True

        await mgr.update_verbose("p2p:ou_test001", False)
        entry = await mgr.get_or_create("p2p:ou_test001")
        assert entry.verbose is False


# ── append ─────────────────────────────────────────────────────


class TestAppend:
    async def test_writes_user_and_assistant(self, mgr, tmp_path):
        """写入 user + assistant 消息到 JSONL"""
        entry = await mgr.get_or_create("p2p:ou_test001")
        await mgr.append(
            entry.id,
            user="你好",
            feishu_msg_id="om_001",
            assistant="你好！有什么可以帮你的？",
        )

        jsonl_path = tmp_path / "sessions" / f"{entry.id}.jsonl"
        lines = jsonl_path.read_text().strip().split("\n")
        # meta + user + assistant = 3 lines
        assert len(lines) == 3
        user_msg = json.loads(lines[1])
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "你好"
        assert user_msg["feishu_msg_id"] == "om_001"
        asst_msg = json.loads(lines[2])
        assert asst_msg["role"] == "assistant"
        assert asst_msg["content"] == "你好！有什么可以帮你的？"

    async def test_increments_message_count(self, mgr):
        """append 后 message_count 应更新"""
        entry = await mgr.get_or_create("p2p:ou_test001")
        assert entry.message_count == 0

        await mgr.append(
            entry.id,
            user="hi",
            feishu_msg_id="om_001",
            assistant="hello",
        )
        updated = await mgr.get_or_create("p2p:ou_test001")
        assert updated.message_count == 2  # user + assistant


# ── load_history ───────────────────────────────────────────────


class TestLoadHistory:
    async def test_returns_messages(self, mgr):
        """读取 JSONL 消息（跳过 meta 行）"""
        entry = await mgr.get_or_create("p2p:ou_test001")
        await mgr.append(
            entry.id, user="q1", feishu_msg_id="om_1", assistant="a1"
        )
        history = await mgr.load_history(entry.id)
        assert len(history) == 2
        assert all(isinstance(m, MessageEntry) for m in history)
        assert history[0].role == "user"
        assert history[0].content == "q1"
        assert history[1].role == "assistant"

    async def test_respects_max_turns(self, mgr):
        """max_turns 截断到最近 N 条消息"""
        entry = await mgr.get_or_create("p2p:ou_test001")
        for i in range(10):
            await mgr.append(
                entry.id,
                user=f"q{i}",
                feishu_msg_id=f"om_{i}",
                assistant=f"a{i}",
            )
        history = await mgr.load_history(entry.id, max_turns=4)
        assert len(history) == 4
        # 应该是最后 4 条
        assert history[0].content == "q8"
        assert history[3].content == "a9"

    async def test_empty_session(self, mgr):
        """新 session 返回空历史"""
        entry = await mgr.get_or_create("p2p:ou_test001")
        history = await mgr.load_history(entry.id)
        assert history == []


# ── 文件安全 ───────────────────────────────────────────────────


class TestFileSafety:
    async def test_no_tmp_file_after_write(self, mgr, tmp_path):
        """write-then-rename 后不应残留 .tmp 文件"""
        await mgr.get_or_create("p2p:ou_test001")
        tmp_file = tmp_path / "sessions" / "index.json.tmp"
        assert not tmp_file.exists()

    async def test_concurrent_get_or_create(self, mgr):
        """多个并发 get_or_create 不会损坏 index.json"""
        keys = [f"p2p:ou_user{i}" for i in range(20)]
        results = await asyncio.gather(
            *[mgr.get_or_create(k) for k in keys]
        )
        # 所有返回的 session 应有效
        assert len(results) == 20
        assert len({r.id for r in results}) == 20  # 全部唯一

        # 再次读取验证一致性
        for key in keys:
            entry = await mgr.get_or_create(key)
            assert entry.id.startswith("s-")


# ── clear_all ──────────────────────────────────────────────────


class TestClearAll:
    async def test_removes_all_data(self, mgr, tmp_path):
        """清空所有 session 数据"""
        await mgr.get_or_create("p2p:ou_a")
        await mgr.get_or_create("group:oc_b")

        await mgr.clear_all()

        # index.json 应为空或不存在
        index_path = tmp_path / "sessions" / "index.json"
        if index_path.exists():
            data = json.loads(index_path.read_text())
            assert data == {}

        # JSONL 文件应被删除
        jsonl_files = list((tmp_path / "sessions").glob("s-*.jsonl"))
        assert len(jsonl_files) == 0

    async def test_can_create_after_clear(self, mgr):
        """clear 后可以正常创建新 session"""
        await mgr.get_or_create("p2p:ou_a")
        await mgr.clear_all()
        entry = await mgr.get_or_create("p2p:ou_a")
        assert entry.id.startswith("s-")
