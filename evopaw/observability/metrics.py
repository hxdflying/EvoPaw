from __future__ import annotations

"""Prometheus metrics definitions for EvoPaw."""

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
    "evopaw_feishu_events_total",
    "Number of Feishu events received via WebSocket",
    ["event_type", "chat_type"],
    registry=REGISTRY,
)

inbound_messages_total = Counter(
    "evopaw_inbound_messages_total",
    "Number of InboundMessage objects dispatched to Runner",
    ["routing_key_type", "has_attachment"],
    registry=REGISTRY,
)

runner_workers_active = Gauge(
    "evopaw_runner_workers_active",
    "Number of active per-routing_key workers in Runner",
    ["routing_key_type"],
    registry=REGISTRY,
)

runner_queue_size = Gauge(
    "evopaw_runner_queue_size",
    "Queue size per routing_key in Runner",
    ["routing_key_type"],
    registry=REGISTRY,
)

http_requests_total = Counter(
    "evopaw_http_requests_total",
    "HTTP requests handled by TestAPI and metrics endpoints",
    ["path", "method", "status_code"],
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "evopaw_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["path", "method"],
    registry=REGISTRY,
)

errors_total = Counter(
    "evopaw_errors_total",
    "Errors encountered by various components",
    ["component", "error_type"],
    registry=REGISTRY,
)


# ── ASR / Voice 指标（设计文档 §15）────────────────────────────────

asr_requests_total = Counter(
    "evopaw_asr_requests_total",
    "Total ASR (one-shot transcribe) requests by terminal status",
    # status ∈ {success, ws_connect, submit, disconnect, timeout, task_failed, empty, download}
    ["provider", "status"],
    registry=REGISTRY,
)

asr_latency_seconds = Histogram(
    "evopaw_asr_latency_seconds",
    "End-to-end duration of a one-shot ASR transcribe (service layer)",
    ["provider"],
    registry=REGISTRY,
)

asr_timeouts_total = Counter(
    "evopaw_asr_timeouts_total",
    "ASR requests that hit max_wait_s overall timeout",
    ["provider"],
    registry=REGISTRY,
)

asr_ws_reconnect_total = Counter(
    "evopaw_asr_ws_reconnect_total",
    "Whole-transcribe retries triggered by max_reconnect_retries",
    ["provider"],
    registry=REGISTRY,
)

audio_messages_total = Counter(
    "evopaw_audio_messages_total",
    "Audio messages processed by Runner, by final status",
    # status ∈ {success, asr_failed, no_service, download_failed}
    ["status"],
    registry=REGISTRY,
)

audio_dedup_hits_total = Counter(
    "evopaw_audio_dedup_hits_total",
    "Duplicate msg_id hits filtered out by Runner dedup",
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


def record_asr_request(provider: str, status: str) -> None:
    asr_requests_total.labels(provider=provider or "unknown", status=status).inc()
    if status == "timeout":
        asr_timeouts_total.labels(provider=provider or "unknown").inc()


def record_asr_latency(provider: str, seconds: float) -> None:
    asr_latency_seconds.labels(provider=provider or "unknown").observe(max(0.0, seconds))


def record_asr_ws_reconnect(provider: str) -> None:
    asr_ws_reconnect_total.labels(provider=provider or "unknown").inc()


def record_audio_message(status: str) -> None:
    audio_messages_total.labels(status=status or "unknown").inc()


def record_audio_dedup_hit() -> None:
    audio_dedup_hits_total.inc()


def export_metrics() -> tuple[bytes, str]:
    """Return Prometheus metrics payload and content type."""
    data = generate_latest(REGISTRY)
    return data, CONTENT_TYPE_LATEST

