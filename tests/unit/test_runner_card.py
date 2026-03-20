"""Runner Loading 效果 + 卡片流程单元测试

测试范围：
- 正常消息：send_thinking → agent → update_card
- send_thinking 失败时降级为 send
- slash 命令使用 send_text（纯文本）
- CaptureSender 的新方法在 Runner 流程中正确工作
"""

from __future__ import annotations

import asyncio

import pytest

from xiaopaw.models import InboundMessage
from xiaopaw.runner import Runner
from xiaopaw.session.manager import SessionManager
from xiaopaw.session.models import MessageEntry


# ── Test Helpers ──────────────────────────────────────────────


class MockSenderWithCard:
    """支持卡片操作的可观测 Sender mock"""

    def __init__(
        self,
        thinking_returns: str | None = "test-card-001",
        thinking_raises: Exception | None = None,
    ) -> None:
        self.send_calls: list[tuple[str, str, str]] = []
        self.send_text_calls: list[tuple[str, str, str]] = []
        self.send_thinking_calls: list[tuple[str, str]] = []
        self.update_card_calls: list[tuple[str, str]] = []
        self._thinking_returns = thinking_returns
        self._thinking_raises = thinking_raises
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, routing_key: str, content: str, root_id: str) -> None:
        self.send_calls.append((routing_key, content, root_id))
        await self._queue.put(content)

    async def send_text(
        self, routing_key: str, content: str, root_id: str
    ) -> None:
        self.send_text_calls.append((routing_key, content, root_id))
        await self._queue.put(content)

    async def send_thinking(
        self, routing_key: str, root_id: str
    ) -> str | None:
        self.send_thinking_calls.append((routing_key, root_id))
        if self._thinking_raises is not None:
            raise self._thinking_raises
        return self._thinking_returns

    async def update_card(self, card_msg_id: str, content: str) -> None:
        self.update_card_calls.append((card_msg_id, content))
        await self._queue.put(content)

    async def wait_for_reply(self, timeout: float = 2.0) -> str:
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)


def make_inbound(
    routing_key: str = "p2p:ou_test",
    content: str = "hello",
    msg_id: str = "om_001",
) -> InboundMessage:
    return InboundMessage(
        routing_key=routing_key,
        content=content,
        msg_id=msg_id,
        root_id=msg_id,
        sender_id="ou_test",
        ts=1000000,
    )


async def echo_agent(
    user_message: str,
    history: list[MessageEntry],
    session_id: str,
    routing_key: str = "",
    root_id: str = "",
    verbose: bool = False,
) -> str:
    return f"echo: {user_message}"


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def session_mgr(tmp_path):
    return SessionManager(data_dir=tmp_path)


# ── Loading 正常流程 ────────────────────────────────────────────


class TestRunnerLoadingFlow:
    """send_thinking → agent 执行 → update_card 的正常流程"""

    async def test_send_thinking_called_before_agent(self, session_mgr):
        """收到普通消息后，send_thinking 应在 agent 执行前被调用"""
        call_order: list[str] = []

        class OrderTrackingSender(MockSenderWithCard):
            async def send_thinking(self, routing_key, root_id):
                call_order.append("thinking")
                return "card-001"

            async def update_card(self, card_msg_id, content):
                call_order.append("update_card")
                await super().update_card(card_msg_id, content)

        async def tracking_agent(user_msg, history, sid, routing_key="",
                                  root_id="", verbose=False):
            call_order.append("agent")
            return "agent reply"

        sender = OrderTrackingSender()
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=tracking_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(make_inbound(content="普通消息"))
            await sender.wait_for_reply()
            assert call_order == ["thinking", "agent", "update_card"]
        finally:
            await runner.shutdown()

    async def test_update_card_called_with_agent_reply(self, session_mgr):
        """send_thinking 成功时，用 update_card 发送 Agent 回复"""
        sender = MockSenderWithCard(thinking_returns="card-001")
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(make_inbound(content="hello"))
            reply = await sender.wait_for_reply()

            assert reply == "echo: hello"
            assert len(sender.update_card_calls) == 1
            card_msg_id, content = sender.update_card_calls[0]
            assert card_msg_id == "card-001"
            assert content == "echo: hello"
            # send() 不应被调用（已走 update_card 路径）
            assert len(sender.send_calls) == 0
        finally:
            await runner.shutdown()

    async def test_send_thinking_receives_correct_routing_key_and_root_id(
        self, session_mgr
    ):
        """send_thinking 接收正确的 routing_key 和 root_id"""
        sender = MockSenderWithCard()
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(
                make_inbound(routing_key="group:oc_chat001", msg_id="root_msg")
            )
            await sender.wait_for_reply()

            assert len(sender.send_thinking_calls) == 1
            rk, root_id = sender.send_thinking_calls[0]
            assert rk == "group:oc_chat001"
            assert root_id == "root_msg"
        finally:
            await runner.shutdown()


