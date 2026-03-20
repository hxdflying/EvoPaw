"""集成测试公共 Fixtures

运行前提：
  - QWEN_API_KEY 或 DASHSCOPE_API_KEY 已设置（否则 LLM 测试自动跳过）
  - AIO-Sandbox 运行在 localhost:8022（否则 sandbox 测试自动跳过）

快速运行（仅 slash command，无需 API key）：
  pytest tests/integration/ -m "not llm"

完整运行（需要 API key）：
  QWEN_API_KEY=sk-xxx pytest tests/integration/ -v -s

仅 sandbox 相关（需 API key + sandbox）：
  pytest tests/integration/ -m "sandbox" -v -s
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from xiaopaw.api.capture_sender import CaptureSender
from xiaopaw.api.test_server import create_test_app
from xiaopaw.runner import Runner
from xiaopaw.session.manager import SessionManager
from xiaopaw.session.models import MessageEntry


# ── 环境检测 ───────────────────────────────────────────────────────────────────

SANDBOX_HOST = "localhost"
SANDBOX_PORT = 8022
SANDBOX_URL = f"http://{SANDBOX_HOST}:{SANDBOX_PORT}/mcp"


def _sandbox_reachable() -> bool:
    """检查 AIO-Sandbox 是否可达（TCP 连接测试）。"""
    try:
        s = socket.create_connection((SANDBOX_HOST, SANDBOX_PORT), timeout=1.0)
        s.close()
        return True
    except OSError:
        return False


def _qwen_api_key() -> str | None:
    return os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")


# ── pytest 钩子：注册 markers ──────────────────────────────────────────────────

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "llm: 需要真实 LLM API（QWEN_API_KEY）的测试",
    )
    config.addinivalue_line(
        "markers",
        "sandbox: 需要 AIO-Sandbox（localhost:8022）运行的测试",
    )
    config.addinivalue_line(
        "markers",
        "integration: 集成测试（与外部服务交互）",
    )
    config.addinivalue_line(
        "markers",
        "feishu: 需要真实飞书凭证（FEISHU_APP_ID + FEISHU_APP_SECRET）的实况测试",
    )


# ── Session 级别 Fixtures（检查一次）─────────────────────────────────────────

@pytest.fixture(scope="session")
def qwen_api_key() -> str:
    """返回 Qwen API Key；未设置则跳过整个 session。"""
    key = _qwen_api_key()
    if not key:
        pytest.skip("QWEN_API_KEY / DASHSCOPE_API_KEY 未设置，跳过 LLM 集成测试")
    return key


@pytest.fixture(scope="session")
def sandbox_available() -> bool:
    return _sandbox_reachable()


# ── Function 级别 Fixtures（每个测试独立）────────────────────────────────────

@pytest.fixture
def session_mgr(tmp_path: Path) -> SessionManager:
    """每个测试独立的 SessionManager，数据存在临时目录。"""
    return SessionManager(data_dir=tmp_path)


@pytest.fixture
def cron_dir(tmp_path: Path) -> Path:
    """scheduler_mgr 测试专用临时 cron 目录。"""
    d = tmp_path / "cron"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Echo Agent（不需要 LLM）──────────────────────────────────────────────────

async def _echo_agent_fn(
    user_message: str,
    history: list[MessageEntry],
    session_id: str,
    routing_key: str = "",
    root_id: str = "",
    verbose: bool = False,
) -> str:
    return f"echo: {user_message}"


@pytest.fixture
async def slash_client(session_mgr: SessionManager) -> TestClient:
    """仅 slash command 测试用客户端：不调用 LLM，用 echo agent。"""
    sender = CaptureSender()
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=_echo_agent_fn,
        idle_timeout=5.0,
    )
    app = create_test_app(runner=runner, sender=sender, session_mgr=session_mgr)
    async with TestClient(TestServer(app)) as cli:
        yield cli
    await runner.shutdown()


# ── 真实 LLM 客户端 ───────────────────────────────────────────────────────────

@pytest.fixture
async def llm_client(
    session_mgr: SessionManager,
    qwen_api_key: str,
    sandbox_available: bool,
) -> TestClient:
    """
    完整 E2E 客户端：真实 AliyunLLM + 真实 Runner + 真实 SessionManager。
    如果沙盒可用，连接沙盒；否则 skill_crew 调用沙盒时会自然失败并被跳过。
    """
    from xiaopaw.agents.main_crew import build_agent_fn  # noqa: PLC0415

    sender = CaptureSender()
    sandbox_url = SANDBOX_URL if sandbox_available else ""

    agent_fn = build_agent_fn(
        sender=sender,
        max_history_turns=20,
        sandbox_url=sandbox_url,
    )
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        idle_timeout=30.0,
    )
    app = create_test_app(runner=runner, sender=sender, session_mgr=session_mgr)
    async with TestClient(TestServer(app)) as cli:
        yield cli
    await runner.shutdown()


# ── 测试辅助函数 ──────────────────────────────────────────────────────────────

async def send_message(
    client: TestClient,
    content: str,
    routing_key: str = "p2p:ou_tester",
) -> dict:
    """向 TestAPI 发送消息，返回响应 JSON。"""
    resp = await client.post(
        "/api/test/message",
        json={"routing_key": routing_key, "content": content},
    )
    assert resp.status == 200, f"Unexpected status {resp.status}"
    data = await resp.json()
    assert "reply" in data
    return data
