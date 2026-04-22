"""Cron 数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CronSchedule:
    """调度配置（三选一：at / every / cron）"""

    kind: str  # "at" | "every" | "cron"
    at_ms: int | None = None  # kind="at": 触发时刻（毫秒时间戳）
    every_ms: int | None = None  # kind="every": 间隔毫秒数
    expr: str | None = None  # kind="cron": cron 表达式
    tz: str | None = None  # kind="cron": 时区，如 "Asia/Shanghai"


@dataclass
class CronPayload:
    """触发时的消息内容"""

    routing_key: str  # 目标 routing_key
    message: str  # 注入为 InboundMessage.content


@dataclass
class CronState:
    """运行状态（由 CronService 维护）"""

    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: str | None = None  # "ok" | "error"
    last_error: str | None = None


@dataclass
class CronJob:
    """一个定时任务"""

    id: str
    name: str
    enabled: bool
    schedule: CronSchedule
    payload: CronPayload
    state: CronState = field(default_factory=CronState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False
