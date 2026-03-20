"""FeishuSender 单元测试"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from xiaopaw.feishu.sender import FeishuSender


def _make_client(success: bool = True) -> MagicMock:
    """构造 lark-oapi Client mock。"""
    resp = MagicMock()
    resp.success.return_value = success
    resp.code = 0 if success else 400
    resp.msg = "ok" if success else "Feishu API error"

    client = MagicMock()
    client.im.v1.message.acreate = AsyncMock(return_value=resp)
    client.im.v1.message.areply = AsyncMock(return_value=resp)
    return client


class TestFeishuSenderRouting:
    async def test_send_p2p_calls_acreate(self):
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send("p2p:ou_abc123", "hello", "msg_001")
        client.im.v1.message.acreate.assert_called_once()
        client.im.v1.message.areply.assert_not_called()

    async def test_send_group_calls_acreate(self):
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send("group:oc_chat001", "hello", "msg_001")
        client.im.v1.message.acreate.assert_called_once()
        client.im.v1.message.areply.assert_not_called()

    async def test_send_thread_calls_areply(self):
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send("thread:oc_chat:thread_001", "hello", "msg_001")
        client.im.v1.message.areply.assert_called_once()
        client.im.v1.message.acreate.assert_not_called()

    async def test_send_unknown_routing_key_does_nothing(self):
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send("unknown:foo", "hello", "msg_001")
        client.im.v1.message.acreate.assert_not_called()
        client.im.v1.message.areply.assert_not_called()


class TestFeishuSenderErrorHandling:
    async def test_send_p2p_api_failure_raises(self):
        client = _make_client(success=False)
        sender = FeishuSender(client=client)
        with pytest.raises(RuntimeError, match="Feishu create message failed"):
            await sender._send_p2p("ou_abc", "text", '{"text":"hi"}', "msg_001")

    async def test_send_group_api_failure_raises(self):
        client = _make_client(success=False)
        sender = FeishuSender(client=client)
        with pytest.raises(RuntimeError, match="Feishu create message failed"):
            await sender._send_group("oc_chat", "text", '{"text":"hi"}', "msg_001")

    async def test_send_thread_api_failure_raises(self):
        client = _make_client(success=False)
        sender = FeishuSender(client=client)
        with pytest.raises(RuntimeError, match="Feishu reply message failed"):
            await sender._send_thread("msg_001", "text", '{"text":"hi"}', "msg_001")

    async def test_send_p2p_success(self):
        client = _make_client(success=True)
        sender = FeishuSender(client=client)
        # should not raise
        await sender._send_p2p("ou_abc", "text", '{"text":"hi"}', "msg_001")

    async def test_send_group_success(self):
        client = _make_client(success=True)
        sender = FeishuSender(client=client)
        await sender._send_group("oc_chat", "text", '{"text":"hi"}', "msg_001")

    async def test_send_thread_success(self):
        client = _make_client(success=True)
        sender = FeishuSender(client=client)
        await sender._send_thread("msg_001", "text", '{"text":"hi"}', "msg_001")
