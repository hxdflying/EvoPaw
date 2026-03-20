"""CaptureSender 新方法单元测试

测试范围：
- send_thinking: stub 返回固定 card_msg_id，不实际发送
- update_card: 将内容转发到 Future 捕获，供测试验证最终回复
- send_text: slash 命令纯文本，不走 Future 捕获
"""

from __future__ import annotations

import asyncio

import pytest

from xiaopaw.api.capture_sender import CaptureSender


class TestCaptureSenderSendThinking:
    """send_thinking: 返回固定 stub card_msg_id，不实际发送"""

    async def test_returns_stub_card_msg_id(self):
        """send_thinking 返回约定的 stub card_msg_id"""
        sender = CaptureSender()
        card_msg_id = await sender.send_thinking("p2p:ou_test", "root_001")
        assert card_msg_id == "test-card-thinking-001"

    async def test_returns_same_id_for_any_routing_key(self):
        """不同 routing_key 都返回相同 stub id"""
        sender = CaptureSender()
        id1 = await sender.send_thinking("p2p:ou_a", "root_001")
        id2 = await sender.send_thinking("group:oc_b", "root_002")
        id3 = await sender.send_thinking("thread:oc_c:t_001", "root_003")
        assert id1 == id2 == id3 == "test-card-thinking-001"

    async def test_does_not_affect_futures(self):
        """send_thinking 不 resolve 任何已注册的 Future"""
        sender = CaptureSender()
        fut = sender.register("msg_001")
        await sender.send_thinking("p2p:ou_test", "msg_001")
        assert not fut.done()

    async def test_signature_matches_protocol(self):
        """send_thinking 接受 routing_key 和 root_id 两个参数"""
        sender = CaptureSender()
        # 不应抛 TypeError
        result = await sender.send_thinking(
            routing_key="p2p:ou_test", root_id="root_001"
        )
        assert result is not None

    async def test_returns_str_not_none(self):
        """CaptureSender.send_thinking 返回 str（不是 None），保证测试可捕获"""
        sender = CaptureSender()
        result = await sender.send_thinking("p2p:ou_test", "root_001")
        assert isinstance(result, str)


class TestCaptureSenderUpdateCard:
    """update_card: 将内容 resolve 到已注册的 Future，供测试捕获最终回复"""

    async def test_update_card_resolves_future(self):
        """update_card 将 content resolve 到对应 Future"""
        sender = CaptureSender()
        # 模拟 Runner 的行为：先注册，再 send_thinking，再 update_card
        fut = sender.register("root_msg")
        await sender.update_card("test-card-thinking-001", "Agent 的回复")

        # update_card 应将内容 resolve 到最近注册的 Future
        assert fut.result() == "Agent 的回复"

    async def test_update_card_with_markdown_content(self):
        """update_card 正确传递 Markdown 内容"""
        sender = CaptureSender()
        fut = sender.register("root_msg")
        md_reply = "## 分析结果\n\n- 条目 1\n- 条目 2"
        await sender.update_card("any-card-id", md_reply)
        assert fut.result() == md_reply

    async def test_wait_for_reply_works_with_update_card(self):
        """wait_for_reply 可以等到 update_card 触发的 Future"""
        sender = CaptureSender()
        sender.register("root_msg")

        async def delayed_update():
            await asyncio.sleep(0.01)
            await sender.update_card("test-card-thinking-001", "delayed reply")

        asyncio.create_task(delayed_update())
        result = await sender.wait_for_reply("root_msg", timeout=1.0)
        assert result == "delayed reply"

    async def test_update_card_no_registered_future_is_noop(self):
        """update_card 没有注册的 Future 时不应报错"""
        sender = CaptureSender()
        # 不应抛异常
        await sender.update_card("some-card-id", "内容")

    async def test_send_thinking_then_update_card_full_flow(self):
        """模拟完整流程：register → send_thinking → update_card → wait_for_reply"""
        sender = CaptureSender()
        fut = sender.register("root_001")

        card_id = await sender.send_thinking("p2p:ou_test", "root_001")
        assert card_id == "test-card-thinking-001"

        await sender.update_card(card_id, "最终 Agent 回复")

        result = await asyncio.wait_for(fut, timeout=1.0)
        assert result == "最终 Agent 回复"


class TestCaptureSenderSendText:
    """send_text: slash 命令纯文本，不走 Future 捕获"""

    async def test_send_text_does_not_resolve_future(self):
        """send_text 不 resolve 已注册的 Future（slash 命令不是 Agent 回复）"""
        sender = CaptureSender()
        fut = sender.register("root_msg")
        await sender.send_text("p2p:ou_test", "/help 回复内容", "root_msg")
        # Future 应仍处于 pending 状态
        assert not fut.done()

    async def test_send_text_accepts_correct_signature(self):
        """send_text 接受 routing_key, content, root_id 三个参数"""
        sender = CaptureSender()
        # 不应抛 TypeError
        await sender.send_text(
            routing_key="p2p:ou_test",
            content="纯文本内容",
            root_id="root_001",
        )

    async def test_send_text_noop_for_any_routing_key(self):
        """不同 routing_key 调用 send_text 都不影响 Future"""
        sender = CaptureSender()
        fut = sender.register("root_001")
        await sender.send_text("p2p:ou_x", "text", "root_001")
        await sender.send_text("group:oc_y", "text", "root_001")
        await sender.send_text("thread:oc_z:t_001", "text", "root_001")
        assert not fut.done()

    async def test_send_text_different_from_send(self):
        """send_text 行为与 send 不同：send 会 resolve Future，send_text 不会"""
        sender = CaptureSender()
        fut = sender.register("root_msg")

        await sender.send_text("p2p:ou_test", "slash reply", "root_msg")
        assert not fut.done()

        await sender.send("p2p:ou_test", "agent reply", "root_msg")
        assert fut.done()
        assert fut.result() == "agent reply"


class TestCaptureSenderIntegration:
    """CaptureSender 与 Runner 流程的集成场景"""

    async def test_update_card_captures_agent_reply_not_slash(self):
        """Agent 回复走 update_card 被捕获，slash 回复走 send_text 不被捕获"""
        sender = CaptureSender()
        fut = sender.register("agent_root")

        # slash 命令不触发捕获
        await sender.send_text("p2p:ou_test", "/help 的回复", "agent_root")
        assert not fut.done()

        # Agent 回复走 update_card 触发捕获
        await sender.update_card("test-card-thinking-001", "Agent 分析结果")
        assert fut.result() == "Agent 分析结果"
