"""SubAgentRegistry 单元测试（P1-2）。"""

from __future__ import annotations

import asyncio

import pytest

from evopaw.agents.sub_agent_registry import (
    SubAgentRegistry,
    _reset_default_registry_for_tests,
    get_default_registry,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    _reset_default_registry_for_tests()
    yield
    _reset_default_registry_for_tests()


async def _idle() -> None:
    """长睡 task：注册后等被 cancel 用。"""
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        raise


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_then_active_count(self):
        reg = SubAgentRegistry()
        task = asyncio.create_task(_idle())
        try:
            await reg.register("p2p:u1", "abc12345", task)
            assert reg.active_count("p2p:u1") == 1
            assert reg.active_count("p2p:u_other") == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_unregister_removes(self):
        reg = SubAgentRegistry()
        task = asyncio.create_task(_idle())
        try:
            await reg.register("p2p:u1", "abc12345", task)
            await reg.unregister("p2p:u1", "abc12345")
            assert reg.active_count("p2p:u1") == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_unregister_unknown_is_safe(self):
        reg = SubAgentRegistry()
        # 不应抛
        await reg.unregister("nope", "xx")
        await reg.unregister("nope", "yy")


class TestCancelBySession:
    @pytest.mark.asyncio
    async def test_cancels_all_in_routing_key(self):
        reg = SubAgentRegistry()
        tasks = [asyncio.create_task(_idle()) for _ in range(3)]
        try:
            for i, t in enumerate(tasks):
                await reg.register("p2p:u1", f"id{i:08x}", t)
            count = await reg.cancel_by_session("p2p:u1")
            assert count == 3
            await asyncio.gather(*tasks, return_exceptions=True)
            for t in tasks:
                assert t.cancelled()
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_does_not_touch_other_sessions(self):
        reg = SubAgentRegistry()
        t1 = asyncio.create_task(_idle())
        t2 = asyncio.create_task(_idle())
        try:
            await reg.register("p2p:u1", "11111111", t1)
            await reg.register("p2p:u2", "22222222", t2)
            count = await reg.cancel_by_session("p2p:u1")
            assert count == 1
            assert t1.cancelled() or t1.cancelling() > 0 or t1.done()
            assert not t2.done()
            assert reg.active_count("p2p:u2") == 1
        finally:
            t2.cancel()
            await asyncio.gather(t1, t2, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_cancel_unknown_session_returns_zero(self):
        reg = SubAgentRegistry()
        assert await reg.cancel_by_session("nobody") == 0

    @pytest.mark.asyncio
    async def test_cancel_already_done_task_not_counted(self):
        reg = SubAgentRegistry()

        async def quick():
            return 1

        t = asyncio.create_task(quick())
        await asyncio.gather(t, return_exceptions=True)
        await reg.register("p2p:u1", "deadbeef", t)
        # 已 done 的 task 不应计入 cancel 数
        assert await reg.cancel_by_session("p2p:u1") == 0


class TestDefaultRegistrySingleton:
    def test_same_instance_returned(self):
        a = get_default_registry()
        b = get_default_registry()
        assert a is b

    def test_reset_creates_new(self):
        a = get_default_registry()
        _reset_default_registry_for_tests()
        b = get_default_registry()
        assert a is not b
