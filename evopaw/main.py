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
from evopaw.asr.funasr_realtime_client import FunASRRealtimeClient
from evopaw.asr.service import SpeechRecognitionService
from evopaw.cleanup.service import CleanupService
from evopaw.cron.service import CronService
from evopaw.feishu.downloader import FeishuDownloader
from evopaw.feishu.listener import FeishuListener, run_forever
from evopaw.feishu.sender import FeishuSender
from evopaw.llm import check_claude_cli
from evopaw.memory.context_mgmt import configure_memory_runtime as _cfg_summary_runtime
from evopaw.memory.indexer import (
    configure_memory_runtime as _cfg_index_runtime,
    shutdown_index_clients,
)
from evopaw.observability.logging_config import setup_logging
from evopaw.observability.metrics_server import start_metrics_server
from evopaw.provider_runtime import ResolveError, resolve_runtime
from evopaw.runner import Runner
from evopaw.session.manager import SessionManager

logger = logging.getLogger(__name__)


# Fun-ASR 不带快照号的稳定别名集合；生产环境建议固定到快照版本。
_ASR_MODEL_ALIASES = frozenset({"fun-asr-realtime", "fun-asr-flash-8k-realtime"})


def _warn_if_model_is_alias(model: str) -> None:
    """模型名为稳定别名时发警告；开发联调允许继续使用别名。"""

    if model in _ASR_MODEL_ALIASES:
        logger.warning(
            "ASR model='%s' 是稳定别名，生产建议固定为快照号（如 "
            "fun-asr-realtime-2025-11-07）。",
            model,
        )


def _build_speech_service(asr_cfg: dict) -> SpeechRecognitionService | None:
    """按 config + 环境变量构建 SpeechRecognitionService；未启用或缺凭证时返回 None."""
    if not asr_cfg.get("enabled", False):
        logger.info("ASR disabled by config; voice messages will be ignored.")
        return None
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        logger.warning(
            "ASR enabled but DASHSCOPE_API_KEY is empty; disabling speech service."
        )
        return None
    model = asr_cfg.get("model", "fun-asr-realtime")
    _warn_if_model_is_alias(model)
    client = FunASRRealtimeClient(
        api_key=api_key,
        ws_url=asr_cfg.get(
            "ws_url", "wss://dashscope.aliyuncs.com/api-ws/v1/inference/"
        ),
        model=model,
        audio_format=asr_cfg.get("audio_format", "opus"),
        sample_rate=int(asr_cfg.get("sample_rate", 16000)),
        chunk_bytes=int(asr_cfg.get("chunk_bytes", 1024)),
        chunk_interval_ms=int(asr_cfg.get("chunk_interval_ms", 100)),
        submit_timeout_s=float(asr_cfg.get("submit_timeout_s", 10.0)),
        max_wait_s=float(asr_cfg.get("max_wait_s", 120.0)),
        max_reconnect_retries=int(asr_cfg.get("max_reconnect_retries", 1)),
        provider=asr_cfg.get("provider", "aliyun_funasr_realtime"),
    )
    logger.info("ASR enabled: model=%s", client._model)  # noqa: SLF001
    return SpeechRecognitionService(client)


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. 请先复制 config.yaml.template 并填写配置。"
        )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return data


