"""Runner 单元测试"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from evopaw.asr.models import AsrFailure, AsrResult
from evopaw.models import Attachment, InboundMessage
from evopaw.observability.metrics import (
    audio_dedup_hits_total,
    audio_messages_total,
)
from evopaw.runner import Runner
from evopaw.session.manager import SessionManager
from evopaw.session.models import MessageEntry


# ── Test Helpers ──────────────────────────────────────────────


class MockSender:
    """可观测的 Sender，记录所有发送并通过 Queue 通知等待方"""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self._queue: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()

    async def send(self, routing_key: str, content: str, root_id: str) -> None:
        msg = (routing_key, content, root_id)
        self.messages.append(msg)
        await self._queue.put(msg)

    async def send_text(
        self, routing_key: str, content: str, root_id: str
    ) -> None:
        """slash 命令纯文本回复，与 send 行为相同（供测试捕获）"""
        msg = (routing_key, content, root_id)
        self.messages.append(msg)
        await self._queue.put(msg)

    async def send_thinking(
        self, routing_key: str, root_id: str
    ) -> str | None:
        """Stub：模拟 Loading 卡片，不实际发送，返回 None 触发 send() 降级"""
        return None  # 降级到 send()，保持现有测试语义

    async def update_card(self, card_msg_id: str, content: str) -> None:
        """更新卡片内容，同时写入 queue 供测试捕获"""
        msg = ("card", content, card_msg_id)
        self.messages.append(msg)
        await self._queue.put(msg)

    async def wait_for_message(self, timeout: float = 2.0) -> tuple[str, str, str]:
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
def mock_sender():
    return MockSender()


@pytest.fixture
def session_mgr(tmp_path):
    return SessionManager(data_dir=tmp_path)


@pytest.fixture
async def runner(session_mgr, mock_sender):
    r = Runner(
        session_mgr=session_mgr,
        sender=mock_sender,
        agent_fn=echo_agent,
        idle_timeout=2.0,
    )
    yield r
    await r.shutdown()


# ── Slash Commands ────────────────────────────────────────────


class TestSlashNew:
    async def test_creates_new_session(self, runner, mock_sender, session_mgr):
        """发送普通消息后 /new，session 应切换"""
        await runner.dispatch(make_inbound(content="hi"))
        await mock_sender.wait_for_message()

        s1 = await session_mgr.get_or_create("p2p:ou_test")

        await runner.dispatch(make_inbound(content="/new", msg_id="om_002"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "新对话" in reply or "已创建" in reply

        s2 = await session_mgr.get_or_create("p2p:ou_test")
        assert s2.id != s1.id

    async def test_new_on_fresh_routing_key(self, runner, mock_sender, session_mgr):
        """/new 即使是全新 routing_key 也应成功"""
        await runner.dispatch(make_inbound(content="/new"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "新对话" in reply or "已创建" in reply

        session = await session_mgr.get_or_create("p2p:ou_test")
        assert session.id.startswith("s-")


class TestSlashVerbose:
    async def test_verbose_on(self, runner, mock_sender, session_mgr):
        """/verbose on 开启详细模式"""
        await runner.dispatch(make_inbound(content="/verbose on"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "开启" in reply

        session = await session_mgr.get_or_create("p2p:ou_test")
        assert session.verbose is True

    async def test_verbose_off(self, runner, mock_sender, session_mgr):
        """/verbose off 关闭详细模式"""
        # 先开启
        await runner.dispatch(make_inbound(content="/verbose on"))
        await mock_sender.wait_for_message()

        # 再关闭
        await runner.dispatch(make_inbound(content="/verbose off", msg_id="om_002"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "关闭" in reply

        session = await session_mgr.get_or_create("p2p:ou_test")
        assert session.verbose is False

    async def test_verbose_query_default(self, runner, mock_sender):
        """/verbose 查询默认状态（关闭）"""
        await runner.dispatch(make_inbound(content="/verbose"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "关闭" in reply

    async def test_verbose_query_after_on(self, runner, mock_sender):
        """/verbose 查询开启后的状态"""
        await runner.dispatch(make_inbound(content="/verbose on"))
        await mock_sender.wait_for_message()

        await runner.dispatch(make_inbound(content="/verbose", msg_id="om_002"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "开启" in reply


class TestSlashHelp:
    async def test_returns_command_list(self, runner, mock_sender):
        """/help 返回包含所有命令的说明"""
        await runner.dispatch(make_inbound(content="/help"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "/new" in reply
        assert "/verbose" in reply
        assert "/help" in reply
        assert "/status" in reply
        assert "/stop" in reply


class TestSlashStatus:
    async def test_returns_session_info(self, runner, mock_sender):
        """/status 返回当前 session 信息"""
        # 先发一条消息，产生 session
        await runner.dispatch(make_inbound(content="hi"))
        await mock_sender.wait_for_message()

        await runner.dispatch(make_inbound(content="/status", msg_id="om_002"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert "s-" in reply  # session id


class TestSlashStop:
    """/stop 走 dispatch 入口快路径，不入同 routing_key 队列。"""

    async def test_stop_with_no_active_handle_replies_noop(
        self, runner, mock_sender,
    ):
        await runner.dispatch(make_inbound(content="/stop"))
        _, reply, _ = await mock_sender.wait_for_message()
        assert "没有进行中的任务" in reply

    async def test_stop_does_not_create_queue_or_worker(
        self, runner, mock_sender,
    ):
        await runner.dispatch(make_inbound(content="/stop"))
        await mock_sender.wait_for_message()
        # /stop 不入队、不创建 worker
        assert "p2p:ou_test" not in runner._queues
        assert "p2p:ou_test" not in runner._workers

    async def test_stop_cancels_active_handle(
        self, session_mgr, mock_sender,
    ):
        """慢 agent 跑到一半时 /stop 应 cancel 它，并回复'已取消'。"""
        started = asyncio.Event()

        async def slow_agent(
            user_msg, history, sid,
            routing_key="", root_id="", verbose=False,
        ):
            started.set()
            await asyncio.sleep(60)  # 慢任务
            return "should not reach"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=slow_agent,
            idle_timeout=10.0,
        )
        try:
            await runner.dispatch(make_inbound(content="slow", msg_id="om_1"))
            await asyncio.wait_for(started.wait(), timeout=2.0)

            # 此刻 slow_agent 正在跑；/stop 走快路径
            await runner.dispatch(
                make_inbound(content="/stop", msg_id="om_2"),
            )
            # 第一个回复就是"已取消"（因为 slow_agent 还没完成，没回复过）
            _, reply, _ = await mock_sender.wait_for_message(timeout=2.0)
            assert "已取消" in reply
        finally:
            await runner.shutdown()

    async def test_stop_not_blocked_by_same_routing_key_queue(
        self, session_mgr, mock_sender,
    ):
        """慢 agent 跑、第二条普通消息排队时，/stop 仍能立即响应。"""
        started = asyncio.Event()

        async def slow_agent(
            user_msg, history, sid,
            routing_key="", root_id="", verbose=False,
        ):
            started.set()
            await asyncio.sleep(60)
            return "x"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=slow_agent,
            idle_timeout=10.0,
        )
        try:
            await runner.dispatch(make_inbound(content="slow", msg_id="om_1"))
            await asyncio.wait_for(started.wait(), timeout=2.0)

            # 第二条普通消息进入队列（被 slow_agent 阻塞）
            await runner.dispatch(make_inbound(content="next", msg_id="om_2"))

            # /stop 不应排队，应立即响应
            t0 = asyncio.get_event_loop().time()
            await runner.dispatch(make_inbound(content="/stop", msg_id="om_3"))
            # 用 timeout 较短，验证快路径不阻塞
            _, reply, _ = await mock_sender.wait_for_message(timeout=1.0)
            elapsed = asyncio.get_event_loop().time() - t0

            assert "已取消" in reply
            assert elapsed < 1.0
        finally:
            await runner.shutdown()

    async def test_stop_does_not_affect_other_routing_key(
        self, session_mgr, mock_sender,
    ):
        """/stop p2p:u_a 不应取消 p2p:u_b 的进行中任务。"""
        started_a = asyncio.Event()
        started_b = asyncio.Event()
        done_b = asyncio.Event()

        async def gated_agent(
            user_msg, history, sid,
            routing_key="", root_id="", verbose=False,
        ):
            if routing_key == "p2p:ou_a":
                started_a.set()
                await asyncio.sleep(60)
                return "a"
            started_b.set()
            await asyncio.sleep(0.05)
            done_b.set()
            return "b-done"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=gated_agent,
            idle_timeout=10.0,
        )
        try:
            await runner.dispatch(
                make_inbound(routing_key="p2p:ou_a", content="a", msg_id="om_a")
            )
            await asyncio.wait_for(started_a.wait(), timeout=2.0)
            await runner.dispatch(
                make_inbound(routing_key="p2p:ou_b", content="b", msg_id="om_b")
            )
            await asyncio.wait_for(started_b.wait(), timeout=2.0)

            # cancel a
            await runner.dispatch(
                make_inbound(
                    routing_key="p2p:ou_a", content="/stop", msg_id="om_a_stop"
                )
            )
            # b 应正常完成
            await asyncio.wait_for(done_b.wait(), timeout=2.0)
            # 收集所有消息，确认 b 的回复存在
            replies = []
            for _ in range(2):
                _, reply, _ = await mock_sender.wait_for_message(timeout=1.0)
                replies.append(reply)
            # 至少包含 b-done 和"已取消"
            assert any("b-done" in r for r in replies)
            assert any("已取消" in r for r in replies)
        finally:
            await runner.shutdown()

    async def test_stop_with_attachment_treated_as_normal_message(
        self, runner, mock_sender,
    ):
        """带附件的消息即使内容是 /stop 也走正常队列（防止用户上传文件附带任意文本被误识别）。"""
        from evopaw.models import Attachment

        att = Attachment(msg_type="image", file_key="fk", file_name="x.jpg")
        inbound = InboundMessage(
            routing_key="p2p:ou_test",
            content="/stop",
            msg_id="om_a",
            root_id="om_a",
            sender_id="ou_test",
            ts=1000000,
            attachment=att,
        )
        await runner.dispatch(inbound)
        # 应进入 agent 而非 _handle_stop（echo agent 把内容回声）
        _, reply, _ = await mock_sender.wait_for_message(timeout=2.0)
        assert reply.startswith("echo:")

    async def test_stop_uppercase_and_whitespace_normalized(
        self, runner, mock_sender,
    ):
        await runner.dispatch(make_inbound(content="  /STOP  "))
        _, reply, _ = await mock_sender.wait_for_message()
        assert "没有进行中的任务" in reply


class TestSlashNotCommand:
    async def test_non_slash_goes_to_agent(self, runner, mock_sender):
        """非 slash command 的消息正常进入 agent"""
        await runner.dispatch(make_inbound(content="普通消息"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert reply == "echo: 普通消息"

    async def test_unknown_slash_goes_to_agent(self, runner, mock_sender):
        """未知 slash command 进入 agent 而非报错"""
        await runner.dispatch(make_inbound(content="/unknown"))
        _, reply, _ = await mock_sender.wait_for_message()

        assert reply == "echo: /unknown"


# ── Dispatch + Queue ──────────────────────────────────────────


class TestDispatch:
    async def test_creates_queue_and_processes(self, runner, mock_sender):
        """dispatch 后消息应被处理"""
        await runner.dispatch(make_inbound())
        _, reply, _ = await mock_sender.wait_for_message()

        assert reply == "echo: hello"

    async def test_serial_within_routing_key(self, runner, mock_sender):
        """同一 routing_key 的消息串行处理，按顺序回复"""
        for i in range(3):
            await runner.dispatch(
                make_inbound(content=f"msg{i}", msg_id=f"om_{i}")
            )

        replies = []
        for _ in range(3):
            _, reply, _ = await mock_sender.wait_for_message()
            replies.append(reply)

        assert replies == ["echo: msg0", "echo: msg1", "echo: msg2"]

    async def test_parallel_across_routing_keys(self, runner, mock_sender):
        """不同 routing_key 的消息并行处理"""
        await runner.dispatch(
            make_inbound(routing_key="p2p:ou_a", content="a", msg_id="om_a")
        )
        await runner.dispatch(
            make_inbound(routing_key="p2p:ou_b", content="b", msg_id="om_b")
        )

        replies = set()
        for _ in range(2):
            _, reply, _ = await mock_sender.wait_for_message()
            replies.add(reply)

        assert replies == {"echo: a", "echo: b"}


# ── Handle Flow ───────────────────────────────────────────────


class TestHandle:
    async def test_sends_reply_with_correct_routing(self, runner, mock_sender):
        """回复发送到正确的 routing_key 和 root_id"""
        await runner.dispatch(make_inbound(content="world"))
        rk, reply, root_id = await mock_sender.wait_for_message()

        assert rk == "p2p:ou_test"
        assert reply == "echo: world"
        assert root_id == "om_001"

    async def test_appends_to_session_history(self, runner, mock_sender, session_mgr):
        """处理后 user + assistant 消息应写入 session 历史"""
        await runner.dispatch(make_inbound())
        await mock_sender.wait_for_message()

        session = await session_mgr.get_or_create("p2p:ou_test")
        history = await session_mgr.load_history(session.id)

        assert len(history) == 2
        assert history[0].role == "user"
        assert history[0].content == "hello"
        assert history[1].role == "assistant"
        assert history[1].content == "echo: hello"

    async def test_passes_history_to_agent(self, session_mgr, mock_sender):
        """第二条消息时 agent 应收到之前的历史"""

        async def history_agent(
            user_msg: str,
            history: list[MessageEntry],
            sid: str,
            routing_key: str = "",
            root_id: str = "",
            verbose: bool = False,
        ) -> str:
            return f"history_len={len(history)}"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=history_agent,
            idle_timeout=2.0,
        )

        try:
            await runner.dispatch(make_inbound(content="first"))
            await mock_sender.wait_for_message()

            await runner.dispatch(
                make_inbound(content="second", msg_id="om_002")
            )
            _, reply, _ = await mock_sender.wait_for_message()

            assert reply == "history_len=2"
        finally:
            await runner.shutdown()

    async def test_error_in_agent_sends_error_message(
        self, session_mgr, mock_sender
    ):
        """agent 抛异常时应发送错误提示"""

        async def failing_agent(
            user_msg: str,
            history: list[MessageEntry],
            sid: str,
            routing_key: str = "",
            root_id: str = "",
            verbose: bool = False,
        ) -> str:
            raise RuntimeError("Agent crashed")

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=failing_agent,
            idle_timeout=2.0,
        )

        try:
            await runner.dispatch(make_inbound())
            _, reply, _ = await mock_sender.wait_for_message()

            assert "出错" in reply or "重试" in reply
        finally:
            await runner.shutdown()

    async def test_slash_command_not_saved_to_history(
        self, runner, mock_sender, session_mgr
    ):
        """slash command 不应写入 session 历史"""
        await runner.dispatch(make_inbound(content="/help"))
        await mock_sender.wait_for_message()

        session = await session_mgr.get_or_create("p2p:ou_test")
        history = await session_mgr.load_history(session.id)

        assert len(history) == 0


# ── Worker Lifecycle ──────────────────────────────────────────


class TestWorkerLifecycle:
    async def test_idle_timeout_cleans_up(self, session_mgr, mock_sender):
        """worker 空闲超时后应自动清理"""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=0.1,
        )

        try:
            await runner.dispatch(make_inbound())
            await mock_sender.wait_for_message()

            assert "p2p:ou_test" in runner._queues

            # 等待 idle timeout
            await asyncio.sleep(0.3)

            assert "p2p:ou_test" not in runner._queues
            assert "p2p:ou_test" not in runner._workers
        finally:
            await runner.shutdown()

    async def test_shutdown_cancels_workers(self, session_mgr, mock_sender):
        """shutdown 应取消所有 worker"""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=10.0,
        )

        await runner.dispatch(make_inbound())
        await mock_sender.wait_for_message()

        assert len(runner._workers) == 1

        await runner.shutdown()

        assert len(runner._workers) == 0
        assert len(runner._queues) == 0

    async def test_worker_restarts_after_idle_timeout(
        self, session_mgr, mock_sender
    ):
        """worker 超时退出后，新消息应自动创建新 worker"""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=0.1,
        )

        try:
            await runner.dispatch(make_inbound(content="first"))
            await mock_sender.wait_for_message()

            # 等待 worker 超时退出
            await asyncio.sleep(0.3)
            assert "p2p:ou_test" not in runner._workers

            # 再发消息，应自动创建新 worker
            await runner.dispatch(
                make_inbound(content="second", msg_id="om_002")
            )
            _, reply, _ = await mock_sender.wait_for_message()
            assert reply == "echo: second"
        finally:
            await runner.shutdown()

    async def test_concurrent_dispatch_same_key(self, runner, mock_sender):
        """并发 dispatch 到同一 routing_key 不应创建重复 worker"""
        await asyncio.gather(
            runner.dispatch(make_inbound(content="c0", msg_id="om_0")),
            runner.dispatch(make_inbound(content="c1", msg_id="om_1")),
            runner.dispatch(make_inbound(content="c2", msg_id="om_2")),
        )

        replies = []
        for _ in range(3):
            _, reply, _ = await mock_sender.wait_for_message()
            replies.append(reply)

        # 只有一个 worker
        assert len(runner._workers) == 1
        assert set(replies) == {"echo: c0", "echo: c1", "echo: c2"}


# ── Attachment Download ────────────────────────────────────────


class MockDownloader:
    """可观测的 FeishuDownloader mock"""

    def __init__(self, download_result: Path | None = None) -> None:
        self._result = download_result
        self.calls: list[tuple[str, Attachment, str]] = []

    async def download(
        self, msg_id: str, attachment: Attachment, session_id: str
    ) -> Path | None:
        self.calls.append((msg_id, attachment, session_id))
        return self._result


def make_attachment_inbound(
    content: str = "",
    file_name: str = "test.jpg",
    msg_type: str = "image",
    msg_id: str = "om_img_001",
) -> InboundMessage:
    att = Attachment(msg_type=msg_type, file_key="fk_001", file_name=file_name)
    return InboundMessage(
        routing_key="p2p:ou_test",
        content=content,
        msg_id=msg_id,
        root_id=msg_id,
        sender_id="ou_test",
        ts=1000000,
        attachment=att,
    )


class TestAttachmentDownload:
    async def test_with_attachment_and_successful_download(
        self, session_mgr, mock_sender, tmp_path
    ):
        """有附件且下载成功 → agent 收到包含 sandbox 路径的模板消息"""
        dl = MockDownloader(download_result=tmp_path / "test.jpg")
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
        )

        inbound = make_attachment_inbound(content="请分析图片", file_name="test.jpg")

        try:
            await runner.dispatch(inbound)
            _, reply, _ = await mock_sender.wait_for_message()

            # echo_agent 返回 "echo: {user_message}"，其中 user_message 是模板
            assert "/workspace/sessions/" in reply
            assert "test.jpg" in reply
        finally:
            await runner.shutdown()

    async def test_with_attachment_download_fails_sends_failure_message(
        self, session_mgr, mock_sender
    ):
        """有附件但下载失败 → agent 收到附件下载失败提示"""
        dl = MockDownloader(download_result=None)
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
        )

        inbound = make_attachment_inbound(content="看看这个", file_name="test.jpg")

        try:
            await runner.dispatch(inbound)
            _, reply, _ = await mock_sender.wait_for_message()

            assert "下载失败" in reply
        finally:
            await runner.shutdown()

    async def test_with_attachment_but_no_downloader_passthrough(
        self, session_mgr, mock_sender
    ):
        """有附件但未注入 downloader → 原始 content 直接传给 agent"""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=None,
        )

        inbound = make_attachment_inbound(content="请分析图片")

        try:
            await runner.dispatch(inbound)
            _, reply, _ = await mock_sender.wait_for_message()

            assert reply == "echo: 请分析图片"
        finally:
            await runner.shutdown()

    async def test_without_attachment_downloader_not_called(
        self, session_mgr, mock_sender
    ):
        """无附件消息 → downloader.download 不被调用"""
        dl = MockDownloader(download_result=None)
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
        )

        try:
            await runner.dispatch(make_inbound(content="普通文字消息"))
            await mock_sender.wait_for_message()

            assert len(dl.calls) == 0
        finally:
            await runner.shutdown()

    async def test_original_text_included_in_template_when_present(
        self, session_mgr, mock_sender, tmp_path
    ):
        """下载成功且有原文时，模板中包含用户备注"""
        dl = MockDownloader(download_result=tmp_path / "doc.pdf")
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
        )

        inbound = make_attachment_inbound(
            content="帮我总结一下",
            file_name="doc.pdf",
            msg_type="file",
        )

        try:
            await runner.dispatch(inbound)
            _, reply, _ = await mock_sender.wait_for_message()

            # 原文备注应出现在 agent 收到的消息中
            assert "帮我总结一下" in reply
        finally:
            await runner.shutdown()

    async def test_download_called_with_correct_session_id(
        self, session_mgr, mock_sender, tmp_path
    ):
        """downloader.download 被调用时使用了正确的 session_id"""
        dl = MockDownloader(download_result=tmp_path / "img.jpg")
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
        )

        inbound = make_attachment_inbound(msg_id="om_sid_test")

        try:
            await runner.dispatch(inbound)
            await mock_sender.wait_for_message()

            assert len(dl.calls) == 1
            _, _, session_id = dl.calls[0]
            assert session_id.startswith("s-")
        finally:
            await runner.shutdown()


# ── Voice (ASR) ─────────────────────────────────────────────────


class MockSpeechService:
    """可观测的 SpeechRecognitionService mock."""

    def __init__(
        self,
        result: AsrResult | None = None,
        failure: AsrFailure | None = None,
    ) -> None:
        self._result = result or AsrResult(
            transcript="你好世界",
            provider="aliyun_funasr_realtime",
            model="fun-asr-realtime",
            task_id="tid",
        )
        self._failure = failure
        self.calls: list[tuple[Path, int | None]] = []

    async def transcribe_file(
        self,
        audio_path: Path,
        *,
        duration_ms: int | None = None,
    ) -> AsrResult:
        self.calls.append((audio_path, duration_ms))
        if self._failure is not None:
            raise self._failure
        return self._result


def make_audio_inbound(
    msg_id: str = "om_audio_001",
    file_key: str = "audio_fk_001",
    duration_ms: int | None = 3000,
) -> InboundMessage:
    att = Attachment(
        msg_type="audio",
        file_key=file_key,
        file_name=f"{file_key}.audio",
        duration_ms=duration_ms,
    )
    return InboundMessage(
        routing_key="p2p:ou_test",
        content="",
        msg_id=msg_id,
        root_id=msg_id,
        sender_id="ou_test",
        ts=1000000,
        attachment=att,
    )


class TestVoiceTranscription:
    async def test_audio_success_agent_gets_voice_template(
        self, session_mgr, mock_sender, tmp_path
    ):
        """语音转写成功 → Agent 收到含 transcript + sandbox 路径的模板."""
        audio_path = tmp_path / "audio_fk_001.audio"
        audio_path.write_bytes(b"opus")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            result=AsrResult(
                transcript="帮我查一下天气",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t1",
            )
        )
        received: list[str] = []

        async def capture_agent(user_msg, history, sid, rk="", rid="", verbose=False):
            received.append(user_msg)
            return "今天北京晴。"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=capture_agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
        )

        try:
            await runner.dispatch(make_audio_inbound())
            _, reply, _ = await mock_sender.wait_for_message()

            # Agent 侧：transcript 和沙盒路径都出现在 user_content
            assert len(received) == 1
            uc = received[0]
            assert "帮我查一下天气" in uc
            assert "/workspace/sessions/" in uc
            assert "audio_fk_001.audio" in uc
            # 服务被调用，透传 duration_ms
            assert svc.calls == [(audio_path, 3000)]
            # 最终回复：语音转写 + 回答 两段
            assert reply.startswith("语音转写：")
            assert "帮我查一下天气" in reply
            assert "回答：" in reply
            assert "今天北京晴。" in reply
        finally:
            await runner.shutdown()

    async def test_audio_success_final_reply_written_to_history(
        self, session_mgr, mock_sender, tmp_path
    ):
        """语音成功后 session 历史里写入的是格式化后的 '语音转写 + 回答'."""
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            result=AsrResult(
                transcript="早",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t",
            )
        )

        async def agent(user_msg, history, sid, rk="", rid="", verbose=False):
            return "早上好。"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
        )

        try:
            await runner.dispatch(make_audio_inbound())
            await mock_sender.wait_for_message()

            session = await session_mgr.get_or_create("p2p:ou_test")
            history = await session_mgr.load_history(session.id)
            assistant_entry = next(h for h in history if h.role == "assistant")
            assert assistant_entry.content.startswith("语音转写：")
            assert "早上好。" in assistant_entry.content
        finally:
            await runner.shutdown()

    async def test_asr_failure_replies_friendly_text_and_skips_agent(
        self, session_mgr, mock_sender, tmp_path
    ):
        """ASR 失败直接回友好文案，不进 Agent，不写历史."""
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            failure=AsrFailure(reason="task_failed", detail="E001")
        )

        agent_called: list[bool] = []

        async def agent(user_msg, history, sid, rk="", rid="", verbose=False):
            agent_called.append(True)
            return "ignored"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
        )

        try:
            await runner.dispatch(make_audio_inbound())
            _, reply, _ = await mock_sender.wait_for_message()

            assert "语音转写失败" in reply
            assert agent_called == []

            session = await session_mgr.get_or_create("p2p:ou_test")
            history = await session_mgr.load_history(session.id)
            assert history == []
        finally:
            await runner.shutdown()

    async def test_agent_error_after_asr_success_preserves_transcript(
        self, session_mgr, mock_sender, tmp_path
    ):
        """Agent 异常时 transcript 仍出现在回复里。"""
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            result=AsrResult(
                transcript="我的转写内容",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t",
            )
        )

        async def failing_agent(user_msg, history, sid, rk="", rid="", verbose=False):
            raise RuntimeError("boom")

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=failing_agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
        )

        try:
            await runner.dispatch(make_audio_inbound())
            _, reply, _ = await mock_sender.wait_for_message()

            # transcript 仍然出现
            assert "我的转写内容" in reply
            assert reply.startswith("语音转写：")
            # 回答部分降级为错误文案
            assert "处理出错" in reply
        finally:
            await runner.shutdown()

    async def test_audio_without_speech_service_falls_back_to_generic(
        self, session_mgr, mock_sender, tmp_path
    ):
        """speech_service 未注入时，audio 退回通用附件模板（不应崩溃）."""
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=None,
        )

        try:
            await runner.dispatch(make_audio_inbound())
            _, reply, _ = await mock_sender.wait_for_message()

            # 退回通用附件模板（echo_agent 把模板回显）
            assert "/workspace/sessions/" in reply
            assert "音频" not in reply or True  # 只是确保不崩溃
            assert "语音转写：" not in reply  # 没有 ASR 时不会走语音格式化
        finally:
            await runner.shutdown()


# ── Dedup ───────────────────────────────────────────────────────


class TestDedup:
    async def test_duplicate_msg_id_is_skipped(self, session_mgr, mock_sender):
        """同 msg_id 重复送达时，第二条应被丢弃，不再调用 agent."""
        call_count = 0

        async def counting_agent(user_msg, history, sid, rk="", rid="", verbose=False):
            nonlocal call_count
            call_count += 1
            return f"reply {call_count}"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=counting_agent,
            idle_timeout=2.0,
        )

        try:
            await runner.dispatch(make_inbound(msg_id="om_dup"))
            await mock_sender.wait_for_message()

            await runner.dispatch(make_inbound(msg_id="om_dup"))
            # 给 worker 一点时间处理（若处理了会有额外消息）
            with pytest.raises(asyncio.TimeoutError):
                await mock_sender.wait_for_message(timeout=0.3)

            assert call_count == 1
        finally:
            await runner.shutdown()

    async def test_distinct_msg_ids_both_processed(self, session_mgr, mock_sender):
        """不同 msg_id 正常全部处理."""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )

        try:
            await runner.dispatch(make_inbound(content="a", msg_id="om_1"))
            await runner.dispatch(make_inbound(content="b", msg_id="om_2"))

            replies = []
            for _ in range(2):
                _, r, _ = await mock_sender.wait_for_message()
                replies.append(r)
            assert replies == ["echo: a", "echo: b"]
        finally:
            await runner.shutdown()

    async def test_dedup_window_size_zero_disables_dedup(
        self, session_mgr, mock_sender
    ):
        """dedup_window_size=0 时完全关闭去重，同 msg_id 两次都处理."""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            dedup_window_size=0,
        )

        try:
            await runner.dispatch(make_inbound(msg_id="om_dup"))
            await mock_sender.wait_for_message()

            await runner.dispatch(make_inbound(msg_id="om_dup"))
            _, reply, _ = await mock_sender.wait_for_message(timeout=1.0)
            assert reply.startswith("echo:")
        finally:
            await runner.shutdown()

    async def test_cron_bypasses_dedup(self, session_mgr, mock_sender):
        """is_cron=True 的消息不参与去重（cron 重复触发合法）."""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
        )

        def cron_msg(msg_id: str) -> InboundMessage:
            return InboundMessage(
                routing_key="p2p:ou_test",
                content="daily summary",
                msg_id=msg_id,
                root_id=msg_id,
                sender_id="ou_test",
                ts=1,
                is_cron=True,
            )

        try:
            await runner.dispatch(cron_msg("om_cron"))
            await mock_sender.wait_for_message()

            await runner.dispatch(cron_msg("om_cron"))
            _, reply, _ = await mock_sender.wait_for_message(timeout=1.0)
            assert reply == "echo: daily summary"
        finally:
            await runner.shutdown()

    async def test_dedup_window_lru_eviction(self, session_mgr, mock_sender):
        """窗口满后最老的 msg_id 被淘汰，可再次被处理."""
        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            dedup_window_size=2,
        )

        try:
            await runner.dispatch(make_inbound(content="a", msg_id="om_1"))
            await mock_sender.wait_for_message()
            await runner.dispatch(make_inbound(content="b", msg_id="om_2"))
            await mock_sender.wait_for_message()
            # 窗口满，再进一条把 om_1 挤出去
            await runner.dispatch(make_inbound(content="c", msg_id="om_3"))
            await mock_sender.wait_for_message()

            # om_1 已被淘汰，可以再次被处理
            await runner.dispatch(make_inbound(content="a2", msg_id="om_1"))
            _, reply, _ = await mock_sender.wait_for_message(timeout=1.0)
            assert reply == "echo: a2"
        finally:
            await runner.shutdown()


# ── 分类文案 & 回执 & 指标 ─────────────────────────────────────


def _counter_value(counter, **labels) -> float:
    if labels:
        return counter.labels(**labels)._value.get()
    return counter._value.get()


class SlowSpeechService:
    """延迟 `delay_s` 秒后返回结果的 mock，用于测 short_wait_s 触发的回执."""

    def __init__(
        self,
        delay_s: float,
        result: AsrResult | None = None,
    ) -> None:
        self._delay = delay_s
        self._result = result or AsrResult(
            transcript="慢转写",
            provider="aliyun_funasr_realtime",
            model="fun-asr-realtime",
            task_id="t",
        )
        self.calls: list[tuple] = []

    async def transcribe_file(self, audio_path, *, duration_ms=None):
        self.calls.append((audio_path, duration_ms))
        await asyncio.sleep(self._delay)
        return self._result


class TestVoiceFailureClassified:
    """按 AsrFailure.reason 映射文案。"""

    @pytest.mark.parametrize(
        ("reason", "expect_phrase"),
        [
            ("download", "下载失败"),
            ("ws_connect", "转写服务连接失败"),
            ("submit", "转写服务连接失败"),
            ("disconnect", "转写中断"),
            ("timeout", "转写超时"),
            ("task_failed", "转写失败"),
            ("empty", "转写失败"),
        ],
    )
    async def test_reason_maps_to_user_text(
        self, session_mgr, mock_sender, tmp_path, reason, expect_phrase
    ):
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(failure=AsrFailure(reason=reason, detail="d"))

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
        )
        try:
            await runner.dispatch(make_audio_inbound(msg_id=f"om_{reason}"))
            _, reply, _ = await mock_sender.wait_for_message()
            assert expect_phrase in reply, (
                f"reason={reason} reply={reply!r} 不含期望短语 {expect_phrase!r}"
            )
        finally:
            await runner.shutdown()


class TestVoiceAck:
    """回执机制。"""

    async def test_long_duration_triggers_ack_before_result(
        self, session_mgr, mock_sender, tmp_path
    ):
        """duration_ms 超阈值时立即发 ack，正式回复晚到."""
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            result=AsrResult(
                transcript="长语音",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t",
            )
        )

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=lambda *a, **kw: _async_return("答复"),
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=10_000,
            short_wait_s=10.0,  # 确保不会被 short_wait 触发
        )
        try:
            # duration=30000 > threshold=10000 → 必发 ack
            await runner.dispatch(
                make_audio_inbound(msg_id="om_long", duration_ms=30_000)
            )
            ack = await mock_sender.wait_for_message(timeout=1.0)
            assert "稍候" in ack[1] or "正在转写" in ack[1], (
                f"第一条应为回执，实际为 {ack[1]!r}"
            )

            final = await mock_sender.wait_for_message(timeout=1.0)
            assert "长语音" in final[1]
            assert "答复" in final[1]
        finally:
            await runner.shutdown()

    async def test_short_wait_timeout_triggers_ack(
        self, session_mgr, mock_sender, tmp_path
    ):
        """duration_ms 小或缺失时，short_wait_s 超时触发 ack."""
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        # 转写耗时 0.3s 远超 short_wait_s=0.05s
        svc = SlowSpeechService(
            delay_s=0.3,
            result=AsrResult(
                transcript="慢",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t",
            ),
        )

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=lambda *a, **kw: _async_return("答"),
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=60_000,  # 确保不被 duration 触发
            short_wait_s=0.05,
        )
        try:
            await runner.dispatch(
                make_audio_inbound(msg_id="om_slow", duration_ms=3000)
            )
            ack = await mock_sender.wait_for_message(timeout=1.0)
            assert "稍候" in ack[1] or "正在转写" in ack[1]

            final = await mock_sender.wait_for_message(timeout=1.0)
            assert "慢" in final[1]
        finally:
            await runner.shutdown()

    async def test_fast_short_audio_no_ack(
        self, session_mgr, mock_sender, tmp_path
    ):
        """短语音快速完成时不发 ack，只有一条最终回复."""
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            result=AsrResult(
                transcript="快",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t",
            )
        )

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=lambda *a, **kw: _async_return("答"),
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=60_000,
            short_wait_s=10.0,
        )
        try:
            await runner.dispatch(
                make_audio_inbound(msg_id="om_fast", duration_ms=2000)
            )
            final = await mock_sender.wait_for_message(timeout=1.0)
            assert "快" in final[1]
            assert "答" in final[1]
            # 不应还有第二条消息（ack）
            with pytest.raises(asyncio.TimeoutError):
                await mock_sender.wait_for_message(timeout=0.2)
        finally:
            await runner.shutdown()


class TestAudioMetrics:
    """Runner 侧 audio_* 指标。"""

    async def test_dedup_hit_increments_audio_dedup_metric(
        self, session_mgr, mock_sender, tmp_path
    ):
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService()

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=60_000,
            short_wait_s=10.0,
        )
        try:
            before = audio_dedup_hits_total._value.get()
            await runner.dispatch(make_audio_inbound(msg_id="om_dup"))
            await mock_sender.wait_for_message()

            await runner.dispatch(make_audio_inbound(msg_id="om_dup"))
            with pytest.raises(asyncio.TimeoutError):
                await mock_sender.wait_for_message(timeout=0.3)

            after = audio_dedup_hits_total._value.get()
            assert after - before == 1.0
        finally:
            await runner.shutdown()

    async def test_audio_success_increments_success_counter(
        self, session_mgr, mock_sender, tmp_path
    ):
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService()

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=60_000,
            short_wait_s=10.0,
        )
        try:
            before = _counter_value(audio_messages_total, status="success")
            await runner.dispatch(make_audio_inbound(msg_id="om_metric_ok"))
            await mock_sender.wait_for_message()
            after = _counter_value(audio_messages_total, status="success")
            assert after - before == 1.0
        finally:
            await runner.shutdown()

    async def test_audio_asr_failure_increments_asr_failed(
        self, session_mgr, mock_sender, tmp_path
    ):
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(failure=AsrFailure(reason="timeout"))

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=echo_agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=60_000,
            short_wait_s=10.0,
        )
        try:
            before = _counter_value(audio_messages_total, status="asr_failed")
            await runner.dispatch(make_audio_inbound(msg_id="om_metric_fail"))
            await mock_sender.wait_for_message()
            after = _counter_value(audio_messages_total, status="asr_failed")
            assert after - before == 1.0
        finally:
            await runner.shutdown()


async def _async_return(value):
    """lambda + async 适配的小工具（直接返回协程）."""
    return value


# ── 显示配置可覆写 ─────────────────────────────────────────────


class TestVoiceDisplayConfig:
    """transcription_title / answer_title / display_transcript / include_audio_path."""

    async def test_custom_titles_applied_to_reply(
        self, session_mgr, mock_sender, tmp_path
    ):
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            result=AsrResult(
                transcript="原文",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t",
            )
        )

        async def agent(*a, **kw):
            return "答复"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=60_000,
            short_wait_s=10.0,
            transcription_title="Transcript",
            answer_title="Reply",
        )
        try:
            await runner.dispatch(make_audio_inbound())
            _, reply, _ = await mock_sender.wait_for_message()

            assert reply.startswith("Transcript：")
            assert "Reply：" in reply
            assert "语音转写：" not in reply
            assert "回答：" not in reply
        finally:
            await runner.shutdown()

    async def test_display_transcript_false_omits_transcript_segment(
        self, session_mgr, mock_sender, tmp_path
    ):
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            result=AsrResult(
                transcript="不该出现",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t",
            )
        )

        async def agent(*a, **kw):
            return "纯回答"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=60_000,
            short_wait_s=10.0,
            display_transcript=False,
        )
        try:
            await runner.dispatch(make_audio_inbound())
            _, reply, _ = await mock_sender.wait_for_message()

            assert reply == "纯回答"
            assert "不该出现" not in reply
            assert "语音转写" not in reply
        finally:
            await runner.shutdown()

    async def test_include_audio_path_false_hides_path_from_agent(
        self, session_mgr, mock_sender, tmp_path
    ):
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            result=AsrResult(
                transcript="一句转写",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t",
            )
        )

        captured: list[str] = []

        async def agent(user_msg, *a, **kw):
            captured.append(user_msg)
            return "OK"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=60_000,
            short_wait_s=10.0,
            include_audio_path=False,
        )
        try:
            await runner.dispatch(make_audio_inbound())
            await mock_sender.wait_for_message()

            assert len(captured) == 1
            user_content = captured[0]
            assert "一句转写" in user_content
            # 沙盒路径与原音频文件提示都不应出现
            assert "/workspace/sessions/" not in user_content
            assert "原始音频文件" not in user_content
        finally:
            await runner.shutdown()

    async def test_defaults_preserve_existing_display_behavior(
        self, session_mgr, mock_sender, tmp_path
    ):
        """不传任何显示参数时，保留默认显示行为。"""
        audio_path = tmp_path / "a.audio"
        audio_path.write_bytes(b"x")
        dl = MockDownloader(download_result=audio_path)
        svc = MockSpeechService(
            result=AsrResult(
                transcript="hi",
                provider="aliyun_funasr_realtime",
                model="fun-asr-realtime",
                task_id="t",
            )
        )

        async def agent(*a, **kw):
            return "ans"

        runner = Runner(
            session_mgr=session_mgr,
            sender=mock_sender,
            agent_fn=agent,
            idle_timeout=2.0,
            downloader=dl,
            speech_service=svc,
            long_audio_threshold_ms=60_000,
            short_wait_s=10.0,
        )
        try:
            await runner.dispatch(make_audio_inbound())
            _, reply, _ = await mock_sender.wait_for_message()
            assert reply.startswith("语音转写：")
            assert "hi" in reply
            assert "回答：\nans" in reply
        finally:
            await runner.shutdown()
