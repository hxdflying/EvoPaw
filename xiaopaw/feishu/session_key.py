"""routing_key 解析 — 飞书事件字段映射为统一路由键

路由规则:
- 单聊: p2p:{open_id}
- 群聊: group:{chat_id}
- 话题: thread:{chat_id}:{thread_id}
"""

from __future__ import annotations


def resolve_routing_key(
    chat_type: str,
    sender_id: str,
    chat_id: str,
    thread_id: str | None,
) -> str:
    """将飞书事件字段映射为 routing_key 字符串"""
    if chat_type == "p2p":
        return f"p2p:{sender_id}"
    if thread_id:
        return f"thread:{chat_id}:{thread_id}"
    return f"group:{chat_id}"
