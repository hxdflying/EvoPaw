"""SenderProtocol 扩展测试

测试范围：
- SenderProtocol 包含新增的 send_thinking / update_card / send_text 方法签名
- FeishuSender 和 CaptureSender 都满足扩展后的 SenderProtocol
"""

from __future__ import annotations

import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock

from xiaopaw.models import SenderProtocol
from xiaopaw.feishu.sender import FeishuSender
from xiaopaw.api.capture_sender import CaptureSender


class TestSenderProtocolMethods:
    """SenderProtocol 必须定义所有 4 个方法"""

    def test_protocol_has_send_method(self):
        """SenderProtocol 包含 send 方法"""
        assert hasattr(SenderProtocol, "send")

    def test_protocol_has_send_thinking_method(self):
        """SenderProtocol 包含 send_thinking 方法"""
        assert hasattr(SenderProtocol, "send_thinking")

    def test_protocol_has_update_card_method(self):
        """SenderProtocol 包含 update_card 方法"""
        assert hasattr(SenderProtocol, "update_card")

    def test_protocol_has_send_text_method(self):
        """SenderProtocol 包含 send_text 方法"""
        assert hasattr(SenderProtocol, "send_text")


class TestFeishuSenderProtocolCompliance:
    """FeishuSender 满足扩展后的 SenderProtocol"""

    def test_feishu_sender_has_send_thinking(self):
        client = MagicMock()
        sender = FeishuSender(client=client)
        assert hasattr(sender, "send_thinking")
        assert callable(sender.send_thinking)

    def test_feishu_sender_has_update_card(self):
        client = MagicMock()
        sender = FeishuSender(client=client)
        assert hasattr(sender, "update_card")
        assert callable(sender.update_card)

    def test_feishu_sender_has_send_text(self):
        client = MagicMock()
        sender = FeishuSender(client=client)
        assert hasattr(sender, "send_text")
        assert callable(sender.send_text)

    def test_feishu_sender_send_thinking_is_coroutine(self):
        """send_thinking 是 async 方法"""
        client = MagicMock()
        sender = FeishuSender(client=client)
        assert inspect.iscoroutinefunction(sender.send_thinking)

    def test_feishu_sender_update_card_is_coroutine(self):
        """update_card 是 async 方法"""
        client = MagicMock()
        sender = FeishuSender(client=client)
        assert inspect.iscoroutinefunction(sender.update_card)

    def test_feishu_sender_send_text_is_coroutine(self):
        """send_text 是 async 方法"""
        client = MagicMock()
        sender = FeishuSender(client=client)
        assert inspect.iscoroutinefunction(sender.send_text)


class TestCaptureSenderProtocolCompliance:
    """CaptureSender 满足扩展后的 SenderProtocol"""

    def test_capture_sender_has_send_thinking(self):
        sender = CaptureSender()
        assert hasattr(sender, "send_thinking")
        assert callable(sender.send_thinking)

    def test_capture_sender_has_update_card(self):
        sender = CaptureSender()
        assert hasattr(sender, "update_card")
        assert callable(sender.update_card)

    def test_capture_sender_has_send_text(self):
        sender = CaptureSender()
        assert hasattr(sender, "send_text")
        assert callable(sender.send_text)

    def test_capture_sender_send_thinking_is_coroutine(self):
        sender = CaptureSender()
        assert inspect.iscoroutinefunction(sender.send_thinking)

    def test_capture_sender_update_card_is_coroutine(self):
        sender = CaptureSender()
        assert inspect.iscoroutinefunction(sender.update_card)

    def test_capture_sender_send_text_is_coroutine(self):
        sender = CaptureSender()
        assert inspect.iscoroutinefunction(sender.send_text)
