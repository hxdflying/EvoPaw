"""Session 数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SessionEntry:
    """index.json 中的单个 session 元数据快照"""

    id: str  # "s-{uuid}"
    created_at: str  # ISO 8601
    verbose: bool = False
    message_count: int = 0


@dataclass(frozen=True)
class RoutingEntry:
    """index.json 中一个 routing_key 的完整数据"""

    active_session_id: str
    sessions: list[SessionEntry] = field(default_factory=list)


@dataclass(frozen=True)
class MessageEntry:
    """JSONL 中的一条对话消息"""

    role: str  # "user" | "assistant"
    content: str
    ts: int  # 毫秒时间戳
    feishu_msg_id: str | None = None
