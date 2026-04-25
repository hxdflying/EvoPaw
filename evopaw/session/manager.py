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
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from evopaw.session.models import MessageEntry, SessionEntry


# 同时活跃的 per-session JSONL Lock 数量上限。
# 超出后按 LRU 顺序踢出最旧的、未被持有的 entry，避免长期运行时 dict 单调增长（M-1）。
_JSONL_LOCKS_MAX = 256


class SessionManager:
    def __init__(self, data_dir: Path, jsonl_locks_max: int = _JSONL_LOCKS_MAX) -> None:
        self._data_dir = data_dir
        self._sessions_dir = data_dir / "sessions"
        self._index_path = self._sessions_dir / "index.json"
        self._index_lock = asyncio.Lock()
        # OrderedDict 实现 LRU；新 entry 加入末尾，每次使用 move_to_end 刷新。
        self._jsonl_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._jsonl_locks_max = max(1, int(jsonl_locks_max))
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
        for line in jsonl_path.read_text(encoding="utf-8").strip().split("\n"):
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

        if max_turns > 0 and len(messages) > max_turns:
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

        lock = self._acquire_jsonl_lock(session_id)
        async with lock:
            jsonl_path = self._jsonl_path(session_id)
            with open(jsonl_path, "a", encoding="utf-8") as f:
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
        """清空所有 session 数据（仅供 TestAPI 在静默期使用）。

        ⚠️ 使用前提（M-2）：调用方需保证当前**没有任何 worker 正在 append**。
        - 本方法只持有 ``_index_lock``，并不阻塞已经获取了 per-session jsonl_lock 的
          ``append`` 协程。如果在 append 进行中调用，可能产生孤儿 JSONL 文件
          （append 写入磁盘后 jsonl 文件已被删，写入再生成一个新文件）。
        - TestAPI 通过 HTTP 触发本方法，且测试用例都是"先发消息→等回复→clear"的
          串行流程；当前用法是安全的。
        - 若未来需要在并发场景调用，应先 ``shutdown`` Runner 让 worker 全部退出再调。

        防护：跳过当前**仍被持有**的 jsonl_lock entry（可能正在 append），
        只清理已释放的，避免不一致。
        """
        async with self._index_lock:
            # 删除所有 JSONL
            for f in self._sessions_dir.glob("s-*.jsonl"):
                f.unlink()
            # 清空 index
            self._write_index({})
            # 仅清理未持有的 jsonl_locks；持有中的留给 append 自行释放
            held = {sid for sid, lock in self._jsonl_locks.items() if lock.locked()}
            for sid in list(self._jsonl_locks.keys()):
                if sid not in held:
                    del self._jsonl_locks[sid]

    # ── 内部方法 ───────────────────────────────────────────────

    def _acquire_jsonl_lock(self, session_id: str) -> asyncio.Lock:
        """获取 session_id 对应的 JSONL Lock，按 LRU 控制总量（M-1）。

        - 命中：刷新 LRU 顺序，复用 Lock 实例
        - 未命中：创建新 Lock；超过 _jsonl_locks_max 时踢出最旧的、当前未被持有的 entry
        - 已被持有的 Lock 永不踢出，避免并发写入同一 session 导致冲突
        """
        existing = self._jsonl_locks.get(session_id)
        if existing is not None:
            self._jsonl_locks.move_to_end(session_id)
            return existing

        lock = asyncio.Lock()
        self._jsonl_locks[session_id] = lock

        while len(self._jsonl_locks) > self._jsonl_locks_max:
            evicted = False
            for k, v in list(self._jsonl_locks.items()):
                if k == session_id:
                    continue
                if not v.locked():
                    del self._jsonl_locks[k]
                    evicted = True
                    break
            if not evicted:
                # 全部被持有（极端情况）：停止踢出，让 dict 暂时超限，下次再清理
                break
        return lock

    def _ensure_dirs(self) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        # 清理上次崩溃残留的 .tmp 文件
        tmp_file = self._index_path.with_suffix(".json.tmp")
        if tmp_file.exists():
            tmp_file.unlink()

    def _read_index(self) -> dict:
        if not self._index_path.exists():
            return {}
        return json.loads(self._index_path.read_text(encoding="utf-8"))

    def _write_index(self, data: dict) -> None:
        """write-then-rename: 防止写入中途崩溃导致文件损坏"""
        tmp_path = self._index_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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
        with open(jsonl_path, "w", encoding="utf-8") as f:
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
