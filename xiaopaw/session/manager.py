"""SessionManager — Session 生命周期管理、index.json 路由映射、JSONL 对话历史

并发安全:
- index.json: asyncio.Lock + write-then-rename
- JSONL: per-session asyncio.Lock + flush + fsync
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from xiaopaw.session.models import MessageEntry, SessionEntry


class SessionManager:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._sessions_dir = data_dir / "sessions"
        self._index_path = self._sessions_dir / "index.json"
        self._index_lock = asyncio.Lock()
        self._jsonl_locks: dict[str, asyncio.Lock] = {}
        self._ensure_dirs()

    # ── 公开方法 ───────────────────────────────────────────────

    async def get_or_create(self, routing_key: str) -> SessionEntry:
        """获取 routing_key 的当前活跃 session，不存在则创建"""
        async with self._index_lock:
            index = self._read_index()
            if routing_key not in index:
                entry = self._make_new_session()
                index[routing_key] = {
                    "active_session_id": entry.id,
                    "sessions": [self._session_to_dict(entry)],
                }
                self._write_index(index)
                self._write_jsonl_meta(entry.id, routing_key)
                return entry

            routing = index[routing_key]
            active_id = routing["active_session_id"]
            for s in routing["sessions"]:
                if s["id"] == active_id:
                    return self._dict_to_session(s)

            # active_session_id 指向不存在的 session（不应发生，兜底处理）
            return self._dict_to_session(routing["sessions"][-1])

    async def create_new_session(self, routing_key: str) -> SessionEntry:
        """为 routing_key 创建新 session 并切换为 active"""
        async with self._index_lock:
            index = self._read_index()
            if routing_key not in index:
                index[routing_key] = {
                    "active_session_id": "",
                    "sessions": [],
                }

            entry = self._make_new_session()
            routing = index[routing_key]
            routing["sessions"].append(self._session_to_dict(entry))
            routing["active_session_id"] = entry.id
            self._write_index(index)

        self._write_jsonl_meta(entry.id, routing_key)
        return entry

    async def update_verbose(self, routing_key: str, verbose: bool) -> None:
        """修改当前活跃 session 的 verbose 标志"""
        async with self._index_lock:
            index = self._read_index()
            routing = index.get(routing_key)
            if routing is None:
                return
            active_id = routing["active_session_id"]
            for s in routing["sessions"]:
                if s["id"] == active_id:
                    s["verbose"] = verbose
                    break
            self._write_index(index)

    async def load_history(
        self, session_id: str, max_turns: int = 20
    ) -> list[MessageEntry]:
        """读取 session 的对话历史，跳过 meta 行，截断到最近 max_turns 条"""
        jsonl_path = self._jsonl_path(session_id)
        if not jsonl_path.exists():
            return []

        messages: list[MessageEntry] = []
        for line in jsonl_path.read_text().strip().split("\n"):
            if not line:
                continue
            record = json.loads(line)
            if record.get("type") != "message":
                continue
            messages.append(
                MessageEntry(
                    role=record["role"],
                    content=record["content"],
                    ts=record.get("ts", 0),
                    feishu_msg_id=record.get("feishu_msg_id"),
                )
            )

        if len(messages) > max_turns:
            messages = messages[-max_turns:]
        return messages

    async def append(
        self,
        session_id: str,
        *,
        user: str,
        feishu_msg_id: str,
        assistant: str,
    ) -> None:
        """追加 user + assistant 消息到 JSONL，同步更新 message_count"""
        ts_ms = int(time.time() * 1000)
        entries = [
            {
                "type": "message",
                "role": "user",
                "content": user,
                "ts": ts_ms,
                "feishu_msg_id": feishu_msg_id,
            },
            {
                "type": "message",
                "role": "assistant",
                "content": assistant,
                "ts": ts_ms,
            },
        ]

        lock = self._jsonl_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            jsonl_path = self._jsonl_path(session_id)
            with open(jsonl_path, "a") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

        # 更新 index.json 中的 message_count
        async with self._index_lock:
            index = self._read_index()
            for routing in index.values():
                for s in routing["sessions"]:
                    if s["id"] == session_id:
                        s["message_count"] = s.get("message_count", 0) + 2
                        self._write_index(index)
                        return

    async def get_session_info(self, routing_key: str) -> SessionEntry:
        """获取当前活跃 session 信息（同 get_or_create 但不创建新的）"""
        return await self.get_or_create(routing_key)

    async def clear_all(self) -> None:
        """清空所有 session 数据（供 TestAPI 使用）"""
        async with self._index_lock:
            # 删除所有 JSONL
            for f in self._sessions_dir.glob("s-*.jsonl"):
                f.unlink()
            # 清空 index
            self._write_index({})
            self._jsonl_locks.clear()

    # ── 内部方法 ───────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        # 清理上次崩溃残留的 .tmp 文件
        tmp_file = self._index_path.with_suffix(".json.tmp")
        if tmp_file.exists():
            tmp_file.unlink()

    def _read_index(self) -> dict:
        if not self._index_path.exists():
            return {}
        return json.loads(self._index_path.read_text())

    def _write_index(self, data: dict) -> None:
        """write-then-rename: 防止写入中途崩溃导致文件损坏"""
        tmp_path = self._index_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp_path.rename(self._index_path)

    def _jsonl_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.jsonl"

    def _write_jsonl_meta(self, session_id: str, routing_key: str) -> None:
        """写入 JSONL 文件的 meta 行"""
        meta = {
            "type": "meta",
            "session_id": session_id,
            "routing_key": routing_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        jsonl_path = self._jsonl_path(session_id)
        with open(jsonl_path, "w") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    @staticmethod
    def _make_new_session() -> SessionEntry:
        return SessionEntry(
            id=f"s-{uuid.uuid4().hex[:12]}",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _session_to_dict(entry: SessionEntry) -> dict:
        return {
            "id": entry.id,
            "created_at": entry.created_at,
            "verbose": entry.verbose,
            "message_count": entry.message_count,
        }

    @staticmethod
    def _dict_to_session(d: dict) -> SessionEntry:
        return SessionEntry(
            id=d["id"],
            created_at=d["created_at"],
            verbose=d.get("verbose", False),
            message_count=d.get("message_count", 0),
        )