# ── send_thinking 失败降级 ─────────────────────────────────────


class TestRunnerLoadingFallback:
    """send_thinking 返回 None 时降级为 send()"""

    async def test_fallback_to_send_when_thinking_returns_none(
        self, session_mgr
    ):
        """send_thinking 返回 None → 用 send() 发送 agent 回复"""
        sender = MockSenderWithCard(thinking_returns=None)
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(make_inbound(content="hello"))
            reply = await sender.wait_for_reply()

            assert reply == "echo: hello"
            assert len(sender.send_calls) == 1
            assert len(sender.update_card_calls) == 0
        finally:
            await runner.shutdown()

    async def test_no_update_card_when_thinking_failed(self, session_mgr):
        """send_thinking 失败（返回 None）时不调用 update_card"""
        sender = MockSenderWithCard(thinking_returns=None)
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(make_inbound())
            await sender.wait_for_reply()
            assert len(sender.update_card_calls) == 0
        finally:
            await runner.shutdown()

    async def test_send_called_with_correct_args_on_fallback(
        self, session_mgr
    ):
        """降级到 send() 时，参数与旧行为一致"""
        sender = MockSenderWithCard(thinking_returns=None)
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(
                make_inbound(
                    routing_key="p2p:ou_test",
                    content="hi",
                    msg_id="om_fallback",
                )
            )
            await sender.wait_for_reply()
            rk, content, root_id = sender.send_calls[0]
            assert rk == "p2p:ou_test"
            assert content == "echo: hi"
            assert root_id == "om_fallback"
        finally:
            await runner.shutdown()


# ── Slash 命令使用 send_text ────────────────────────────────────


class TestRunnerSlashUseSendText:
    """slash 命令回复应使用 send_text（纯文本），不经过 send_thinking"""

    async def test_slash_help_uses_send_text(self, session_mgr):
        """/help 应通过 send_text 回复，不调用 send_thinking"""
        sender = MockSenderWithCard()
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(make_inbound(content="/help"))
            reply = await sender.wait_for_reply()

            assert "/new" in reply
            assert len(sender.send_thinking_calls) == 0
            assert len(sender.send_text_calls) == 1
        finally:
            await runner.shutdown()

    async def test_slash_new_uses_send_text(self, session_mgr):
        """/new 应通过 send_text 回复"""
        sender = MockSenderWithCard()
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(make_inbound(content="/new"))
            await sender.wait_for_reply()
            assert len(sender.send_text_calls) == 1
            assert len(sender.send_thinking_calls) == 0
        finally:
            await runner.shutdown()

    async def test_slash_status_uses_send_text(self, session_mgr):
        """/status 应通过 send_text 回复"""
        sender = MockSenderWithCard()
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(make_inbound(content="hi"))  # 先建 session
            await sender.wait_for_reply()  # consume update_card reply

            await runner.dispatch(make_inbound(content="/status", msg_id="om_002"))
            await sender.wait_for_reply()

            # 只有最后的 /status 回复走 send_text
            text_contents = [c for _, c, _ in sender.send_text_calls]
            assert any("s-" in c for c in text_contents)
        finally:
            await runner.shutdown()

    async def test_slash_uses_send_text_not_send(self, session_mgr):
        """slash 命令回复不走 send()（防止误用 interactive 卡片）"""
        sender = MockSenderWithCard()
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(make_inbound(content="/verbose on"))
            await sender.wait_for_reply()

            # send() 不应被调用（slash 命令走 send_text）
            assert len(sender.send_calls) == 0
        finally:
            await runner.shutdown()

    async def test_slash_send_text_correct_routing_key(self, session_mgr):
        """send_text 使用正确的 routing_key"""
        sender = MockSenderWithCard()
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(
                make_inbound(routing_key="group:oc_test", content="/help")
            )
            await sender.wait_for_reply()

            rk, _, _ = sender.send_text_calls[0]
            assert rk == "group:oc_test"
        finally:
            await runner.shutdown()


# ── 错误处理兼容性 ─────────────────────────────────────────────


class TestRunnerErrorHandlingCompat:
    """agent 出错时，错误消息仍通过 send() 发送（保持原有行为）"""

    async def test_agent_error_sends_via_send(self, session_mgr):
        """agent 崩溃时，错误提示走 send() 而非 update_card"""

        async def failing_agent(
            user_msg, history, sid, routing_key="", root_id="", verbose=False
        ):
            raise RuntimeError("agent crashed")

        sender = MockSenderWithCard(thinking_returns="card-001")
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=failing_agent,
            idle_timeout=2.0,
        )
        try:
            await runner.dispatch(make_inbound())
            reply = await sender.wait_for_reply()
            assert "出错" in reply or "重试" in reply
            # 错误消息走 send()
            assert len(sender.send_calls) == 1
        finally:
            await runner.shutdown()
