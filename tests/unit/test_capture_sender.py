"""CaptureSender 单元测试"""

import asyncio

import pytest

from xiaopaw.api.capture_sender import CaptureSender


class TestCaptureSender:
    """CaptureSender: 注册 Future → send 时 resolve → 调用方拿到回复"""

    async def test_register_and_send(self):
        """注册 msg_id 后，send 应将 content resolve 到对应 Future"""
        sender = CaptureSender()
        fut = sender.register("msg_001")

        await sender.send("p2p:ou_test", "你好，世界", "msg_001")

        result = fut.result()
        assert result == "你好，世界"

    async def test_send_unknown_msg_id_is_noop(self):
        """send 未注册的 msg_id 不应报错（如 verbose 推送等非主回复）"""
        sender = CaptureSender()
        # 不应抛异常
        await sender.send("p2p:ou_test", "some message", "unknown_id")

    async def test_concurrent_captures(self):
        """多个 msg_id 并发注册和 resolve，互不干扰"""
        sender = CaptureSender()
        fut_a = sender.register("msg_a")
        fut_b = sender.register("msg_b")

        await sender.send("p2p:ou_1", "reply_b", "msg_b")
        await sender.send("p2p:ou_2", "reply_a", "msg_a")

        assert fut_a.result() == "reply_a"
        assert fut_b.result() == "reply_b"

    async def test_wait_for_reply_with_timeout(self):
        """wait_for_reply 应在超时后抛出 TimeoutError"""
        sender = CaptureSender()
        sender.register("msg_slow")

        with pytest.raises(asyncio.TimeoutError):
            await sender.wait_for_reply("msg_slow", timeout=0.05)

    async def test_wait_for_reply_returns_content(self):
        """wait_for_reply 应在 send 后立即返回 content"""
        sender = CaptureSender()
        sender.register("msg_fast")

        async def delayed_send():
            await asyncio.sleep(0.01)
            await sender.send("p2p:ou_test", "got it", "msg_fast")

        asyncio.create_task(delayed_send())
        result = await sender.wait_for_reply("msg_fast", timeout=1.0)
        assert result == "got it"

    async def test_register_returns_future(self):
        """register 返回的 Future 在 send 前处于 pending 状态"""
        sender = CaptureSender()
        fut = sender.register("msg_pending")
        assert not fut.done()

    async def test_duplicate_register_overwrites(self):
        """重复注册同一 msg_id 应覆盖旧 Future"""
        sender = CaptureSender()
        fut_old = sender.register("msg_dup")
        fut_new = sender.register("msg_dup")

        await sender.send("p2p:ou_test", "new_reply", "msg_dup")

        assert fut_new.result() == "new_reply"
        # 旧 Future 不应被 resolve
        assert not fut_old.done()

    async def test_wait_for_reply_unregistered_raises_key_error(self):
        """wait_for_reply 未注册的 msg_id 应抛出 KeyError"""
        sender = CaptureSender()
        with pytest.raises(KeyError):
            await sender.wait_for_reply("never_registered", timeout=0.01)
