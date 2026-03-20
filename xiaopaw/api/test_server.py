"""TestAPI — 模拟飞书消息事件的 HTTP 测试接口

仅在 debug.enable_test_api: true 时由 main.py 启动。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError

from xiaopaw.api.capture_sender import CaptureSender
from xiaopaw.api.schemas import TestRequest, TestResponse
from xiaopaw.models import InboundMessage
from xiaopaw.session.manager import SessionManager
from xiaopaw.observability.metrics import (
    http_requests_total,
    http_request_duration_seconds,
)


_DEFAULT_TIMEOUT = 300.0  # 等待 Bot 回复的默认超时（秒）

_runner_key = web.AppKey("runner", object)
_sender_key = web.AppKey("sender", CaptureSender)
_session_mgr_key = web.AppKey("session_mgr", object)
_workspace_dir_key = web.AppKey("workspace_dir", Path)


def create_test_app(
    runner: object,
    sender: CaptureSender,
    session_mgr: SessionManager | None = None,
    workspace_dir: Path | None = None,
) -> web.Application:
    """创建 aiohttp 应用，注入 Runner、CaptureSender、SessionManager。"""
    app = web.Application()
    app[_runner_key] = runner
    app[_sender_key] = sender
    if session_mgr is not None:
        app[_session_mgr_key] = session_mgr
    if workspace_dir is not None:
        app[_workspace_dir_key] = workspace_dir

    app.router.add_post("/api/test/message", _handle_message)
    app.router.add_delete("/api/test/sessions", _handle_delete_sessions)

    return app


async def _handle_message(request: web.Request) -> web.Response:
    path = "/api/test/message"
    method = request.method
    t_start = time.monotonic()
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    try:
        req = TestRequest.model_validate(body)
    except ValidationError as e:
        resp = web.json_response(
            {"error": e.errors()}, status=422
        )
        duration = time.monotonic() - t_start
        http_requests_total.labels(path=path, method=method, status_code=str(resp.status)).inc()
        http_request_duration_seconds.labels(path=path, method=method).observe(duration)
        return resp

    msg_id = req.msg_id or f"test_{uuid.uuid4().hex[:12]}"
    ts_ms = int(time.time() * 1000)

    content = req.content
    session_mgr = request.app.get(_session_mgr_key)

    # 附件处理：复制本地文件到 session uploads/，改写 content 为沙盒路径提示
    if req.attachment and session_mgr is not None:
        workspace_dir: Path | None = request.app.get(_workspace_dir_key)
        session = await session_mgr.get_or_create(req.routing_key)
        content = await _copy_attachment(
            attachment_path=req.attachment.file_path,
            file_name=req.attachment.file_name,
            session_id=session.id,
            workspace_dir=workspace_dir,
            original_text=content,
        )

    inbound = InboundMessage(
        routing_key=req.routing_key,
        content=content,
        msg_id=msg_id,
        root_id=msg_id,
        sender_id=req.sender_id,
        ts=ts_ms,
    )

    sender = request.app[_sender_key]
    runner = request.app[_runner_key]

    # 注册 Future，dispatch 完成后 send 会 resolve 它
    reply_fut = sender.register(msg_id)

    await runner.dispatch(inbound)
    reply = await asyncio.wait_for(reply_fut, timeout=_DEFAULT_TIMEOUT)
    duration_ms = int((time.monotonic() - t_start) * 1000)

    # 从 SessionManager 获取当前 session_id
    session_id = ""
    if session_mgr is not None:
        session = await session_mgr.get_or_create(req.routing_key)
        session_id = session.id

    resp_obj = TestResponse(
        msg_id=msg_id,
        reply=reply,
        session_id=session_id,
        duration_ms=duration_ms,
        skills_called=[],  # TODO: 从 Trace 获取
    )
    resp = web.json_response(resp_obj.model_dump())
    http_requests_total.labels(path=path, method=method, status_code=str(resp.status)).inc()
    http_request_duration_seconds.labels(path=path, method=method).observe(
        (time.monotonic() - t_start)
    )
    return resp


async def _handle_delete_sessions(request: web.Request) -> web.Response:
    path = "/api/test/sessions"
    method = request.method
    t_start = time.monotonic()
    if _session_mgr_key in request.app:
        session_mgr: SessionManager = request.app[_session_mgr_key]
        await session_mgr.clear_all()
    resp = web.json_response({"status": "ok"})
    duration = time.monotonic() - t_start
    http_requests_total.labels(path=path, method=method, status_code=str(resp.status)).inc()
    http_request_duration_seconds.labels(path=path, method=method).observe(duration)
    return resp


async def _copy_attachment(
    attachment_path: str,
    file_name: str | None,
    session_id: str,
    workspace_dir: Path | None,
    original_text: str,
) -> str:
    """将本地文件复制到 session uploads/ 目录，返回改写后的 content（文件路径提示）。

    workspace_dir 默认为 data/workspace/（相对于当前工作目录）。
    沙盒内可见路径为 /workspace/sessions/{session_id}/uploads/{filename}。
    """
    src = Path(attachment_path)
    if not src.exists():
        return f"（附件文件不存在：{attachment_path}）"

    actual_name = file_name or src.name

    # 目标目录：workspace/sessions/{sid}/uploads/
    base = workspace_dir or Path("data/workspace")
    uploads_dir = base / "sessions" / session_id / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    dest = uploads_dir / actual_name
    await asyncio.to_thread(shutil.copy2, src, dest)

    # 沙盒内可见路径
    sandbox_path = f"/workspace/sessions/{session_id}/uploads/{actual_name}"
    hint = f"用户发来了文件，已自动保存至沙盒路径：\n`{sandbox_path}`\n请根据文件内容和用户意图完成相应处理。"
    if original_text:
        hint += f"\n（用户备注：{original_text}）"
    return hint
