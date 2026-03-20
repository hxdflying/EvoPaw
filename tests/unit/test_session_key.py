"""feishu/session_key.py 单元测试"""

from __future__ import annotations

import pytest

from xiaopaw.feishu.session_key import resolve_routing_key


class TestResolveRoutingKey:
    """resolve_routing_key: 飞书事件字段 → routing_key 字符串"""

    def test_p2p_chat(self):
        """单聊 → p2p:{open_id}"""
        key = resolve_routing_key(
            chat_type="p2p",
            sender_id="ou_abc123",
            chat_id="oc_xxx",
            thread_id=None,
        )
        assert key == "p2p:ou_abc123"

    def test_group_chat(self):
        """普通群聊 → group:{chat_id}"""
        key = resolve_routing_key(
            chat_type="group",
            sender_id="ou_abc123",
            chat_id="oc_chat456",
            thread_id=None,
        )
        assert key == "group:oc_chat456"

    def test_group_chat_empty_thread(self):
        """群聊 thread_id 为空字符串 → group:{chat_id}"""
        key = resolve_routing_key(
            chat_type="group",
            sender_id="ou_abc123",
            chat_id="oc_chat456",
            thread_id="",
        )
        assert key == "group:oc_chat456"

    def test_thread_chat(self):
        """话题群 → thread:{chat_id}:{thread_id}"""
        key = resolve_routing_key(
            chat_type="group",
            sender_id="ou_abc123",
            chat_id="oc_chat789",
            thread_id="ot_topic001",
        )
        assert key == "thread:oc_chat789:ot_topic001"

    def test_p2p_ignores_chat_id(self):
        """单聊时 chat_id 不影响结果"""
        k1 = resolve_routing_key("p2p", "ou_a", "oc_1", None)
        k2 = resolve_routing_key("p2p", "ou_a", "oc_2", None)
        assert k1 == k2 == "p2p:ou_a"

    def test_thread_includes_chat_id(self):
        """不同群的同名 thread_id 产生不同 routing_key"""
        k1 = resolve_routing_key("group", "ou_a", "oc_1", "ot_x")
        k2 = resolve_routing_key("group", "ou_a", "oc_2", "ot_x")
        assert k1 != k2
        assert k1 == "thread:oc_1:ot_x"
        assert k2 == "thread:oc_2:ot_x"
