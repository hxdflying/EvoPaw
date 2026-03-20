"""AliyunLLM 单元测试"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from xiaopaw.llm.aliyun_llm import (
    AliyunLLM,
    _normalize_mcp_tool_arguments,
    _truncate_tool_results,
)


# ── Helpers ────────────────────────────────────────────────────


def _make_llm(**kwargs) -> AliyunLLM:
    defaults = {"model": "qwen-max", "api_key": "test-key"}
    return AliyunLLM(**{**defaults, **kwargs})


def _mock_resp(
    content: str = "hello",
    status_code: int = 200,
    tool_calls: list | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "error body"
    resp.url = "https://dashscope.example.com"

    message: dict = {}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
        message["content"] = None
    else:
        message["content"] = content

    resp.json.return_value = {"choices": [{"message": message}]}
    resp.raise_for_status = MagicMock()
    return resp


# ── Init ───────────────────────────────────────────────────────


class TestAliyunLLMInit:
    def test_valid_init(self):
        llm = _make_llm()
        assert llm.model == "qwen-max"
        assert llm.api_key == "test-key"
        assert llm.region == "cn"

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("QWEN_API_KEY", raising=False)
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API Key"):
            AliyunLLM(model="qwen-max")

    def test_api_key_from_qwen_env(self, monkeypatch):
        monkeypatch.setenv("QWEN_API_KEY", "env-key")
        llm = AliyunLLM(model="qwen-max")
        assert llm.api_key == "env-key"

    def test_api_key_from_dashscope_env(self, monkeypatch):
        monkeypatch.delenv("QWEN_API_KEY", raising=False)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "ds-key")
        llm = AliyunLLM(model="qwen-max")
        assert llm.api_key == "ds-key"

    def test_invalid_region_raises(self):
        with pytest.raises(ValueError, match="地域"):
            _make_llm(region="invalid_region")

    def test_intl_region(self):
        llm = _make_llm(region="intl")
        assert "intl" in llm.endpoint

    def test_retry_count_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_RETRY_COUNT", "5")
        llm = _make_llm(retry_count=None)
        assert llm.retry_count == 5

    def test_retry_count_invalid_env_defaults_to_2(self, monkeypatch):
        monkeypatch.setenv("LLM_RETRY_COUNT", "not_a_number")
        llm = _make_llm(retry_count=None)
        assert llm.retry_count == 2

    def test_explicit_retry_count_overrides_env(self, monkeypatch):
        monkeypatch.setenv("LLM_RETRY_COUNT", "10")
        llm = _make_llm(retry_count=3)
        assert llm.retry_count == 3

    def test_default_image_model(self):
        llm = _make_llm(image_model=None)
        assert llm.image_model == "qwen3-vl-plus"


# ── Context window & capabilities ─────────────────────────────


class TestAliyunLLMCapabilities:
    def test_long_model_200k(self):
        assert _make_llm(model="qwen-long").get_context_window_size() == 200_000

    def test_max_model_131k(self):
        assert _make_llm(model="qwen3-max").get_context_window_size() == 131_072

    def test_plus_model_131k(self):
        assert _make_llm(model="qwen-plus").get_context_window_size() == 131_072

    def test_turbo_model_131k(self):
        assert _make_llm(model="qwen-turbo").get_context_window_size() == 131_072

    def test_flash_model_131k(self):
        assert _make_llm(model="qwen-flash").get_context_window_size() == 131_072

    def test_unknown_model_8k(self):
        assert _make_llm(model="qwen-base").get_context_window_size() == 8_192

    def test_supports_function_calling(self):
        assert _make_llm().supports_function_calling() is True

    def test_supports_stop_words(self):
        assert _make_llm().supports_stop_words() is True


# ── Message validation ─────────────────────────────────────────


class TestValidateMessages:
    def test_valid_user_message(self):
        _make_llm()._validate_messages([{"role": "user", "content": "hi"}])

    def test_valid_system_message(self):
        _make_llm()._validate_messages([{"role": "system", "content": "you are helpful"}])

    def test_valid_tool_message(self):
        _make_llm()._validate_messages([{
            "role": "tool",
            "tool_call_id": "call_001",
            "content": "result",
        }])

    def test_not_dict_raises(self):
        with pytest.raises(ValueError, match="字典"):
            _make_llm()._validate_messages(["not a dict"])

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="role"):
            _make_llm()._validate_messages([{"role": "bot", "content": "hi"}])

    def test_missing_role_raises(self):
        with pytest.raises(ValueError, match="role"):
            _make_llm()._validate_messages([{"content": "hi"}])

    def test_tool_message_missing_tool_call_id_raises(self):
        with pytest.raises(ValueError, match="tool_call_id"):
            _make_llm()._validate_messages([{"role": "tool", "content": "result"}])

    def test_user_message_missing_content_raises(self):
        with pytest.raises(ValueError, match="content"):
            _make_llm()._validate_messages([{"role": "user"}])

    def test_assistant_with_tool_calls_no_content_ok(self):
        _make_llm()._validate_messages([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1"}],
        }])


# ── call() ────────────────────────────────────────────────────


class TestAliyunLLMCall:
    def test_simple_success(self):
        llm = _make_llm()
        with patch("requests.post", return_value=_mock_resp("world")):
            assert llm.call("hello") == "world"

    def test_string_wrapped_to_user_message(self):
        llm = _make_llm(retry_count=0)
        with patch("requests.post", return_value=_mock_resp("reply")) as mock_post:
            llm.call("a question")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][0]["content"] == "a question"

    def test_500_retries_then_succeeds(self):
        llm = _make_llm(retry_count=1)
        fail = _mock_resp(status_code=500)
        fail.raise_for_status.side_effect = requests.HTTPError("500")
        ok = _mock_resp("success")
        with patch("requests.post", side_effect=[fail, ok]):
            assert llm.call("hi") == "success"

    def test_429_retries_then_succeeds(self):
        llm = _make_llm(retry_count=1)
        rate = _mock_resp(status_code=429)
        rate.raise_for_status.side_effect = requests.HTTPError("429")
        ok = _mock_resp("ok")
        with patch("requests.post", side_effect=[rate, ok]):
            assert llm.call("hi") == "ok"

    def test_4xx_raises_immediately(self):
        llm = _make_llm(retry_count=0)
        resp = _mock_resp(status_code=400)
        resp.raise_for_status.side_effect = requests.HTTPError("400")
        with patch("requests.post", return_value=resp):
            with pytest.raises((requests.HTTPError, RuntimeError)):
                llm.call("hi")

    def test_timeout_raises(self):
        llm = _make_llm(retry_count=0)
        with patch("requests.post", side_effect=requests.Timeout):
            with pytest.raises(TimeoutError):
                llm.call("hi")

    def test_request_exception_raises(self):
        llm = _make_llm(retry_count=0)
        with patch("requests.post", side_effect=requests.RequestException("network")):
            with pytest.raises(RuntimeError, match="失败"):
                llm.call("hi")

    def test_empty_content_with_retry_off_raises(self):
        llm = _make_llm(retry_count=0)
        with patch("requests.post", return_value=_mock_resp("")):
            with pytest.raises(ValueError):
                llm.call("hi", _retry_on_empty=False)

    def test_empty_choices_raises(self):
        llm = _make_llm(retry_count=0)
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"choices": []}
        with patch("requests.post", return_value=resp):
            with pytest.raises(ValueError, match="choices"):
                llm.call("hi")

    def test_max_iterations_zero_raises_before_request(self):
        llm = _make_llm()
        with pytest.raises(RuntimeError, match="最大迭代"):
            llm.call("hi", max_iterations=0)

    def test_callbacks_on_llm_start_and_end_called(self):
        llm = _make_llm()
        cb = MagicMock()
        with patch("requests.post", return_value=_mock_resp("hi")):
            llm.call("hello", callbacks=[cb])
        cb.on_llm_start.assert_called_once()
        cb.on_llm_end.assert_called_once()

    def test_callback_exception_does_not_propagate(self):
        llm = _make_llm()
        cb = MagicMock()
        cb.on_llm_start.side_effect = RuntimeError("callback error")
        with patch("requests.post", return_value=_mock_resp("hi")):
            result = llm.call("hello", callbacks=[cb])
        assert result == "hi"

    def test_temperature_included_in_payload(self):
        llm = _make_llm(temperature=0.5)
        with patch("requests.post", return_value=_mock_resp("ok")) as mock_post:
            llm.call("hello")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["temperature"] == 0.5

    def test_stop_words_included_when_set(self):
        llm = _make_llm()
        llm.stop = ["<END>"]
        with patch("requests.post", return_value=_mock_resp("ok")) as mock_post:
            llm.call("hello")
        payload = mock_post.call_args.kwargs["json"]
        assert "stop" in payload

    def test_tool_calls_returned_when_no_available_functions(self):
        tool_calls = [{"id": "c1", "function": {"name": "fn", "arguments": "{}"}}]
        llm = _make_llm()
        with patch("requests.post", return_value=_mock_resp(tool_calls=tool_calls)):
            result = llm.call("hi", available_functions=None)
        assert result == tool_calls


# ── _handle_function_calls ────────────────────────────────────


class TestHandleFunctionCalls:
    def test_function_executed_and_result_sent_back(self):
        tool_calls = [{"id": "c1", "function": {"name": "add", "arguments": '{"x": 1, "y": 2}'}}]
        resp1 = _mock_resp(tool_calls=tool_calls)
        resp2 = _mock_resp("the answer is 3")
        available = {"add": lambda x, y: x + y}
        llm = _make_llm()
        with patch("requests.post", side_effect=[resp1, resp2]):
            result = llm.call("what is 1+2?", available_functions=available)
        assert result == "the answer is 3"

    def test_unknown_function_returns_tool_calls_passthrough(self):
        # available_functions={} 是 falsy，代码直接返回原始 tool_calls（供上层执行）
        tool_calls = [{"id": "c2", "function": {"name": "unknown", "arguments": "{}"}}]
        resp = _mock_resp(tool_calls=tool_calls)
        llm = _make_llm()
        with patch("requests.post", return_value=resp):
            result = llm.call("hi", available_functions=None)
        assert result == tool_calls

    def test_unknown_function_in_handle_appends_unavailable(self):
        # available_functions 非空但不含该函数 → 追加"不可用"消息后继续
        tool_calls = [{"id": "c2", "function": {"name": "unknown", "arguments": "{}"}}]
        resp1 = _mock_resp(tool_calls=tool_calls)
        resp2 = _mock_resp("ok")
        llm = _make_llm()
        with patch("requests.post", side_effect=[resp1, resp2]):
            result = llm.call("hi", available_functions={"other_fn": lambda: None})
        assert result == "ok"

    def test_missing_tool_call_id_raises(self):
        llm = _make_llm()
        with pytest.raises(ValueError, match="id"):
            llm._handle_function_calls(
                [{"function": {"name": "fn", "arguments": "{}"}}],
                [{"role": "user", "content": "hi"}],
                [],
                {},
                5,
            )

    def test_original_messages_not_mutated(self):
        llm = _make_llm()
        original = [{"role": "user", "content": "hi"}]
        snapshot = list(original)
        tool_calls = [{"id": "c3", "function": {"name": "fn", "arguments": "{}"}}]
        resp = _mock_resp("done")
        with patch("requests.post", return_value=resp):
            llm._handle_function_calls(tool_calls, list(original), [], {}, 5)
        assert original == snapshot  # caller's list untouched

    def test_max_iterations_zero_in_handle_raises(self):
        llm = _make_llm()
        with pytest.raises(RuntimeError, match="最大迭代"):
            llm._handle_function_calls([], [], [], {}, 0)

    def test_function_raises_returns_error_string(self):
        tool_calls = [{"id": "c4", "function": {"name": "bad", "arguments": "{}"}}]
        resp1 = _mock_resp(tool_calls=tool_calls)
        resp2 = _mock_resp("handled")
        available = {"bad": lambda: (_ for _ in ()).throw(RuntimeError("boom"))}
        llm = _make_llm()
        with patch("requests.post", side_effect=[resp1, resp2]):
            result = llm.call("hi", available_functions=available)
        assert result == "handled"


# ── _normalize_multimodal_tool_result ─────────────────────────


class TestNormalizeMultimodal:
    def test_normal_message_passthrough(self):
        llm = _make_llm()
        msgs = [{"role": "user", "content": "hello"}]
        result, flag = llm._normalize_multimodal_tool_result(msgs)
        assert result == msgs
        assert flag is False

    def test_non_assistant_passthrough(self):
        llm = _make_llm()
        msgs = [{"role": "tool", "content": "result", "tool_call_id": "c1"}]
        result, flag = llm._normalize_multimodal_tool_result(msgs)
        assert result == msgs
        assert flag is False

    def test_base64_image_detected_and_rewritten(self):
        llm = _make_llm()
        msgs = [{
            "role": "assistant",
            "content": "Add image to content Local Observation: data:image/jpeg;base64,abc123xyz",
        }]
        result, flag = llm._normalize_multimodal_tool_result(msgs)
        assert flag is True
        assert result[0]["role"] == "user"
        assert any(
            part.get("type") == "image_url"
            for part in result[0]["content"]
        )

    def test_http_image_url_detected_and_rewritten(self):
        llm = _make_llm()
        msgs = [{
            "role": "assistant",
            "content": "Add image to content Local Observation: http://example.com/image.jpg",
        }]
        result, flag = llm._normalize_multimodal_tool_result(msgs)
        assert flag is True
        assert result[0]["role"] == "user"

    def test_non_string_content_passthrough(self):
        llm = _make_llm()
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        result, flag = llm._normalize_multimodal_tool_result(msgs)
        assert flag is False


# ── _normalize_mcp_tool_arguments ───────────────────────────────


class TestNormalizeMcpToolArguments:
    """MCP 工具参数规范化：字符串 'None'/'True'/'False' 转为合法 JSON，避免 sandbox_file_operations 等 schema 校验失败。"""

    def test_file_types_none_string_becomes_empty_list(self):
        raw = [{
            "id": "call_1",
            "function": {
                "name": "localhost_8022_mcp_sandbox_file_operations",
                "arguments": '{"action": "list", "path": "/workspace/sessions/s-1/uploads/", "pattern": "None", "recursive": "False", "file_types": "None"}',
            },
        }]
        out = _normalize_mcp_tool_arguments(raw)
        assert len(out) == 1
        args = json.loads(out[0]["function"]["arguments"])
        assert args["file_types"] == []
        assert args["recursive"] is False
        assert "pattern" not in args  # 非 list 的 "None" 被删除

    def test_boolean_strings_normalized(self):
        raw = [{
            "id": "call_2",
            "function": {"name": "mcp_tool", "arguments": '{"recursive": "True", "show_hidden": "False"}'},
        }]
        out = _normalize_mcp_tool_arguments(raw)
        args = json.loads(out[0]["function"]["arguments"])
        assert args["recursive"] is True
        assert args["show_hidden"] is False

    def test_passthrough_valid_json_unchanged(self):
        raw = [{
            "id": "call_3",
            "function": {"name": "mcp_tool", "arguments": '{"action": "read", "path": "/tmp/x"}'},
        }]
        out = _normalize_mcp_tool_arguments(raw)
        assert out[0]["function"]["arguments"] == '{"action": "read", "path": "/tmp/x"}'

    def test_empty_list_unchanged(self):
        raw = [{
            "id": "call_4",
            "function": {"name": "mcp_tool", "arguments": '{"file_types": []}'},
        }]
        out = _normalize_mcp_tool_arguments(raw)
        args = json.loads(out[0]["function"]["arguments"])
        assert args["file_types"] == []


# ── _truncate_tool_results ─────────────────────────────────────


class TestTruncateToolResults:
    """tool 返回过长时截断，避免 payload 过大导致 API 500。"""

    def test_short_tool_content_unchanged(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "c1", "content": "short"},
        ]
        out = _truncate_tool_results(msgs, max_chars=100)
        assert out[1]["content"] == "short"

    def test_long_tool_content_truncated(self):
        long_content = "x" * 200
        msgs = [{"role": "tool", "tool_call_id": "c1", "content": long_content}]
        out = _truncate_tool_results(msgs, max_chars=50)
        assert len(out) == 1
        assert out[0]["content"] != long_content
        assert "截断" in out[0]["content"]
        assert "换用其他方法" in out[0]["content"]

    def test_non_tool_messages_unchanged(self):
        msgs = [{"role": "system", "content": "s" * 2000}]
        out = _truncate_tool_results(msgs, max_chars=100)
        assert out[0]["content"] == "s" * 2000

    def test_max_chars_zero_passthrough(self):
        msgs = [{"role": "tool", "tool_call_id": "c1", "content": "x" * 500}]
        out = _truncate_tool_results(msgs, max_chars=0)
        assert out[0]["content"] == "x" * 500


# ── acall() ───────────────────────────────────────────────────


class TestAliyunLLMAcall:
    async def test_acall_returns_result(self):
        llm = _make_llm()
        with patch("requests.post", return_value=_mock_resp("async result")):
            result = await llm.acall("hello")
        assert result == "async result"

    async def test_acall_propagates_exception(self):
        llm = _make_llm(retry_count=0)
        with patch("requests.post", side_effect=requests.Timeout):
            with pytest.raises(TimeoutError):
                await llm.acall("hi")
