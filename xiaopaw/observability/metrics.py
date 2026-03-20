from __future__ import annotations

"""Prometheus metrics definitions for XiaoPaw."""

from typing import Optional

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    CollectorRegistry,
    CONTENT_TYPE_LATEST,
    generate_latest,
)


# 使用独立 registry，便于测试与导出
REGISTRY = CollectorRegistry()


feishu_events_total = Counter(
    "xiaopaw_feishu_events_total",
    "Number of Feishu events received via WebSocket",
    ["event_type", "chat_type"],
    registry=REGISTRY,
)

inbound_messages_total = Counter(
    "xiaopaw_inbound_messages_total",
    "Number of InboundMessage objects dispatched to Runner",
    ["routing_key_type", "has_attachment"],
    registry=REGISTRY,
)

runner_workers_active = Gauge(
    "xiaopaw_runner_workers_active",
    "Number of active per-routing_key workers in Runner",
    ["routing_key_type"],
    registry=REGISTRY,
)

runner_queue_size = Gauge(
    "xiaopaw_runner_queue_size",
    "Queue size per routing_key in Runner",
    ["routing_key_type"],
    registry=REGISTRY,
)

http_requests_total = Counter(
    "xiaopaw_http_requests_total",
    "HTTP requests handled by TestAPI and metrics endpoints",
    ["path", "method", "status_code"],
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "xiaopaw_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["path", "method"],
    registry=REGISTRY,
)

errors_total = Counter(
    "xiaopaw_errors_total",
    "Errors encountered by various components",
    ["component", "error_type"],
    registry=REGISTRY,
)


def routing_key_type(routing_key: str) -> str:
    if routing_key.startswith("p2p:"):
        return "p2p"
    if routing_key.startswith("group:"):
        return "group"
    if routing_key.startswith("thread:"):
        return "thread"
    return "unknown"


def record_feishu_event(event_type: str, chat_type: Optional[str]) -> None:
    feishu_events_total.labels(
        event_type=event_type or "unknown",
        chat_type=chat_type or "unknown",
    ).inc()


def record_inbound_message(routing_key: str, has_attachment: bool) -> None:
    inbound_messages_total.labels(
        routing_key_type=routing_key_type(routing_key),
        has_attachment="true" if has_attachment else "false",
    ).inc()


def record_error(component: str, error_type: str) -> None:
    errors_total.labels(
        component=component,
        error_type=error_type or "unknown",
    ).inc()


def export_metrics() -> tuple[bytes, str]:
    """Return Prometheus metrics payload and content type."""
    data = generate_latest(REGISTRY)
    return data, CONTENT_TYPE_LATEST

