"""TestAPI 服务端集成测试

使用 aiohttp test_utils 的 TestClient:
1. Mock Runner 验证 HTTP 层（构造 InboundMessage、Pydantic 校验等）
2. Full Integration 验证端到端：TestAPI → Runner → SessionManager → 回复
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

from xiaopaw.api.capture_sender import CaptureSender
from xiaopaw.api.test_server import create_test_app
from xiaopaw.models import InboundMessage
from xiaopaw.runner import Runner
from xiaopaw.session.manager import SessionManager
from xiaopaw.session.models import MessageEntry


@pytest.fixture
def capture_sender():
    return CaptureSender()


@pytest.fixture
def mock_runner(capture_sender):
    """Mock Runner: dispatch 时立即通过 capture_sender 发送回复"""

    async def fake_dispatch(inbound: InboundMessage):
        await capture_sender.send(
            inbound.routing_key,
            f"echo: {inbound.content}",
            inbound.root_id,
        )

    runner = AsyncMock()
    runner.dispatch = AsyncMock(side_effect=fake_dispatch)
    return runner


@pytest.fixture
async def client(mock_runner, capture_sender):
    app = create_test_app(runner=mock_runner, sender=capture_sender)
    async with TestClient(TestServer(app)) as cli:
        yield cli


class TestPostMessage:
    """POST /api/test/message"""

    async def test_basic_text_message(self, client, mock_runner):
        resp = await client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test001", "content": "你好"},
        )
        assert resp.status == 200
        data = await resp.json()

        assert data["reply"] == "echo: 你好"
        assert data["msg_id"].startswith("test_")
        assert data["duration_ms"] >= 0
        assert "session_id" in data

    async def test_auto_generates_msg_id(self, client):
        resp = await client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test001", "content": "msg1"},
        )
        data1 = await resp.json()

        resp = await client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test001", "content": "msg2"},
        )
        data2 = await resp.json()

        assert data1["msg_id"] != data2["msg_id"]

    async def test_custom_msg_id(self, client):
        resp = await client.post(
            "/api/test/message",
            json={
                "routing_key": "p2p:ou_test001",
                "content": "hi",
                "msg_id": "my_custom_id",
            },
        )
        data = await resp.json()
        assert data["msg_id"] == "my_custom_id"

    async def test_dispatches_to_runner(self, client, mock_runner):
        await client.post(
            "/api/test/message",
            json={"routing_key": "group:oc_chat123", "content": "hello"},
        )

        mock_runner.dispatch.assert_called_once()
        inbound = mock_runner.dispatch.call_args[0][0]
        assert isinstance(inbound, InboundMessage)
        assert inbound.routing_key == "group:oc_chat123"
        assert inbound.content == "hello"

    async def test_thread_routing_key(self, client, mock_runner):
        await client.post(
            "/api/test/message",
            json={
                "routing_key": "thread:oc_chat789:ot_topic001",
                "content": "thread msg",
            },
        )
        inbound = mock_runner.dispatch.call_args[0][0]
        assert inbound.routing_key == "thread:oc_chat789:ot_topic001"

    async def test_empty_content(self, client):
        resp = await client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test001"},
        )
        data = await resp.json()
        assert data["reply"] == "echo: "

    async def test_invalid_request_missing_routing_key(self, client):
        resp = await client.post(
            "/api/test/message",
            json={"content": "no routing key"},
        )
        assert resp.status == 422


class TestDeleteSessions:
    """DELETE /api/test/sessions"""

    async def test_delete_returns_ok(self, client):
        resp = await client.delete("/api/test/sessions")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"


# ── Full Integration (Real Runner + SessionManager) ───────────


async def _echo_agent(
    user_message: str,
    history: list[MessageEntry],
    session_id: str,
    routing_key: str = "",
    root_id: str = "",
    verbose: bool = False,
) -> str:
    return f"echo: {user_message}"


@pytest.fixture
def full_session_mgr(tmp_path):
    return SessionManager(data_dir=tmp_path)


@pytest.fixture
async def full_client(full_session_mgr):
    """端到端集成：real CaptureSender + real Runner + real SessionManager"""
    sender = CaptureSender()
    runner = Runner(
        session_mgr=full_session_mgr,
        sender=sender,
        agent_fn=_echo_agent,
        idle_timeout=5.0,
    )
    app = create_test_app(
        runner=runner, sender=sender, session_mgr=full_session_mgr
    )
    async with TestClient(TestServer(app)) as cli:
        yield cli
    await runner.shutdown()


class TestFullIntegration:
    """端到端：HTTP → Runner → SessionManager → 回复"""

    async def test_response_has_valid_session_id(self, full_client):
        """响应中 session_id 应为有效的 session ID"""
        resp = await full_client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test", "content": "hi"},
        )
        data = await resp.json()

        assert resp.status == 200
        assert data["reply"] == "echo: hi"
        assert data["session_id"].startswith("s-")

    async def test_same_routing_key_same_session(self, full_client):
        """同一 routing_key 的消息应属于同一 session"""
        resp1 = await full_client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test", "content": "msg1"},
        )
        resp2 = await full_client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test", "content": "msg2"},
        )
        data1 = await resp1.json()
        data2 = await resp2.json()

        assert data1["session_id"] == data2["session_id"]

    async def test_slash_new_switches_session(self, full_client):
        """/new 命令应切换 session，后续消息属于新 session"""
        resp1 = await full_client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test", "content": "before new"},
        )
        sid1 = (await resp1.json())["session_id"]

        # /new 切换
        await full_client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test", "content": "/new"},
        )

        # 新 session
        resp3 = await full_client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test", "content": "after new"},
        )
        sid3 = (await resp3.json())["session_id"]

        assert sid1 != sid3

    async def test_delete_clears_sessions(
        self, full_client, full_session_mgr
    ):
        """DELETE 后再发消息应获得全新 session"""
        resp1 = await full_client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test", "content": "hi"},
        )
        sid1 = (await resp1.json())["session_id"]

        # 清空
        del_resp = await full_client.delete("/api/test/sessions")
        assert (await del_resp.json())["status"] == "ok"

        # 再发消息 — 应是新 session
        resp2 = await full_client.post(
            "/api/test/message",
            json={"routing_key": "p2p:ou_test", "content": "hi again"},
        )
        sid2 = (await resp2.json())["session_id"]

        assert sid1 != sid2

    async def test_history_accumulates(self, full_client, full_session_mgr):
        """多条消息后 session 的 message_count 应递增"""
        key = "p2p:ou_history_test"
        for i in range(3):
            await full_client.post(
                "/api/test/message",
                json={"routing_key": key, "content": f"msg{i}"},
            )

        session = await full_session_mgr.get_or_create(key)
        # 3 轮 × 2 (user + assistant) = 6
        assert session.message_count == 6
