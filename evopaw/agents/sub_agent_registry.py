"""Sub-Agent task registry —— 进程内任务跟踪与取消（P1-2）。

为支持 `/stop` 取消机制（Runner 同 routing_key 串行队列下，普通消息会排队等待
当前慢任务，普通消息无法及时进入 `_handle_slash`），引入按 `routing_key`
索引的进程内 task registry。

当前 EvoPaw 的主流程是：

    Runner._handle()  → dispatcher.dispatch  → run_skill_agent  → query()

整个调用链在同一个 `_handle` task 内串行 await；`/stop` 时 cancel 主 handle
task，CancelledError 会沿调用栈传播到 sub-agent，无需独立 cancel。

本 registry 的作用主要是：

1. 兜底场景：未来 P2-1 引入显式后台 sub-agent（spawn 后立即返回）后，需要
   `cancel_by_session(routing_key)` 清理那些独立 task。
2. 调试/审计：列举当前 routing_key 上活跃的 sub-agent 数量。

不跨进程，不持久化；多 worker 部署需另外协调。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class SubAgentRegistry:
    """按 routing_key 索引的 sub-agent task 集合。"""

    def __init__(self) -> None:
        # routing_key -> {task_id: asyncio.Task}
        self._tasks: dict[str, dict[str, asyncio.Task]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, routing_key: str, task_id: str, task: asyncio.Task,
    ) -> None:
        """登记一个 sub-agent task。"""
        async with self._lock:
            self._tasks.setdefault(routing_key, {})[task_id] = task

    async def unregister(self, routing_key: str, task_id: str) -> None:
        """注销（task 自然结束时调用）。"""
        async with self._lock:
            bucket = self._tasks.get(routing_key)
            if bucket is None:
                return
            bucket.pop(task_id, None)
            if not bucket:
                self._tasks.pop(routing_key, None)

    async def cancel_by_session(self, routing_key: str) -> int:
        """取消该 routing_key 下所有未完成 task；返回发出 cancel 的数量。"""
        async with self._lock:
            bucket = self._tasks.pop(routing_key, None)
        if not bucket:
            return 0
        count = 0
        for task_id, task in bucket.items():
            if not task.done():
                task.cancel()
                count += 1
                logger.info(
                    "subagent_registry: cancelled task#%s on %s",
                    task_id, routing_key,
                )
        return count

    def active_count(self, routing_key: str) -> int:
        """同步读：当前 routing_key 上的活跃 task 数。"""
        return len(self._tasks.get(routing_key, {}))


_DEFAULT_REGISTRY: SubAgentRegistry | None = None


def get_default_registry() -> SubAgentRegistry:
    """进程级默认 registry。Runner / dispatcher 在没有显式注入时使用它。"""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SubAgentRegistry()
    return _DEFAULT_REGISTRY


def _reset_default_registry_for_tests() -> None:
    """仅供单测使用：重置默认 registry。"""
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None
