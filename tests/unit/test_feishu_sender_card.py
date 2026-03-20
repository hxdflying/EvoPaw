"""FeishuSender 卡片消息 + Loading 效果 + Markdown 渲染单元测试

测试范围：
- _build_card: 构建飞书交互式卡片 JSON
- send_thinking: 发送 Loading 卡片，返回 card_msg_id
- update_card: PATCH 更新已发送卡片
- send_text: 发送纯文本消息（slash 命令用）
- send: 改为发送 interactive 卡片（lark_md 格式）
"""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from xiaopaw.feishu.sender import FeishuSender


# ── Test Helpers ──────────────────────────────────────────────


def _make_client(success: bool = True, msg_id: str = "om_card_001") -> MagicMock:
    """构造 lark-oapi Client mock，支持 acreate / areply / apatch。"""
    resp = MagicMock()
    resp.success.return_value = success
    resp.code = 0 if success else 400
    resp.msg = "ok" if success else "Feishu API error"

    # acreate 返回带 data.message_id 的 resp
    data = MagicMock()
    data.message_id = msg_id
    resp.data = data

    client = MagicMock()
    client.im.v1.message.acreate = AsyncMock(return_value=resp)
    client.im.v1.message.areply = AsyncMock(return_value=resp)
    client.im.v1.message.apatch = AsyncMock(return_value=resp)
    return client


# ── _build_card ────────────────────────────────────────────────


class TestBuildCard:
    """_build_card: 构建飞书交互式卡片 JSON"""

    def test_returns_valid_json_string(self):
        """返回值是合法的 JSON 字符串"""
        client = _make_client()
        sender = FeishuSender(client=client)
        result = sender._build_card("hello world")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_card_has_config_wide_screen_mode(self):
        """卡片包含 config.wide_screen_mode = true"""
        client = _make_client()
        sender = FeishuSender(client=client)
        parsed = json.loads(sender._build_card("text"))
        assert parsed["config"]["wide_screen_mode"] is True

    def test_card_has_elements_with_div(self):
        """卡片 elements 包含 tag=div 的元素"""
        client = _make_client()
        sender = FeishuSender(client=client)
        parsed = json.loads(sender._build_card("text"))
        elements = parsed["elements"]
        assert len(elements) >= 1
        assert elements[0]["tag"] == "div"

    def test_card_div_text_tag_is_lark_md(self):
        """div 元素的 text.tag 为 lark_md"""
        client = _make_client()
        sender = FeishuSender(client=client)
        parsed = json.loads(sender._build_card("text"))
        text_block = parsed["elements"][0]["text"]
        assert text_block["tag"] == "lark_md"

    def test_card_content_embedded(self):
        """传入的 content 嵌入到 div text content 中"""
        client = _make_client()
        sender = FeishuSender(client=client)
        content = "## 标题\n\n这是 **Markdown** 内容"
        parsed = json.loads(sender._build_card(content))
        text_block = parsed["elements"][0]["text"]
        assert text_block["content"] == content

    def test_card_with_empty_content(self):
        """空字符串 content 也能生成合法卡片"""
        client = _make_client()
        sender = FeishuSender(client=client)
        result = sender._build_card("")
        parsed = json.loads(result)
        assert parsed["elements"][0]["text"]["content"] == ""

    def test_card_with_special_characters(self):
        """包含 Unicode / emoji / 引号的 content 不破坏 JSON"""
        client = _make_client()
        sender = FeishuSender(client=client)
        content = '你好 "world" \n emoji: 🐾 <script>alert(1)</script>'
        result = sender._build_card(content)
        parsed = json.loads(result)  # 不应抛异常
        assert parsed["elements"][0]["text"]["content"] == content


# ── send_thinking ──────────────────────────────────────────────


