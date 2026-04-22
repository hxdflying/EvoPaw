"""集成测试公共 Fixtures

运行前提：
  - ANTHROPIC_API_KEY 已设置（否则 LLM 测试自动跳过）
  - pgvector 运行在 localhost:5432（否则 pgvector 测试自动跳过）

快速运行（仅 slash command，无需 API key）：
  pytest tests/integration/ -m "not llm"

完整运行（需要 API key）：
  ANTHROPIC_API_KEY=sk-ant-xxx pytest tests/integration/ -v -s
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import socket
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from evopaw.api.capture_sender import CaptureSender
from evopaw.api.test_server import create_test_app
from evopaw.runner import Runner
from evopaw.session.manager import SessionManager
from evopaw.session.models import MessageEntry


# ── 日志归档 + markers 注册 ──────────────────────────────────────────────────

def pytest_configure(config: pytest.Config) -> None:
    """日志归档 + 注册 markers（合并为一个函数避免重复定义）。"""
    # 日志归档
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{ts}.log"
    config.option.log_file = str(log_file)
    latest = log_dir / "latest.log"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(log_file.name)

    # 注册 markers
    for marker in [
        "llm: 需要真实 LLM API（ANTHROPIC_API_KEY）的测试",
        "integration: 集成测试（与外部服务交互）",
        "feishu: 需要真实飞书凭证的实况测试",
        "pgvector: 需要 pgvector（localhost:5432）运行的测试",
    ]:
        config.addinivalue_line("markers", marker)


# ── 环境检测 ───────────────────────────────────────────────────────────────────

PGVECTOR_HOST = "localhost"
PGVECTOR_PORT = 5432
PGVECTOR_DSN  = "postgresql://evopaw:evopaw123@localhost:5432/evopaw_memory"


def _pgvector_reachable() -> bool:
    try:
        s = socket.create_connection((PGVECTOR_HOST, PGVECTOR_PORT), timeout=1.0)
        s.close()
        return True
    except OSError:
        return False


def _anthropic_api_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY")


# ── Session 级别 Fixtures ─────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def anthropic_api_key() -> str:
    """返回 Anthropic API Key；未设置则跳过整个 session。"""
    key = _anthropic_api_key()
    if not key:
        pytest.skip("ANTHROPIC_API_KEY 未设置，跳过 LLM 集成测试")
    return key


@pytest.fixture(scope="session")
def pgvector_available() -> bool:
    return _pgvector_reachable()


@pytest.fixture(scope="session")
def pgvector_live_dsn(pgvector_available: bool) -> str:
    if not pgvector_available:
        pytest.skip("pgvector (localhost:5432) 不可达，跳过 pgvector 测试")
    return PGVECTOR_DSN


# ── Function 级别 Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def session_mgr(tmp_path: Path) -> SessionManager:
    return SessionManager(data_dir=tmp_path)


@pytest.fixture
def cron_dir(tmp_path: Path) -> Path:
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


# ── 真实 LLM 客户端 ─────────────────────────────────────────────────────────

_WORKSPACE_INIT = Path(__file__).parents[2] / "workspace-init"


def _init_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """复制 workspace-init/ 到 tmp_path，返回 (workspace_dir, ctx_dir)。"""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    ctx_dir = tmp_path / "ctx"
    ctx_dir.mkdir()
    if _WORKSPACE_INIT.exists():
        for f in _WORKSPACE_INIT.glob("*.md"):
            shutil.copy(f, workspace_dir / f.name)
    return workspace_dir, ctx_dir


@pytest.fixture
async def llm_client(
    tmp_path: Path,
    session_mgr: SessionManager,
    anthropic_api_key: str,
) -> TestClient:
    """完整 E2E 客户端（带三层记忆）。"""
    from evopaw.agents.main_agent import build_agent_fn  # noqa: PLC0415

    workspace_dir, ctx_dir = _init_workspace(tmp_path)
    db_dsn = os.getenv("MEMORY_DB_DSN", "")
    sender = CaptureSender()
    agent_fn = build_agent_fn(
        sender=sender,
        workspace_dir=workspace_dir,
        ctx_dir=ctx_dir,
        db_dsn=db_dsn,
        max_history_turns=20,
    )
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        idle_timeout=30.0,
    )
    app = create_test_app(runner=runner, sender=sender,
                          session_mgr=session_mgr, workspace_dir=workspace_dir)
    async with TestClient(TestServer(app)) as cli:
        cli._workspace_dir = workspace_dir
        cli._ctx_dir = ctx_dir
        yield cli
    await runner.shutdown()


@pytest.fixture
async def memory_client(
    tmp_path: Path,
    session_mgr: SessionManager,
    anthropic_api_key: str,
) -> TestClient:
    """带完整三层记忆的 E2E 测试客户端。"""
    from evopaw.agents.main_agent import build_agent_fn  # noqa: PLC0415

    workspace_dir, ctx_dir = _init_workspace(tmp_path)
    db_dsn = os.getenv("MEMORY_DB_DSN", "")
    sender = CaptureSender()
    agent_fn = build_agent_fn(
        sender=sender,
        workspace_dir=workspace_dir,
        ctx_dir=ctx_dir,
        db_dsn=db_dsn,
        max_history_turns=20,
    )
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        idle_timeout=30.0,
    )
    app = create_test_app(runner=runner, sender=sender,
                          session_mgr=session_mgr, workspace_dir=workspace_dir)
    async with TestClient(TestServer(app)) as cli:
        cli._workspace_dir = workspace_dir
        cli._ctx_dir = ctx_dir
        yield cli
    await runner.shutdown()


@pytest.fixture
def pgvector_dsn() -> str:
    dsn = os.getenv("MEMORY_DB_DSN", "")
    if not dsn:
        pytest.skip("MEMORY_DB_DSN 未配置，跳过 pgvector 测试")
    return dsn


@pytest.fixture
async def memory_client_pgvector(
    tmp_path: Path,
    session_mgr: SessionManager,
    anthropic_api_key: str,
    pgvector_live_dsn: str,
) -> TestClient:
    """带真实 pgvector 的完整三层记忆客户端。"""
    import evopaw.api.test_server as _ts  # noqa: PLC0415
    from evopaw.agents.main_agent import build_agent_fn  # noqa: PLC0415

    _original_timeout = _ts._DEFAULT_TIMEOUT
    _ts._DEFAULT_TIMEOUT = 1500.0

    workspace_dir, ctx_dir = _init_workspace(tmp_path)
    sender = CaptureSender()
    agent_fn = build_agent_fn(
        sender=sender,
        workspace_dir=workspace_dir,
        ctx_dir=ctx_dir,
        db_dsn=pgvector_live_dsn,
        max_history_turns=20,
    )
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        idle_timeout=60.0,
    )
    app = create_test_app(runner=runner, sender=sender,
                          session_mgr=session_mgr, workspace_dir=workspace_dir)
    try:
        async with TestClient(TestServer(app), timeout=aiohttp.ClientTimeout(total=1600)) as cli:
            cli._workspace_dir = workspace_dir
            cli._ctx_dir = ctx_dir
            cli._db_dsn = pgvector_live_dsn
            yield cli
    finally:
        _ts._DEFAULT_TIMEOUT = _original_timeout
        await runner.shutdown()


# ── 测试辅助函数 ──────────────────────────────────────────────────────────────

async def send_message(
    client: TestClient,
    content: str,
    routing_key: str = "p2p:ou_tester",
) -> dict:
    resp = await client.post(
        "/api/test/message",
        json={"routing_key": routing_key, "content": content},
    )
    assert resp.status == 200, f"Unexpected status {resp.status}"
    data = await resp.json()
    assert "reply" in data
    return data


def write_ctx(ctx_dir: Path, session_id: str, messages: list[dict]) -> None:
    path = ctx_dir / f"{session_id}_ctx.json"
    path.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
