"""EvoPaw 进程入口

启动顺序：
1. 加载 config.yaml（飞书配置、agent 参数等）
2. 初始化日志 + Prometheus metrics 服务
3. 检测 Claude Code CLI（Claude Agent SDK 依赖）
4. 初始化 SessionManager、CleanupService、CronService
5. 写入飞书凭证到 workspace/.config/feishu.json（凭证不经过 LLM）
6. 启动 CleanupService.sweep()（清理历史残留文件）
7. 构建真实 agent_fn（使用 build_agent_fn 工厂）
8. 启动 FeishuListener（WebSocket）+ metrics 服务 + 可选 TestAPI
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import partial
from pathlib import Path

import yaml
from lark_oapi.client import Client, LogLevel

from evopaw.agents.main_agent import build_agent_fn
from evopaw.cleanup.service import CleanupService
from evopaw.cron.service import CronService
from evopaw.feishu.downloader import FeishuDownloader
from evopaw.feishu.listener import FeishuListener, run_forever
from evopaw.feishu.sender import FeishuSender
from evopaw.llm import check_claude_cli
from evopaw.observability.logging_config import setup_logging
from evopaw.observability.metrics_server import start_metrics_server
from evopaw.runner import Runner
from evopaw.session.manager import SessionManager

logger = logging.getLogger(__name__)


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. 请先复制 config.yaml.template 并填写配置。"
        )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return data


async def _daily_cleanup_loop(cleanup_svc: CleanupService) -> None:
    """每日 3:00（Asia/Shanghai）定时清理（独立协程，不依赖 CronService）。"""
    import datetime
    import zoneinfo

    _TZ = zoneinfo.ZoneInfo("Asia/Shanghai")

    while True:
        now = datetime.datetime.now(_TZ)
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
        sleep_s = (next_run - now).total_seconds()
        await asyncio.sleep(sleep_s)
        try:
            await cleanup_svc.sweep()
        except Exception:  # noqa: BLE001
            logger.warning("cleanup: daily sweep failed", exc_info=True)


async def async_main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "config.yaml"
    cfg = _load_config(config_path)

    # ── 1. 日志初始化 ──────────────────────────────────────────────────────
    data_dir = Path(cfg.get("data_dir", "./data")).resolve()
    setup_logging(data_dir / "logs")

    logger.info("EvoPaw starting. data_dir=%s", data_dir)

    # ── 2. 检测 Claude Code CLI ────────────────────────────────────────────
    if not check_claude_cli():
        raise RuntimeError(
            "Claude Code CLI 未安装或不在 PATH 中。"
            "Claude Agent SDK 依赖此 CLI，请先安装：npm install -g @anthropic-ai/claude-code"
        )
    logger.info("Claude Code CLI detected.")

    # ── 3. 读取关键配置 ────────────────────────────────────────────────────
    feishu_cfg = cfg.get("feishu", {})
    app_id = feishu_cfg.get("app_id", "")
    app_secret = feishu_cfg.get("app_secret", "")
    if not app_id or not app_secret:
        raise RuntimeError(
            "feishu.app_id / feishu.app_secret 不能为空，请检查 config.yaml"
        )

    max_history_turns = cfg.get("session", {}).get("max_history_turns", 20)

    memory_cfg = cfg.get("memory", {})
    workspace_dir = Path(memory_cfg.get("workspace_dir", "./data/workspace")).resolve()
    ctx_dir       = Path(memory_cfg.get("ctx_dir", "./data/ctx")).resolve()
    db_dsn        = memory_cfg.get("db_dsn", "")

    debug_cfg = cfg.get("debug", {})
    enable_test_api = debug_cfg.get("enable_test_api", False)
    test_api_host = debug_cfg.get("test_api_host", "127.0.0.1")
    test_api_port = debug_cfg.get("test_api_port", 9090)

    agent_cfg = cfg.get("agent", {})
    planner_model = agent_cfg.get("planner_model", "claude-sonnet-4-6")
    sub_agent_model = agent_cfg.get("sub_agent_model", "claude-haiku-4-5")
    agent_max_turns = agent_cfg.get("max_turns", 50)
    sub_agent_max_turns = agent_cfg.get("sub_agent_max_turns", 20)

    sender_cfg = cfg.get("sender", {})
    sender_max_retries = sender_cfg.get("max_retries", 3)
    sender_retry_backoff = tuple(sender_cfg.get("retry_backoff", [1, 2, 4]))

    runner_cfg = cfg.get("runner", {})
    idle_timeout = runner_cfg.get("queue_idle_timeout_s", 300.0)

    # ── 4. 构建 Feishu HTTP Client ─────────────────────────────────────────
    client = (
        Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(LogLevel.INFO)
        .build()
    )

    # ── 5. 初始化核心服务 ───────────────────────────────────────────────────
    session_mgr = SessionManager(data_dir=data_dir)
    sender = FeishuSender(client=client, max_retries=sender_max_retries, retry_backoff=sender_retry_backoff)
    downloader = FeishuDownloader(client=client, data_dir=data_dir, workspace_dir=workspace_dir)
    cleanup_svc = CleanupService(data_dir=data_dir, workspace_dir=workspace_dir)

    # 写入飞书凭证到 workspace/.config 目录（凭证不经过 LLM）
    cleanup_svc.write_feishu_credentials(app_id=app_id, app_secret=app_secret)
    tavily_key = os.getenv("TAVILY_API_KEY", "")
    cleanup_svc.write_tavily_credentials(api_key=tavily_key)

    # 启动时执行一次存储清理（清除历史残留）
    try:
        await cleanup_svc.sweep()
    except Exception:  # noqa: BLE001
        logger.warning("cleanup: startup sweep failed", exc_info=True)

    # ── 6. 构建真实 agent_fn ────────────────────────────────────────────────
    workspace_dir.mkdir(parents=True, exist_ok=True)
    ctx_dir.mkdir(parents=True, exist_ok=True)

    agent_fn = build_agent_fn(
        sender=sender,
        workspace_dir=workspace_dir,
        ctx_dir=ctx_dir,
        db_dsn=db_dsn,
        max_history_turns=max_history_turns,
        planner_model=planner_model,
        agent_max_turns=agent_max_turns,
        sub_agent_model=sub_agent_model,
        sub_agent_max_turns=sub_agent_max_turns,
    )

    # ── 7. 构建 Runner ──────────────────────────────────────────────────────
    # 生产 Runner 与 TestAPI Runner 仅 sender 不同，其余装配完全一致
    _make_runner = partial(
        Runner,
        session_mgr=session_mgr,
        agent_fn=agent_fn,
        downloader=downloader,
        idle_timeout=idle_timeout,
    )
    runner = _make_runner(sender=sender)

    # ── 8. CronService ──────────────────────────────────────────────────────
    (data_dir / "cron").mkdir(parents=True, exist_ok=True)
    # Sub-Agent 通过 /workspace/cron/ 访问，需要软链接到实际 cron 目录
    cron_link = workspace_dir / "cron"
    if not cron_link.exists():
        try:
            cron_link.symlink_to(data_dir / "cron")
        except OSError:
            logger.warning("Failed to create cron symlink: %s → %s", cron_link, data_dir / "cron")
    cron_svc = CronService(data_dir=data_dir, dispatch_fn=runner.dispatch)
    await cron_svc.start()

    # ── 9. WebSocket Listener ───────────────────────────────────────────────
    loop = asyncio.get_running_loop()
    allowed_chats: list[str] = feishu_cfg.get("allowed_chats", []) or []
    listener = FeishuListener(
        app_id=app_id,
        app_secret=app_secret,
        on_message=runner.dispatch,
        loop=loop,
        allowed_chats=allowed_chats if allowed_chats else None,
        # TODO: 实现 on_bot_added — 向新群发送欢迎卡片
        # on_bot_added=lambda chat_id, name: sender.send_welcome_card(chat_id, name),
        on_bot_added=None,
    )

    logger.info("EvoPaw ready. test_api=%s", enable_test_api)

    # ── 10. 并行启动所有服务 ────────────────────────────────────────────────
    tasks = [
        asyncio.create_task(run_forever(listener), name="feishu-listener"),
        asyncio.create_task(
            start_metrics_server(host="127.0.0.1", port=9100),
            name="metrics-server",
        ),
        asyncio.create_task(
            _daily_cleanup_loop(cleanup_svc),
            name="cleanup-scheduler",
        ),
    ]

    if enable_test_api:
        from evopaw.api.capture_sender import CaptureSender  # noqa: PLC0415
        from evopaw.api.test_server import create_test_app  # noqa: PLC0415

        # 💡 核心点：test runner 使用 CaptureSender，拦截 agent 回复供 HTTP 同步返回
        capture_sender = CaptureSender()
        test_runner = _make_runner(sender=capture_sender)
        test_app = create_test_app(
            runner=test_runner,
            sender=capture_sender,
            session_mgr=session_mgr,
            workspace_dir=workspace_dir,
        )
        tasks.append(
            asyncio.create_task(
                _run_test_api(test_app, host=test_api_host, port=test_api_port),
                name="test-api",
            )
        )
        logger.info("TestAPI enabled: http://%s:%d", test_api_host, test_api_port)

    await asyncio.gather(*tasks)


async def _run_test_api(app: object, host: str, port: int) -> None:
    """启动 aiohttp Test API Server。"""
    from aiohttp import web  # noqa: PLC0415

    app_runner = web.AppRunner(app)
    await app_runner.setup()
    site = web.TCPSite(app_runner, host=host, port=port)
    await site.start()
    logger.info("TestAPI listening on http://%s:%d", host, port)
    try:
        await asyncio.Event().wait()
    finally:
        await app_runner.cleanup()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
