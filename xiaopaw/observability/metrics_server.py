from __future__ import annotations

"""轻量级 Prometheus /metrics 服务."""

import asyncio

from aiohttp import web

from xiaopaw.observability.metrics import export_metrics


async def handle_metrics(request: web.Request) -> web.Response:
    data, content_type = export_metrics()
    return web.Response(body=data, content_type=content_type)


async def start_metrics_server(host: str = "127.0.0.1", port: int = 9100) -> None:
    app = web.Application()
    app.router.add_get("/metrics", handle_metrics)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    try:
        # 保持运行直到被取消（保证 runner.cleanup() 得以执行）
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
