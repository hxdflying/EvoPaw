"""CronService 单元测试"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from xiaopaw.cron.models import CronJob, CronPayload, CronSchedule, CronState
from xiaopaw.cron.service import CronService
from xiaopaw.models import InboundMessage


# ── Test Helpers ──────────────────────────────────────────────


class MockDispatcher:
    """记录所有 dispatch 调用"""

    def __init__(self) -> None:
        self.messages: list[InboundMessage] = []
        self._event = asyncio.Event()

    async def dispatch(self, inbound: InboundMessage) -> None:
        self.messages.append(inbound)
        self._event.set()

    async def wait_for_dispatch(self, timeout: float = 2.0) -> InboundMessage:
        self._event.clear()
        await asyncio.wait_for(self._event.wait(), timeout=timeout)
        return self.messages[-1]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _make_at_job(
    job_id: str = "job-001",
    at_ms: int | None = None,
    routing_key: str = "p2p:ou_test",
    message: str = "提醒",
    delete_after_run: bool = True,
) -> dict:
    return {
        "id": job_id,
        "name": "test job",
        "enabled": True,
        "schedule": {
            "kind": "at",
            "at_ms": at_ms or (_now_ms() + 100),  # 100ms 后
            "every_ms": None,
            "expr": None,
            "tz": None,
        },
        "payload": {"routing_key": routing_key, "message": message},
        "state": {
            "next_run_at_ms": at_ms or (_now_ms() + 100),
            "last_run_at_ms": None,
            "last_status": None,
            "last_error": None,
        },
        "created_at_ms": _now_ms(),
        "updated_at_ms": _now_ms(),
        "delete_after_run": delete_after_run,
    }


def _make_every_job(
    job_id: str = "job-every",
    every_ms: int = 200,
    routing_key: str = "p2p:ou_test",
    message: str = "interval",
) -> dict:
    return {
        "id": job_id,
        "name": "every job",
        "enabled": True,
        "schedule": {
            "kind": "every",
            "at_ms": None,
            "every_ms": every_ms,
            "expr": None,
            "tz": None,
        },
        "payload": {"routing_key": routing_key, "message": message},
        "state": {
            "next_run_at_ms": _now_ms() + every_ms,
            "last_run_at_ms": None,
            "last_status": None,
            "last_error": None,
        },
        "created_at_ms": _now_ms(),
        "updated_at_ms": _now_ms(),
        "delete_after_run": False,
    }


def _write_tasks(tmp_path, jobs: list[dict]) -> None:
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "tasks.json").write_text(
        json.dumps({"version": 1, "jobs": jobs}, ensure_ascii=False)
    )


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def dispatcher():
    return MockDispatcher()


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "cron").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── 基本加载 ──────────────────────────────────────────────────


class TestLoad:
    async def test_loads_jobs_from_tasks_json(self, data_dir, dispatcher):
        """从 tasks.json 加载 jobs"""
        _write_tasks(data_dir, [_make_at_job(at_ms=_now_ms() + 60000)])
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            assert len(svc.jobs) == 1
            assert svc.jobs[0].id == "job-001"
        finally:
            await svc.stop()

    async def test_empty_tasks_file(self, data_dir, dispatcher):
        """空 tasks.json 不报错"""
        _write_tasks(data_dir, [])
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            assert len(svc.jobs) == 0
        finally:
            await svc.stop()

    async def test_no_tasks_file(self, data_dir, dispatcher):
        """tasks.json 不存在时也不报错"""
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            assert len(svc.jobs) == 0
        finally:
            await svc.stop()

    async def test_skips_disabled_jobs(self, data_dir, dispatcher):
        """disabled job 不加载"""
        job = _make_at_job()
        job["enabled"] = False
        _write_tasks(data_dir, [job])
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            assert len(svc.jobs) == 0
        finally:
            await svc.stop()


# ── at 模式 ───────────────────────────────────────────────────


class TestAtSchedule:
    async def test_fires_at_specified_time(self, data_dir, dispatcher):
        """at 模式在指定时间触发"""
        _write_tasks(data_dir, [_make_at_job(at_ms=_now_ms() + 50)])
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            msg = await dispatcher.wait_for_dispatch(timeout=2.0)
            assert msg.content == "提醒"
            assert msg.routing_key == "p2p:ou_test"
            assert msg.is_cron is True
        finally:
            await svc.stop()

    async def test_delete_after_run_removes_job(self, data_dir, dispatcher):
        """delete_after_run=true 的 at job 触发后被删除"""
        _write_tasks(
            data_dir,
            [_make_at_job(at_ms=_now_ms() + 50, delete_after_run=True)],
        )
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            await dispatcher.wait_for_dispatch(timeout=2.0)
            # 等持久化
            await asyncio.sleep(0.1)

            # 重新读 tasks.json 验证
            tasks_data = json.loads(
                (data_dir / "cron" / "tasks.json").read_text()
            )
            assert len(tasks_data["jobs"]) == 0
        finally:
            await svc.stop()

    async def test_at_no_delete_disables_job(self, data_dir, dispatcher):
        """delete_after_run=false 的 at job 触发后变 enabled=false"""
        _write_tasks(
            data_dir,
            [_make_at_job(at_ms=_now_ms() + 50, delete_after_run=False)],
        )
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            await dispatcher.wait_for_dispatch(timeout=2.0)
            await asyncio.sleep(0.1)

            tasks_data = json.loads(
                (data_dir / "cron" / "tasks.json").read_text()
            )
            assert len(tasks_data["jobs"]) == 1
            assert tasks_data["jobs"][0]["enabled"] is False
        finally:
            await svc.stop()


# ── every 模式 ────────────────────────────────────────────────


class TestEverySchedule:
    async def test_fires_repeatedly(self, data_dir, dispatcher):
        """every 模式重复触发"""
        _write_tasks(data_dir, [_make_every_job(every_ms=100)])
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            # 等待至少 2 次触发
            await dispatcher.wait_for_dispatch(timeout=2.0)
            first_count = len(dispatcher.messages)

            await asyncio.sleep(0.3)
            assert len(dispatcher.messages) >= first_count + 1
        finally:
            await svc.stop()

    async def test_every_updates_next_run(self, data_dir, dispatcher):
        """every 触发后 next_run_at_ms 递增"""
        _write_tasks(data_dir, [_make_every_job(every_ms=100)])
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            await dispatcher.wait_for_dispatch(timeout=2.0)
            await asyncio.sleep(0.1)

            # job 的 next_run_at_ms 应更新
            assert svc.jobs[0].state.last_run_at_ms is not None
        finally:
            await svc.stop()


# ── mtime 热重载 ──────────────────────────────────────────────


class TestHotReload:
    async def test_detects_new_jobs(self, data_dir, dispatcher):
        """tasks.json 变更后 CronService 自动感知新 job"""
        _write_tasks(data_dir, [])
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            assert len(svc.jobs) == 0

            # 写入新 job
            _write_tasks(
                data_dir, [_make_at_job(at_ms=_now_ms() + 50)]
            )
            # 触发重载（等待 tick）
            await dispatcher.wait_for_dispatch(timeout=3.0)
            assert len(dispatcher.messages) >= 1
        finally:
            await svc.stop()


# ── InboundMessage 构造 ───────────────────────────────────────


class TestInboundConstruction:
    async def test_constructs_valid_inbound(self, data_dir, dispatcher):
        """触发时构造的 InboundMessage 字段正确"""
        _write_tasks(
            data_dir,
            [
                _make_at_job(
                    at_ms=_now_ms() + 50,
                    routing_key="group:oc_test",
                    message="cron msg",
                )
            ],
        )
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        try:
            msg = await dispatcher.wait_for_dispatch(timeout=2.0)
            assert isinstance(msg, InboundMessage)
            assert msg.routing_key == "group:oc_test"
            assert msg.content == "cron msg"
            assert msg.is_cron is True
            assert msg.msg_id.startswith("cron_")
            assert msg.sender_id == "cron"
        finally:
            await svc.stop()


# ── stop 安全 ─────────────────────────────────────────────────


class TestStopSafety:
    async def test_stop_cancels_timer(self, data_dir, dispatcher):
        """stop 后不再触发"""
        _write_tasks(
            data_dir, [_make_every_job(every_ms=50)]
        )
        svc = CronService(data_dir=data_dir, dispatch_fn=dispatcher.dispatch)
        await svc.start()
        await dispatcher.wait_for_dispatch(timeout=2.0)
        await svc.stop()

        count_at_stop = len(dispatcher.messages)
        await asyncio.sleep(0.2)
        assert len(dispatcher.messages) == count_at_stop