class TestSendThinking:
    """send_thinking: 发送 '思考中' Loading 卡片，返回 card_msg_id"""

    async def test_p2p_returns_card_msg_id(self):
        """p2p 路由时返回 acreate 响应的 message_id"""
        client = _make_client(msg_id="om_thinking_001")
        sender = FeishuSender(client=client)
        card_msg_id = await sender.send_thinking("p2p:ou_abc", "root_001")
        assert card_msg_id == "om_thinking_001"

    async def test_group_returns_card_msg_id(self):
        """group 路由时返回 acreate 响应的 message_id"""
        client = _make_client(msg_id="om_thinking_002")
        sender = FeishuSender(client=client)
        card_msg_id = await sender.send_thinking("group:oc_chat001", "root_001")
        assert card_msg_id == "om_thinking_002"

    async def test_thread_returns_card_msg_id(self):
        """thread 路由时返回 areply 响应的 message_id"""
        client = _make_client(msg_id="om_thinking_003")
        sender = FeishuSender(client=client)
        card_msg_id = await sender.send_thinking(
            "thread:oc_chat:thread_001", "root_msg_001"
        )
        assert card_msg_id == "om_thinking_003"

    async def test_p2p_calls_acreate_with_interactive_type(self):
        """p2p send_thinking 通过 acreate 发送 interactive 类型消息"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_thinking("p2p:ou_abc", "root_001")
        client.im.v1.message.acreate.assert_called_once()
        client.im.v1.message.areply.assert_not_called()

    async def test_group_calls_acreate_with_interactive_type(self):
        """group send_thinking 通过 acreate 发送"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_thinking("group:oc_chat001", "root_001")
        client.im.v1.message.acreate.assert_called_once()

    async def test_thread_calls_areply(self):
        """thread send_thinking 通过 areply 回复"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_thinking("thread:oc_chat:thread_001", "root_msg_001")
        client.im.v1.message.areply.assert_called_once()
        client.im.v1.message.acreate.assert_not_called()

    async def test_thinking_content_is_interactive_card(self):
        """send_thinking 发出的消息类型为 interactive，内容为合法卡片 JSON"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_thinking("p2p:ou_abc", "root_001")

        call_args = client.im.v1.message.acreate.call_args
        # 从 CreateMessageRequest 参数中获取 request_body
        # lark-oapi builder pattern: acreate 接收 request 对象
        req = call_args[0][0]  # 位置参数 request
        body = req.request_body
        assert body.msg_type == "interactive"
        card_json = json.loads(body.content)
        assert "elements" in card_json

    async def test_thinking_card_contains_loading_text(self):
        """send_thinking 发出的卡片内容包含 '思考中' 之类的 loading 提示"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_thinking("p2p:ou_abc", "root_001")

        req = client.im.v1.message.acreate.call_args[0][0]
        card_json = json.loads(req.request_body.content)
        text_content = card_json["elements"][0]["text"]["content"]
        # 应包含某种 loading 指示文字
        assert len(text_content) > 0

    async def test_api_failure_returns_none(self):
        """API 调用失败时 send_thinking 返回 None（不阻断主流程）"""
        client = _make_client(success=False)
        sender = FeishuSender(client=client)
        card_msg_id = await sender.send_thinking("p2p:ou_abc", "root_001")
        assert card_msg_id is None

    async def test_exception_returns_none(self):
        """acreate 抛异常时 send_thinking 返回 None"""
        client = MagicMock()
        client.im.v1.message.acreate = AsyncMock(
            side_effect=RuntimeError("network error")
        )
        sender = FeishuSender(client=client)
        card_msg_id = await sender.send_thinking("p2p:ou_abc", "root_001")
        assert card_msg_id is None

    async def test_unknown_routing_key_returns_none(self):
        """未知 routing_key 时 send_thinking 返回 None"""
        client = _make_client()
        sender = FeishuSender(client=client)
        card_msg_id = await sender.send_thinking("unknown:foo", "root_001")
        assert card_msg_id is None


# ── update_card ────────────────────────────────────────────────


class TestUpdateCard:
    """update_card: PATCH 更新已发送的卡片内容"""

    async def test_calls_apatch_with_message_id(self):
        """update_card 调用 apatch，传入正确的 card_msg_id"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.update_card("om_card_001", "更新后的内容")
        client.im.v1.message.apatch.assert_called_once()

    async def test_patch_content_is_interactive_card(self):
        """patch 的 content 是合法的 interactive 卡片 JSON"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.update_card("om_card_001", "新内容")

        req = client.im.v1.message.apatch.call_args[0][0]
        body = req.request_body
        card_json = json.loads(body.content)
        assert "elements" in card_json
        assert card_json["elements"][0]["text"]["content"] == "新内容"

    async def test_patch_uses_correct_message_id(self):
        """apatch 请求中 message_id 与传入一致"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.update_card("om_specific_id", "content")

        req = client.im.v1.message.apatch.call_args[0][0]
        assert req.message_id == "om_specific_id"

    async def test_api_failure_raises_runtime_error(self):
        """patch API 失败时抛出 RuntimeError"""
        client = _make_client(success=False)
        sender = FeishuSender(client=client)
        with pytest.raises(RuntimeError, match="Feishu patch message failed"):
            await sender.update_card("om_card_001", "内容")

    async def test_update_with_markdown_content(self):
        """Markdown 内容正确嵌入卡片"""
        client = _make_client()
        sender = FeishuSender(client=client)
        md_content = "## 结果\n\n- 条目 1\n- 条目 2\n\n**完成**"
        await sender.update_card("om_001", md_content)

        req = client.im.v1.message.apatch.call_args[0][0]
        card_json = json.loads(req.request_body.content)
        assert card_json["elements"][0]["text"]["content"] == md_content

    async def test_update_with_empty_content(self):
        """空字符串 content 也能正常 patch"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.update_card("om_001", "")
        client.im.v1.message.apatch.assert_called_once()


# ── send_text ──────────────────────────────────────────────────


class TestSendText:
    """send_text: 发送纯文本消息（slash 命令专用）"""

    async def test_p2p_calls_acreate(self):
        """p2p 路由时调用 acreate"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_text("p2p:ou_abc", "这是纯文本", "root_001")
        client.im.v1.message.acreate.assert_called_once()

    async def test_group_calls_acreate(self):
        """group 路由时调用 acreate"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_text("group:oc_chat001", "文本内容", "root_001")
        client.im.v1.message.acreate.assert_called_once()

    async def test_thread_calls_areply(self):
        """thread 路由时调用 areply"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_text("thread:oc_chat:t001", "文本", "root_001")
        client.im.v1.message.areply.assert_called_once()

    async def test_content_is_text_type(self):
        """send_text 发出的消息类型为 text（不是 interactive）"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_text("p2p:ou_abc", "hello", "root_001")

        req = client.im.v1.message.acreate.call_args[0][0]
        assert req.request_body.msg_type == "text"

    async def test_text_content_format(self):
        """send_text 的 content 格式为 JSON {text: ...}"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_text("p2p:ou_abc", "hello world", "root_001")

        req = client.im.v1.message.acreate.call_args[0][0]
        content = json.loads(req.request_body.content)
        assert content == {"text": "hello world"}

    async def test_unknown_routing_key_does_nothing(self):
        """未知 routing_key 不调用任何 API"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send_text("unknown:foo", "text", "root_001")
        client.im.v1.message.acreate.assert_not_called()
        client.im.v1.message.areply.assert_not_called()


# ── send (改为 interactive 卡片) ────────────────────────────────


class TestSendInteractiveCard:
    """send: 改为发送 interactive 卡片（lark_md Markdown 格式）"""

    async def test_send_p2p_uses_interactive_type(self):
        """send p2p 消息类型改为 interactive"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send("p2p:ou_abc", "hello", "msg_001")

        req = client.im.v1.message.acreate.call_args[0][0]
        assert req.request_body.msg_type == "interactive"

    async def test_send_group_uses_interactive_type(self):
        """send group 消息类型改为 interactive"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send("group:oc_chat001", "hello", "msg_001")

        req = client.im.v1.message.acreate.call_args[0][0]
        assert req.request_body.msg_type == "interactive"

    async def test_send_thread_uses_interactive_type(self):
        """send thread 消息类型改为 interactive"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send("thread:oc_chat:t001", "hello", "msg_001")

        req = client.im.v1.message.areply.call_args[0][0]
        assert req.request_body.msg_type == "interactive"

    async def test_send_content_is_card_json(self):
        """send 的 content 是合法的卡片 JSON，包含 lark_md"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send("p2p:ou_abc", "## 标题\n内容", "msg_001")

        req = client.im.v1.message.acreate.call_args[0][0]
        card_json = json.loads(req.request_body.content)
        assert card_json["elements"][0]["text"]["tag"] == "lark_md"
        assert card_json["elements"][0]["text"]["content"] == "## 标题\n内容"

    async def test_send_success_no_exception(self):
        """send 成功不抛异常"""
        client = _make_client()
        sender = FeishuSender(client=client)
        await sender.send("p2p:ou_abc", "内容", "msg_001")  # should not raise
