"""Prometheus metrics helper functions 单元测试"""

from __future__ import annotations

from xiaopaw.observability.metrics import (
    export_metrics,
    record_error,
    record_feishu_event,
    record_inbound_message,
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
        assert "xiaopaw_feishu_events_total" in text
        assert "xiaopaw_errors_total" in text
