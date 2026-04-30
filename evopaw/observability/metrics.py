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


# ── LLM Provider Runtime 指标（多 provider 改造 P1）────────────────
#
# 指标暂时仅声明、不计数（计数在 P2 接入 backend 后落实）。提前定义指标的目的：
#   1. 让运维 / Grafana 可以基于 label 名提前接好告警面板。
#   2. 在 P1 验收时即可通过 /metrics 看到指标存在（labels 还没 emit）。

llm_calls_total = Counter(
    "evopaw_llm_calls_total",
    "LLM provider 调用次数（按 provider/family/role/outcome 分桶）",
    ["provider_id", "runtime_family", "role", "outcome"],
    registry=REGISTRY,
)

llm_input_tokens_total = Counter(
    "evopaw_llm_input_tokens_total",
    "LLM provider 累计输入 token",
    ["provider_id", "runtime_family", "role"],
    registry=REGISTRY,
)

llm_output_tokens_total = Counter(
    "evopaw_llm_output_tokens_total",
    "LLM provider 累计输出 token",
    ["provider_id", "runtime_family", "role"],
    registry=REGISTRY,
)

llm_latency_seconds = Histogram(
    "evopaw_llm_latency_seconds",
    "LLM 单次调用延迟（秒）",
    ["provider_id", "runtime_family", "role"],
    registry=REGISTRY,
)

# P1-4：HTTP backend 工具循环 iteration 计数。
# 仅在 openai_chat / anthropic_messages 这两类「手写工具循环」的 backend 中递增，
# claude_sdk_compat 由 SDK 驱动循环，本指标对其不发出（这是测试硬保护的不变量）。
# outcome ∈ {"continue", "final"}：
#   - continue: 本轮命中 tool_calls，进入下一轮请求。
#   - final:    本轮收到终止 finish_reason / stop_reason，函数返回 final text。
# max_turns 耗尽场景不会触发 final（直接抛 ProviderMaxTurnsExceeded），通过既有
# llm_calls_total{outcome="max_turns_exceeded"} 体现，不在本指标中重复打点。
llm_tool_iterations_total = Counter(
    "evopaw_llm_tool_iterations_total",
    "HTTP backend 工具循环每轮 iteration 计数（claude_sdk_compat 不发出）",
    ["provider_id", "runtime_family", "role", "outcome"],
    registry=REGISTRY,
)


def record_llm_tool_iteration(
    provider_id: str,
    runtime_family: str,
    role: str,
    *,
    outcome: str,
) -> None:
    """记录一次 HTTP backend 工具循环 iteration。

    outcome ∈ {"continue", "final"}。失败仅记 warning，不抛，避免 metrics 故障
    污染主流程（与 record_llm_call 保持一致）。
    """
    try:
        llm_tool_iterations_total.labels(
            provider_id=provider_id or "unknown",
            runtime_family=runtime_family or "unknown",
            role=role or "unknown",
            outcome=outcome,
        ).inc()
    except Exception:  # noqa: BLE001
        # 不依赖 logger：metrics 模块零业务依赖；调用方自己关心可包再 try。
        pass


def record_llm_call(
    provider_id: str,
    runtime_family: str,
    role: str,
    *,
    outcome: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_seconds: float | None = None,
) -> None:
    """记录一次 LLM 调用结果；P1 仅声明 API，调用点在 P2 接入 backend 后接通。

    outcome ∈ {"success", "error", "rate_limited", "auth_error", "transient"}。
    """
    pid = provider_id or "unknown"
    fam = runtime_family or "unknown"
    rl  = role or "unknown"
    llm_calls_total.labels(provider_id=pid, runtime_family=fam, role=rl, outcome=outcome).inc()
    if input_tokens > 0:
        llm_input_tokens_total.labels(provider_id=pid, runtime_family=fam, role=rl).inc(input_tokens)
    if output_tokens > 0:
        llm_output_tokens_total.labels(provider_id=pid, runtime_family=fam, role=rl).inc(output_tokens)
    if latency_seconds is not None:
        llm_latency_seconds.labels(provider_id=pid, runtime_family=fam, role=rl).observe(
            max(0.0, float(latency_seconds))
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

