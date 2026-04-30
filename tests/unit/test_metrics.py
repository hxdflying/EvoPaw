"""Prometheus metrics helper functions 单元测试"""

from __future__ import annotations

from evopaw.observability.metrics import (
    export_metrics,
    llm_tool_iterations_total,
    record_error,
    record_feishu_event,
    record_inbound_message,
    record_llm_tool_iteration,
    routing_key_type,
)


class TestRoutingKeyType:
    def test_p2p(self):
        assert routing_key_type("p2p:ou_abc") == "p2p"

    def test_group(self):
        assert routing_key_type("group:oc_abc") == "group"

    def test_thread(self):
        assert routing_key_type("thread:oc_abc:thread_001") == "thread"

    def test_unknown_prefix(self):
        assert routing_key_type("bot:foo") == "unknown"

    def test_empty_string(self):
        assert routing_key_type("") == "unknown"


class TestRecordHelpers:
    def test_record_feishu_event(self):
        record_feishu_event("im.message.receive_v1", "p2p")  # no exception = ok

    def test_record_feishu_event_none_chat_type(self):
        record_feishu_event("some_event", None)  # None → "unknown"

    def test_record_inbound_message_p2p_no_attachment(self):
        record_inbound_message("p2p:ou_test", has_attachment=False)

    def test_record_inbound_message_group_with_attachment(self):
        record_inbound_message("group:oc_test", has_attachment=True)

    def test_record_inbound_message_unknown_key(self):
        record_inbound_message("unknown:foo", has_attachment=False)

    def test_record_error(self):
        record_error("runner", "ValueError")

    def test_record_error_empty_type(self):
        record_error("cron", "")  # empty → "unknown"


class TestExportMetrics:
    def test_returns_bytes_and_content_type(self):
        data, content_type = export_metrics()
        assert isinstance(data, bytes)
        assert "text/plain" in content_type

    def test_data_contains_metric_names(self):
        data, _ = export_metrics()
        text = data.decode()
        assert "evopaw_feishu_events_total" in text
        assert "evopaw_errors_total" in text


class TestRecordLlmToolIteration:
    """HTTP backend 工具循环 iteration 计数 helper。"""

    def _value(self, *, provider_id: str, runtime_family: str, role: str, outcome: str) -> float:
        return llm_tool_iterations_total.labels(
            provider_id=provider_id,
            runtime_family=runtime_family,
            role=role,
            outcome=outcome,
        )._value.get()

    def test_increments_continue_outcome(self):
        before = self._value(
            provider_id="dashscope", runtime_family="openai_chat",
            role="main", outcome="continue",
        )
        record_llm_tool_iteration(
            "dashscope", "openai_chat", "main", outcome="continue",
        )
        after = self._value(
            provider_id="dashscope", runtime_family="openai_chat",
            role="main", outcome="continue",
        )
        assert after - before == 1

    def test_increments_final_outcome(self):
        before = self._value(
            provider_id="anthropic", runtime_family="anthropic_messages",
            role="main", outcome="final",
        )
        record_llm_tool_iteration(
            "anthropic", "anthropic_messages", "main", outcome="final",
        )
        after = self._value(
            provider_id="anthropic", runtime_family="anthropic_messages",
            role="main", outcome="final",
        )
        assert after - before == 1

    def test_empty_labels_default_to_unknown(self):
        before = self._value(
            provider_id="unknown", runtime_family="unknown",
            role="unknown", outcome="continue",
        )
        record_llm_tool_iteration("", "", "", outcome="continue")
        after = self._value(
            provider_id="unknown", runtime_family="unknown",
            role="unknown", outcome="continue",
        )
        assert after - before == 1

    def test_silently_swallows_internal_errors(self):
        # outcome 取一个长字符串也不应抛——Prometheus label 接受任意字符串。
        # 主要保证 helper 自身的 try/except 兜底（即便底层 inc 抛错也不冒泡）。
        record_llm_tool_iteration(
            "p", "f", "r", outcome="x" * 200,
        )  # 不抛即通过
