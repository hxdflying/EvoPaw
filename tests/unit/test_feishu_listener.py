"""FeishuListener 单元测试"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xiaopaw.feishu.listener import FeishuListener, _XiaoPawEventHandler, run_forever
from xiaopaw.models import Attachment, InboundMessage


# ── 辅助：构造 bot.added 事件 payload ─────────────────────────────────────────

def _make_bot_added_payload(chat_id: str = "oc_group_001", name: str = "测试群") -> bytes:
    data = {
        "header": {"event_type": "im.chat.member.bot.added_v1"},
        "event": {
            "chat_id": chat_id,
            "name": name,
        },
    }
    return json.dumps(data).encode()


def _make_im_receive_payload(
    msg_type: str = "text",
    content: str = '{"text": "hello"}',
    chat_type: str = "p2p",
    sender_open_id: str = "ou_abc",
    chat_id: str = "oc_001",
    thread_id: str | None = None,
    message_id: str = "om_001",
    create_time: str = "1000000",
) -> bytes:
    data = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": sender_open_id}},
            "message": {
                "chat_type": chat_type,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "message_type": msg_type,
                "content": content,
                "message_id": message_id,
                "root_id": message_id,
                "create_time": create_time,
            },
        },
    }
    return json.dumps(data).encode()


class TestXiaoPawEventHandlerInvalidInput:
    async def test_invalid_json_payload_does_not_raise(self):
        loop = asyncio.get_event_loop()
        on_msg = asyncio.coroutine(lambda _: None) if False else AsyncMockFn()
        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg.call)
        handler.do_without_validation(b"not-valid-json")
        # no exception = pass

    async def test_non_message_event_is_ignored(self):
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg)
        payload = json.dumps({
            "header": {"event_type": "bot.events.v1"},
            "event": {},
        }).encode()
        handler.do_without_validation(payload)
        await asyncio.sleep(0.05)
        assert received == []

    async def test_text_message_dispatched(self):
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg)
        handler.do_without_validation(_make_im_receive_payload())
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].content == "hello"
        assert received[0].routing_key == "p2p:ou_abc"
        assert received[0].msg_id == "om_001"
        assert received[0].ts == 1000000

    async def test_group_message_routing_key(self):
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg)
        handler.do_without_validation(
            _make_im_receive_payload(chat_type="group", chat_id="oc_group_001")
        )
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].routing_key == "group:oc_group_001"

    async def test_invalid_create_time_defaults_to_zero(self):
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg)
        handler.do_without_validation(
            _make_im_receive_payload(create_time="not_a_number")
        )
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].ts == 0


class AsyncMockFn:
    """轻量 async callable mock（不依赖 unittest.mock）。"""

    def __init__(self) -> None:
        self.calls: list = []

    async def call(self, arg: object) -> None:
        self.calls.append(arg)


class TestFeishuListenerExtractContent:
    def test_text_message(self):
        result = FeishuListener._extract_content("text", '{"text": "hello world"}')
        assert result == "hello world"

    def test_empty_content_returns_empty(self):
        result = FeishuListener._extract_content("text", "")
        assert result == ""

    def test_invalid_json_returns_empty(self):
        result = FeishuListener._extract_content("text", "not json {")
        assert result == ""

    def test_other_message_type_returns_empty(self):
        result = FeishuListener._extract_content("image", '{"image_key": "abc"}')
        assert result == ""

    def test_text_with_missing_text_key(self):
        result = FeishuListener._extract_content("text", '{"other": "value"}')
        assert result == ""

    def test_at_mention_text(self):
        result = FeishuListener._extract_content("text", '{"text": "@bot hello"}')
        assert result == "@bot hello"


# ── _extract_attachment ────────────────────────────────────────────────────────


class TestFeishuListenerExtractAttachment:
    """_extract_attachment 静态方法测试（覆盖 lines 147-169）"""

    def test_text_type_returns_none(self):
        assert FeishuListener._extract_attachment("text", '{"text": "hello"}') is None

    def test_unknown_type_returns_none(self):
        assert FeishuListener._extract_attachment("post", "{}") is None

    def test_empty_content_returns_none_for_image(self):
        assert FeishuListener._extract_attachment("image", "") is None

    def test_empty_content_returns_none_for_file(self):
        assert FeishuListener._extract_attachment("file", "") is None

    def test_invalid_json_returns_none(self):
        assert FeishuListener._extract_attachment("image", "not-json{") is None

    def test_image_with_key_returns_attachment(self):
        content = json.dumps({"image_key": "img_key_abc"})
        result = FeishuListener._extract_attachment("image", content)
        assert result is not None
        assert result.msg_type == "image"
        assert result.file_key == "img_key_abc"
        assert result.file_name == "img_key_abc.jpg"

    def test_image_without_key_returns_none(self):
        content = json.dumps({"other": "data"})
        assert FeishuListener._extract_attachment("image", content) is None

    def test_image_with_empty_key_returns_none(self):
        content = json.dumps({"image_key": ""})
        assert FeishuListener._extract_attachment("image", content) is None

    def test_file_with_key_returns_attachment(self):
        content = json.dumps({"file_key": "file_abc", "file_name": "report.pdf"})
        result = FeishuListener._extract_attachment("file", content)
        assert result is not None
        assert result.msg_type == "file"
        assert result.file_key == "file_abc"
        assert result.file_name == "report.pdf"

    def test_file_without_name_uses_key(self):
        content = json.dumps({"file_key": "file_xyz"})
        result = FeishuListener._extract_attachment("file", content)
        assert result is not None
        assert result.file_name == "file_xyz"

    def test_file_without_key_returns_none(self):
        content = json.dumps({"file_name": "report.pdf"})
        assert FeishuListener._extract_attachment("file", content) is None

    def test_file_with_empty_key_returns_none(self):
        content = json.dumps({"file_key": "", "file_name": "report.pdf"})
        assert FeishuListener._extract_attachment("file", content) is None


# ── FeishuListener init & start ────────────────────────────────────────────────


class TestFeishuListenerInit:
    """FeishuListener.__init__ 测试（覆盖 lines 126-127）"""

    def test_init_creates_ws_client(self):
        with patch("xiaopaw.feishu.listener.WSClient") as mock_ws_cls, \
             patch("xiaopaw.feishu.listener._XiaoPawEventHandler") as mock_handler_cls:
            loop = MagicMock()
            on_msg = AsyncMock()
            listener = FeishuListener("app_id_test", "app_secret_test", on_msg, loop)

            # _XiaoPawEventHandler 应以 loop 和 on_message 构建（以及可选的新参数）
            mock_handler_cls.assert_called_once()
            handler_kwargs = mock_handler_cls.call_args.kwargs
            assert handler_kwargs["loop"] is loop
            assert handler_kwargs["on_message"] is on_msg
            # WSClient 应以飞书凭证和 handler 构建
            mock_ws_cls.assert_called_once()
            ws_kwargs = mock_ws_cls.call_args.kwargs
            assert ws_kwargs["app_id"] == "app_id_test"
            assert ws_kwargs["app_secret"] == "app_secret_test"


class TestFeishuListenerStart:
    """FeishuListener.start() 测试（覆盖 lines 136-140）"""

    async def test_start_runs_ws_client_in_executor(self):
        with patch("xiaopaw.feishu.listener.WSClient") as mock_ws_cls, \
             patch("xiaopaw.feishu.listener._XiaoPawEventHandler"):
            loop = asyncio.get_running_loop()
            mock_ws_instance = MagicMock()
            mock_ws_cls.return_value = mock_ws_instance

            listener = FeishuListener("app_id", "app_secret", AsyncMock(), loop)

            with patch.object(loop, "run_in_executor", new=AsyncMock()) as mock_exec:
                await listener.start()
                # start() 应调用 run_in_executor 并传入 ws_client.start
                mock_exec.assert_awaited_once()
                _, fn = mock_exec.call_args.args
                assert fn == mock_ws_instance.start


# ── run_forever ─────────────────────────────────────────────────────────────


class TestRunForever:
    """run_forever 包装函数测试（覆盖 lines 197-199）"""

    async def test_run_forever_calls_start_once(self):
        """run_forever 调用 listener.start()，成功后循环；CancelledError 退出"""
        listener = MagicMock()
        # 第一次成功，第二次抛 CancelledError 退出 while True
        listener.start = AsyncMock(side_effect=[None, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await run_forever(listener)

        assert listener.start.call_count >= 1


# ── _XiaoPawEventHandler exception branch ─────────────────────────────────────


class TestHandlerExceptionInBody:
    """do_without_validation 内部异常不向上传播（覆盖 lines 105-106）"""

    async def test_exception_in_handler_body_does_not_raise(self):
        loop = asyncio.get_event_loop()
        handler = _XiaoPawEventHandler(loop=loop, on_message=AsyncMock())

        # 让 resolve_routing_key 抛异常，触发 except 分支
        with patch(
            "xiaopaw.feishu.listener.resolve_routing_key",
            side_effect=RuntimeError("unexpected error"),
        ):
            # should NOT raise — exception is caught and logged
            handler.do_without_validation(_make_im_receive_payload())


# ═══════════════════════════════════════════════════════════════════════════════
# 功能一：post 富文本消息解析
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractPostText:
    """_extract_post_text 静态方法的单元测试"""

    def test_basic_text_tag(self):
        """单段落单 text tag，提取文字内容"""
        data = {
            "zh_cn": {
                "title": "",
                "content": [[{"tag": "text", "text": "你好世界"}]],
            }
        }
        result = FeishuListener._extract_post_text(data)
        assert result == "你好世界"

    def test_title_prepended_when_non_empty(self):
        """title 非空时拼接在最前面，与 content 用换行分隔"""
        data = {
            "zh_cn": {
                "title": "标题",
                "content": [[{"tag": "text", "text": "正文"}]],
            }
        }
        result = FeishuListener._extract_post_text(data)
        assert result == "标题\n正文"

    def test_empty_title_not_prepended(self):
        """title 为空时不拼接，仅返回正文"""
        data = {
            "zh_cn": {
                "title": "",
                "content": [[{"tag": "text", "text": "正文"}]],
            }
        }
        result = FeishuListener._extract_post_text(data)
        assert result == "正文"

    def test_multiple_paragraphs_joined(self):
        """多段落文字用空格拼接"""
        data = {
            "zh_cn": {
                "content": [
                    [{"tag": "text", "text": "第一段"}],
                    [{"tag": "text", "text": "第二段"}],
                ]
            }
        }
        result = FeishuListener._extract_post_text(data)
        assert result == "第一段 第二段"

    def test_non_text_tags_skipped(self):
        """非 text tag（如 a、at）不被提取"""
        data = {
            "zh_cn": {
                "content": [
                    [
                        {"tag": "text", "text": "点击"},
                        {"tag": "a", "text": "链接", "href": "https://example.com"},
                        {"tag": "text", "text": "查看"},
                    ]
                ]
            }
        }
        result = FeishuListener._extract_post_text(data)
        assert result == "点击 查看"

    def test_fallback_to_root_when_no_zh_cn(self):
        """无 zh_cn 时取根对象"""
        data = {
            "en_us": {
                "title": "Hello",
                "content": [[{"tag": "text", "text": "world"}]],
            }
        }
        # 根对象中没有 title/content，返回空
        result = FeishuListener._extract_post_text(data)
        assert result == ""

    def test_root_fallback_with_content(self):
        """无 zh_cn 时从根对象提取 title 和 content"""
        data = {
            "title": "Root Title",
            "content": [[{"tag": "text", "text": "Root text"}]],
        }
        result = FeishuListener._extract_post_text(data)
        assert result == "Root Title\nRoot text"

    def test_empty_content_list_returns_empty(self):
        """content 为空列表时返回空串"""
        data = {"zh_cn": {"title": "", "content": []}}
        result = FeishuListener._extract_post_text(data)
        assert result == ""

    def test_empty_dict_returns_empty(self):
        """空字典，健壮性测试"""
        result = FeishuListener._extract_post_text({})
        assert result == ""

    def test_none_content_returns_empty(self):
        """content 字段缺失，健壮性测试"""
        result = FeishuListener._extract_post_text({"zh_cn": {"title": "标题"}})
        assert result == ""

    def test_malformed_inner_list_returns_empty(self):
        """content 内部不是列表，不报错"""
        data = {"zh_cn": {"content": "not-a-list"}}
        result = FeishuListener._extract_post_text(data)
        assert result == ""

    def test_strip_whitespace(self):
        """结果经过 strip"""
        data = {
            "zh_cn": {
                "content": [[{"tag": "text", "text": "  前后空格  "}]]
            }
        }
        result = FeishuListener._extract_post_text(data)
        assert result == "前后空格"

    def test_paragraph_with_only_non_text_tags_skipped(self):
        """段落中只有非 text tag 时，段落贡献空字符串，join 后 strip 干净"""
        data = {
            "zh_cn": {
                "content": [
                    [{"tag": "a", "href": "http://x.com", "text": "link"}],
                    [{"tag": "text", "text": "有效"}],
                ]
            }
        }
        result = FeishuListener._extract_post_text(data)
        assert "有效" in result

    def test_paragraph_that_is_not_a_list_is_skipped(self):
        """content 内某段落不是 list（格式异常），应跳过该段落不报错"""
        data = {
            "zh_cn": {
                "content": [
                    "unexpected-string-paragraph",  # 不是 list，应被 continue 跳过
                    [{"tag": "text", "text": "正常段落"}],
                ]
            }
        }
        result = FeishuListener._extract_post_text(data)
        assert result == "正常段落"

    def test_exception_in_post_text_returns_empty(self):
        """_extract_post_text 内部任意异常都 return ""（覆盖 except 分支）"""
        # 传入一个不支持 .get() 的非 dict，触发 except
        result = FeishuListener._extract_post_text(None)  # type: ignore[arg-type]
        assert result == ""


class TestExtractContentPostType:
    """_extract_content 对 post 消息类型的处理"""

    def test_post_returns_text(self):
        """msg_type=post 时应正确提取文字，而非返回空串"""
        content = json.dumps({
            "zh_cn": {
                "title": "标题",
                "content": [[{"tag": "text", "text": "正文"}]],
            }
        })
        result = FeishuListener._extract_content("post", content)
        assert result == "标题\n正文"

    def test_post_invalid_json_returns_empty(self):
        """post 类型但 JSON 无效时返回空串"""
        result = FeishuListener._extract_content("post", "not-json{")
        assert result == ""

    def test_post_empty_content_returns_empty(self):
        """post 类型但 content_json 为空时返回空串"""
        result = FeishuListener._extract_content("post", "")
        assert result == ""

    def test_post_no_zh_cn_and_no_root_content(self):
        """post 类型但无有效内容时返回空串"""
        content = json.dumps({"en_us": {}})
        result = FeishuListener._extract_content("post", content)
        assert result == ""


class TestPostMessageDispatch:
    """post 消息通过事件处理器能正确 dispatch 到 on_message"""

    async def test_post_message_dispatched_with_text(self):
        """完整流程：post payload → dispatcher → InboundMessage.content 含文字"""
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(loop=loop, on_message=on_msg)

        post_content = json.dumps({
            "zh_cn": {
                "title": "会议纪要",
                "content": [[{"tag": "text", "text": "今日议程如下"}]],
            }
        })
        payload = _make_im_receive_payload(msg_type="post", content=post_content)
        handler.do_without_validation(payload)
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].content == "会议纪要\n今日议程如下"


# ═══════════════════════════════════════════════════════════════════════════════
# 功能二：Bot 入群欢迎事件
# ═══════════════════════════════════════════════════════════════════════════════


class TestBotAddedCallback:
    """im.chat.member.bot.added_v1 事件 → on_bot_added 回调"""

    async def test_on_bot_added_called_with_chat_id_and_name(self):
        """Bot 入群事件触发时，on_bot_added 被以 chat_id 和 group_name 调用"""
        loop = asyncio.get_event_loop()
        received: list[tuple[str, str]] = []

        async def on_bot_added(chat_id: str, group_name: str) -> None:
            received.append((chat_id, group_name))

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=AsyncMock(),
            on_bot_added=on_bot_added,
        )
        handler.do_without_validation(_make_bot_added_payload("oc_001", "工程团队"))
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0] == ("oc_001", "工程团队")

    async def test_on_bot_added_none_silently_ignored(self):
        """on_bot_added 为 None（默认值）时，入群事件静默忽略，不报错"""
        loop = asyncio.get_event_loop()
        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=AsyncMock(),
            on_bot_added=None,
        )
        # 不应抛出任何异常
        handler.do_without_validation(_make_bot_added_payload())

    async def test_bot_added_does_not_dispatch_as_inbound_message(self):
        """入群事件不应触发 on_message 回调"""
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        received_added: list[tuple] = []

        async def on_bot_added(chat_id: str, group_name: str) -> None:
            received_added.append((chat_id, group_name))

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=on_msg,
            on_bot_added=on_bot_added,
        )
        handler.do_without_validation(_make_bot_added_payload())
        await asyncio.sleep(0.1)

        assert received == []  # on_message 未触发
        assert len(received_added) == 1  # on_bot_added 触发

    async def test_bot_added_payload_missing_fields_does_not_raise(self):
        """payload 缺少 chat_id / name 字段时，handler 不抛异常"""
        loop = asyncio.get_event_loop()
        received: list[tuple] = []

        async def on_bot_added(chat_id: str, group_name: str) -> None:
            received.append((chat_id, group_name))

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=AsyncMock(),
            on_bot_added=on_bot_added,
        )
        # 缺少 event 字段的 payload
        payload = json.dumps({
            "header": {"event_type": "im.chat.member.bot.added_v1"},
            "event": {},
        }).encode()
        handler.do_without_validation(payload)
        await asyncio.sleep(0.1)

        # chat_id 为空串，group_name 为空串
        assert len(received) == 1
        assert received[0] == ("", "")


class TestFeishuListenerInitWithBotAdded:
    """FeishuListener.__init__ 接受 on_bot_added 参数"""

    def test_init_accepts_on_bot_added_param(self):
        """FeishuListener 应接受 on_bot_added 可选参数"""
        with patch("xiaopaw.feishu.listener.WSClient"), \
             patch("xiaopaw.feishu.listener._XiaoPawEventHandler") as mock_handler_cls:
            loop = MagicMock()
            on_msg = AsyncMock()
            on_bot_added = AsyncMock()

            FeishuListener(
                app_id="app_id",
                app_secret="app_secret",
                on_message=on_msg,
                loop=loop,
                on_bot_added=on_bot_added,
            )

            # _XiaoPawEventHandler 应接收到 on_bot_added 参数
            call_kwargs = mock_handler_cls.call_args.kwargs
            assert call_kwargs["on_bot_added"] is on_bot_added

    def test_init_default_on_bot_added_is_none(self):
        """不传 on_bot_added 时，默认为 None"""
        with patch("xiaopaw.feishu.listener.WSClient"), \
             patch("xiaopaw.feishu.listener._XiaoPawEventHandler") as mock_handler_cls:
            loop = MagicMock()
            FeishuListener(
                app_id="app_id",
                app_secret="app_secret",
                on_message=AsyncMock(),
                loop=loop,
            )
            call_kwargs = mock_handler_cls.call_args.kwargs
            assert call_kwargs.get("on_bot_added") is None


# ═══════════════════════════════════════════════════════════════════════════════
# 功能三：allowed_chats 白名单
# ═══════════════════════════════════════════════════════════════════════════════


class TestAllowedChats:
    """allowed_chats 白名单过滤测试"""

    async def test_group_message_in_whitelist_is_dispatched(self):
        """群消息的 chat_id 在白名单中，应正常 dispatch"""
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=on_msg,
            allowed_chats=["oc_allowed"],
        )
        handler.do_without_validation(
            _make_im_receive_payload(chat_type="group", chat_id="oc_allowed")
        )
        await asyncio.sleep(0.1)

        assert len(received) == 1

    async def test_group_message_not_in_whitelist_is_silenced(self):
        """群消息的 chat_id 不在白名单中，应静默忽略"""
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=on_msg,
            allowed_chats=["oc_allowed"],
        )
        handler.do_without_validation(
            _make_im_receive_payload(chat_type="group", chat_id="oc_NOT_allowed")
        )
        await asyncio.sleep(0.1)

        assert received == []

    async def test_p2p_message_always_dispatched_regardless_of_whitelist(self):
        """p2p 私聊消息不受白名单限制，始终 dispatch"""
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=on_msg,
            allowed_chats=["oc_some_group"],  # 白名单中没有 p2p chat_id
        )
        handler.do_without_validation(
            _make_im_receive_payload(chat_type="p2p", chat_id="ou_user_abc")
        )
        await asyncio.sleep(0.1)

        assert len(received) == 1

    async def test_empty_whitelist_allows_all(self):
        """空白名单（[]）表示允许所有群"""
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=on_msg,
            allowed_chats=[],
        )
        handler.do_without_validation(
            _make_im_receive_payload(chat_type="group", chat_id="oc_any_group")
        )
        await asyncio.sleep(0.1)

        assert len(received) == 1

    async def test_none_whitelist_allows_all(self):
        """allowed_chats=None（默认）表示允许所有"""
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=on_msg,
            allowed_chats=None,
        )
        handler.do_without_validation(
            _make_im_receive_payload(chat_type="group", chat_id="oc_any_group")
        )
        await asyncio.sleep(0.1)

        assert len(received) == 1

    async def test_bot_added_event_silenced_when_chat_not_in_whitelist(self):
        """Bot 入群事件：chat_id 不在白名单时，on_bot_added 不调用"""
        loop = asyncio.get_event_loop()
        received: list[tuple] = []

        async def on_bot_added(chat_id: str, group_name: str) -> None:
            received.append((chat_id, group_name))

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=AsyncMock(),
            on_bot_added=on_bot_added,
            allowed_chats=["oc_allowed"],
        )
        handler.do_without_validation(_make_bot_added_payload("oc_NOT_allowed", "未授权群"))
        await asyncio.sleep(0.1)

        assert received == []

    async def test_bot_added_event_dispatched_when_chat_in_whitelist(self):
        """Bot 入群事件：chat_id 在白名单时，on_bot_added 被调用"""
        loop = asyncio.get_event_loop()
        received: list[tuple] = []

        async def on_bot_added(chat_id: str, group_name: str) -> None:
            received.append((chat_id, group_name))

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=AsyncMock(),
            on_bot_added=on_bot_added,
            allowed_chats=["oc_allowed"],
        )
        handler.do_without_validation(_make_bot_added_payload("oc_allowed", "授权群"))
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0] == ("oc_allowed", "授权群")

    async def test_multiple_chats_in_whitelist(self):
        """白名单支持多个 chat_id"""
        loop = asyncio.get_event_loop()
        received: list[InboundMessage] = []

        async def on_msg(inbound: InboundMessage) -> None:
            received.append(inbound)

        handler = _XiaoPawEventHandler(
            loop=loop,
            on_message=on_msg,
            allowed_chats=["oc_a", "oc_b", "oc_c"],
        )
        for chat_id in ["oc_a", "oc_b", "oc_c"]:
            handler.do_without_validation(
                _make_im_receive_payload(chat_type="group", chat_id=chat_id)
            )
        await asyncio.sleep(0.1)

        assert len(received) == 3


class TestFeishuListenerInitWithAllowedChats:
    """FeishuListener.__init__ 接受 allowed_chats 参数"""

    def test_init_passes_allowed_chats_to_handler(self):
        """FeishuListener 应将 allowed_chats 传递给 _XiaoPawEventHandler"""
        with patch("xiaopaw.feishu.listener.WSClient"), \
             patch("xiaopaw.feishu.listener._XiaoPawEventHandler") as mock_handler_cls:
            loop = MagicMock()
            FeishuListener(
                app_id="app_id",
                app_secret="app_secret",
                on_message=AsyncMock(),
                loop=loop,
                allowed_chats=["oc_001", "oc_002"],
            )
            call_kwargs = mock_handler_cls.call_args.kwargs
            assert call_kwargs["allowed_chats"] == ["oc_001", "oc_002"]

    def test_init_default_allowed_chats_is_none(self):
        """不传 allowed_chats 时，默认为 None"""
        with patch("xiaopaw.feishu.listener.WSClient"), \
             patch("xiaopaw.feishu.listener._XiaoPawEventHandler") as mock_handler_cls:
            loop = MagicMock()
            FeishuListener(
                app_id="app_id",
                app_secret="app_secret",
                on_message=AsyncMock(),
                loop=loop,
            )
            call_kwargs = mock_handler_cls.call_args.kwargs
            assert call_kwargs.get("allowed_chats") is None