def _validate_subagent_runtime(sub_runtime) -> None:
    """启动期校验 Sub-Agent runtime。

    task 型 Skill 的执行路径仍是 `SkillDispatcher.dispatch → run_skill_agent
    → claude_agent_sdk.query`，因此 `roles.subagent` 当前必须使用
    `claude_sdk_compat`。启动期显式拒绝不兼容配置，避免第一次调用 task skill
    时才暴露错误。
    """
    if sub_runtime.runtime_family != "claude_sdk_compat":
        raise RuntimeError(
            f"roles.subagent 必须使用 claude_sdk_compat runtime（当前解析为 "
            f"provider={sub_runtime.provider_id} family={sub_runtime.runtime_family}）。"
            "task 类型 Skill 的 Sub-Agent 仍依赖 Claude Agent SDK；跨 provider Sub-Agent "
            "尚未支持。"
        )


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

    # ── 2. 解析主 / 子 Agent runtime ──────────────────────────────────────
    try:
        main_runtime = resolve_runtime("main", cfg)
        sub_runtime  = resolve_runtime("subagent", cfg)
    except ResolveError as e:
        raise RuntimeError(f"主 Agent runtime 解析失败：{e}") from e

    _validate_subagent_runtime(sub_runtime)

    # 只要任一角色使用 claude_sdk_compat，就需要可用的 Claude Code CLI。
    needs_claude_cli = (
        main_runtime.runtime_family == "claude_sdk_compat"
        or sub_runtime.runtime_family == "claude_sdk_compat"
    )
    if needs_claude_cli:
        if not check_claude_cli():
            raise RuntimeError(
                "Claude Code CLI 未安装或不在 PATH 中。"
                "当前 main/subagent 角色解析为 claude_sdk_compat，"
                "请先安装：npm install -g @anthropic-ai/claude-code"
            )
        logger.info("Claude Code CLI detected.")
    else:
        logger.info(
            "main=%s/%s, subagent=%s/%s 均非 claude_sdk_compat，跳过 Claude CLI 检测。",
            main_runtime.provider_id, main_runtime.runtime_family,
            sub_runtime.provider_id, sub_runtime.runtime_family,
        )

    # ── 2b. 配置 memory 模块的 provider runtime ──────────────────────────
    _cfg_summary_runtime(cfg)
    _cfg_index_runtime(cfg)

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
    # main / subagent 模型统一由 resolver 解析，旧 agent.* 字段只在 resolver 内兼容。
    agent_max_turns = agent_cfg.get("max_turns", 50)
    sub_agent_max_turns = agent_cfg.get("sub_agent_max_turns", 20)
    # HTTP backend 消费请求超时；claude_sdk_compat 由 Claude Agent SDK 自行管理。
    agent_timeout_s = float(agent_cfg.get("timeout_s", 120.0))
    # 通用 generation 参数仅由 HTTP backend 消费；None 表示使用 provider 默认。
    agent_max_tokens = agent_cfg.get("max_tokens")
    if agent_max_tokens is not None:
        agent_max_tokens = int(agent_max_tokens)
    agent_temperature = agent_cfg.get("temperature")
    if agent_temperature is not None:
        agent_temperature = float(agent_temperature)
    agent_top_p = agent_cfg.get("top_p")
    if agent_top_p is not None:
        agent_top_p = float(agent_top_p)

    sender_cfg = cfg.get("sender", {})
    sender_max_retries = sender_cfg.get("max_retries", 3)
    sender_retry_backoff = tuple(sender_cfg.get("retry_backoff", [1, 2, 4]))

    runner_cfg = cfg.get("runner", {})
    idle_timeout = runner_cfg.get("queue_idle_timeout_s", 300.0)
    dedup_window_size = int(runner_cfg.get("dedup_window_size", 256))

    asr_cfg = cfg.get("asr", {}) or {}

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
        main_runtime=main_runtime,
        sub_runtime=sub_runtime,
        db_dsn=db_dsn,
        max_history_turns=max_history_turns,
        agent_max_turns=agent_max_turns,
        sub_agent_max_turns=sub_agent_max_turns,
        agent_timeout_s=agent_timeout_s,
        agent_max_tokens=agent_max_tokens,
        agent_temperature=agent_temperature,
        agent_top_p=agent_top_p,
    )

    # ── 6b. 构建 ASR 服务（可选）─────────────────────────────────────────────
    speech_service = _build_speech_service(asr_cfg)

    # ── 7. 构建 Runner ──────────────────────────────────────────────────────
    # 生产 Runner 与 TestAPI Runner 仅 sender 不同，其余装配完全一致
    _make_runner = partial(
        Runner,
        session_mgr=session_mgr,
        agent_fn=agent_fn,
        downloader=downloader,
        idle_timeout=idle_timeout,
        speech_service=speech_service,
        dedup_window_size=dedup_window_size,
        long_audio_threshold_ms=int(asr_cfg.get("long_audio_threshold_ms", 15000)),
        short_wait_s=float(asr_cfg.get("short_wait_s", 10.0)),
        ack_text=asr_cfg.get("ack_text", "语音已收到，正在转写和分析，请稍候。"),
        transcription_title=asr_cfg.get("transcription_title", "语音转写"),
        answer_title=asr_cfg.get("answer_title", "回答"),
        display_transcript=bool(asr_cfg.get("display_transcript", True)),
        include_audio_path=bool(asr_cfg.get("include_audio_path", True)),
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
        on_bot_added=sender.send_welcome_card,
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

        # TestAPI 使用 CaptureSender 截获 agent 回复，供 HTTP 请求同步返回。
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

    try:
        await asyncio.gather(*tasks)
    finally:
        # 优雅关闭：关闭 memory.indexer 持有的 OpenAI client（httpx 连接池）。
        shutdown_index_clients()


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
