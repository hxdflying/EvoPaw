"""logging_config 单元测试 — JsonFormatter 结构化字段输出（n-4 验证）"""

from __future__ import annotations

import json
import logging

import pytest

from evopaw.observability.logging_config import JsonFormatter


@pytest.fixture
def fmt():
    return JsonFormatter()


def _make_record(msg: str, **extra) -> logging.LogRecord:
    """构造一个 LogRecord，模拟 logger.info(msg, extra=extra)。"""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


class TestJsonFormatterExtraFields:
    """JsonFormatter 应将 record 上的 routing_key / session_id / feishu_msg_id 写入 JSON"""

    def test_basic_fields_without_extra(self, fmt):
        """没有 extra 字段时，输出仅含基础字段"""
        record = _make_record("hello")
        payload = json.loads(fmt.format(record))
        assert payload["msg"] == "hello"
        assert payload["level"] == "INFO"
        assert "routing_key" not in payload
        assert "session_id" not in payload
        assert "feishu_msg_id" not in payload

    def test_routing_key_extra(self, fmt):
        record = _make_record("dispatch", routing_key="p2p:ou_001")
        payload = json.loads(fmt.format(record))
        assert payload["routing_key"] == "p2p:ou_001"

    def test_session_id_extra(self, fmt):
        record = _make_record("agent_fn", session_id="s-abc123")
        payload = json.loads(fmt.format(record))
        assert payload["session_id"] == "s-abc123"

    def test_feishu_msg_id_extra(self, fmt):
        record = _make_record("dedup", feishu_msg_id="om_001")
        payload = json.loads(fmt.format(record))
        assert payload["feishu_msg_id"] == "om_001"

    def test_all_three_fields_together(self, fmt):
        record = _make_record(
            "handle",
            routing_key="group:oc_x",
            session_id="s-y",
            feishu_msg_id="om_z",
        )
        payload = json.loads(fmt.format(record))
        assert payload["routing_key"] == "group:oc_x"
        assert payload["session_id"] == "s-y"
        assert payload["feishu_msg_id"] == "om_z"

    def test_other_extra_fields_ignored(self, fmt):
        """非白名单字段不写入 JSON（避免噪音）"""
        record = _make_record("hi", custom_field="should-not-appear")
        payload = json.loads(fmt.format(record))
        assert "custom_field" not in payload
