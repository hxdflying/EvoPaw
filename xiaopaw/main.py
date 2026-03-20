"""XiaoPaw 进程入口

启动顺序：
1. 加载 config.yaml（飞书配置、agent 参数、sandbox 配置等）
2. 初始化日志 + Prometheus metrics 服务
3. 初始化 SessionManager、CleanupService、CronService
4. 写入飞书凭证到沙盒 workspace/.config/feishu.json（凭证不经过 LLM）
5. 启动 CleanupService.sweep()（清理历史残留文件）
6. 构建真实 agent_fn（使用 build_agent_fn 工厂）
7. 启动 FeishuListener（WebSocket）+ metrics 服务 + 可选 TestAPI
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import yaml
from lark_oapi.client import Client, LogLevel

from xiaopaw.agents.main_crew import build_agent_fn
from xiaopaw.cleanup.service import CleanupService
from xiaopaw.cron.service import CronService
from xiaopaw.feishu.downloader import FeishuDownloader
from xiaopaw.feishu.listener import FeishuListener, run_forever
from xiaopaw.feishu.sender import FeishuSender
from xiaopaw.observability.logging_config import setup_logging
from xiaopaw.observability.metrics_server import start_metrics_server
from xiaopaw.runner import Runner
from xiaopaw.session.manager import SessionManager

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

    logger.info("XiaoPaw starting. data_dir=%s", data_dir)

    # ── 2. 读取关键配置 ────────────────────────────────────────────────────
    feishu_cfg = cfg.get("feishu", {})
    app_id = feishu_cfg.get("app_id", "")
    app_secret = feishu_cfg.get("app_secret", "")
    if not app_id or not app_secret:
        raise RuntimeError(
            "feishu.app_id / feishu.app_secret 不能为空，请检查 config.yaml"
        )

    max_history_turns = cfg.get("session", {}).get("max_history_turns", 20)
    sandbox_url = cfg.get("sandbox", {}).get("url", "http://localhost:8022/mcp")

    debug_cfg = cfg.get("debug", {})
    enable_test_api = debug_cfg.get("enable_test_api", False)
    test_api_host = debug_cfg.get("test_api_host", "127.0.0.1")
    test_api_port = debug_cfg.get("test_api_port", 9090)

    runner_cfg = cfg.get("runner", {})
    idle_timeout = runner_cfg.get("queue_idle_timeout_s", 300.0)

    # ── 3. 构建 Feishu HTTP Client ─────────────────────────────────────────
    client = (
        Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(LogLevel.INFO)
        .build()
    )

    # ── 4. 初始化核心服务 ───────────────────────────────────────────────────
    session_mgr = SessionManager(data_dir=data_dir)
    sender = FeishuSender(client=client)
    downloader = FeishuDownloader(client=client, data_dir=data_dir)
    cleanup_svc = CleanupService(data_dir=data_dir)

    # 写入飞书凭证到沙盒 .config 目录（凭证不经过 LLM）
    cleanup_svc.write_feishu_credentials(app_id=app_id, app_secret=app_secret)

    # 写入百度千帆 API Key 到沙盒 .config 目录（支持 baidu_search Skill）
    baidu_api_key = cfg.get("baidu", {}).get("api_key", "") or os.environ.get("BAIDU_API_KEY", "")
    cleanup_svc.write_baidu_credentials(api_key=baidu_api_key)

    # 启动时执行一次存储清理（清除历史残留）
    try:
        await cleanup_svc.sweep()
    except Exception:  # noqa: BLE001
        logger.warning("cleanup: startup sweep failed", exc_info=True)

    # ── 5. 构建真实 agent_fn ────────────────────────────────────────────────
    agent_fn = build_agent_fn(
        sender=sender,
        max_history_turns=max_history_turns,
        sandbox_url=sandbox_url,
    )

    # ── 6. 构建 Runner ──────────────────────────────────────────────────────
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        downloader=downloader,
        idle_timeout=idle_timeout,
    )

    # ── 7. CronService ──────────────────────────────────────────────────────
    (data_dir / "cron").mkdir(parents=True, exist_ok=True)
    cron_svc = CronService(data_dir=data_dir, dispatch_fn=runner.dispatch)
    await cron_svc.start()

    # ── 8. WebSocket Listener ───────────────────────────────────────────────
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

    logger.info("XiaoPaw ready. sandbox_url=%s, test_api=%s", sandbox_url, enable_test_api)

    # ── 9. 并行启动所有服务 ─────────────────────────────────────────────────
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
        from xiaopaw.api.test_server import create_test_app  # noqa: PLC0415

        test_app = create_test_app(runner=runner, session_mgr=session_mgr)
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
