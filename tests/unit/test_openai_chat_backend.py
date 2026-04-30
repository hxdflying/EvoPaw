"""OpenAIChatBackend 单元测试（P3）。

mock `httpx.AsyncClient.post` 的响应；覆盖：
- 文本回复（无 tool_calls）→ TurnResult.text 与 usage 正确
- 单轮 skill_loader 调用 → 收集 skills_called，触发 dispatcher.dispatch
- 多轮 tool_calls 循环 → 终止条件 finish_reason='stop'
- StreamSink on_tool_use / on_tool_result 被调用
- HTTP 401/403 → ProviderAuthError；429 → ProviderRateLimited；4xx 其它 → ProviderInvalidRequest；
  5xx → ProviderTransientError；ConnectError / TimeoutException → ProviderTransientError
- 缺 api_base → ProviderInvalidRequest
- extra_body 白名单：通用字段被防御性过滤掉
- record_llm_call 在成功 / 失败两路都被调用
- tool_call.arguments 是 JSON 字符串 / dict / 非法 JSON 各路径
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from evopaw.agent_backends import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderMaxTurnsExceeded,
    ProviderRateLimited,
    ProviderTransientError,
    TurnRequest,
)
from evopaw.agent_backends._http_chat_base import _HttpChatBackendBase
from evopaw.agent_backends.openai_chat import (
    OpenAIChatBackend,
    _parse_tool_call_arguments,
    _parse_usage,
)

_classify_http_error = _HttpChatBackendBase._classify_http_error
from evopaw.provider_runtime import ResolvedRuntime


# ── helpers ──────────────────────────────────────────────────────────────────


def _runtime(**kw) -> ResolvedRuntime:
    base = dict(
        role="main",
        provider_id="dashscope",
        runtime_family="openai_chat",
        model="qwen3-turbo",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-test-xxx",
    )
    base.update(kw)
    return ResolvedRuntime(**base)


def _req(**kw) -> TurnRequest:
    base = dict(
        role="main",
        runtime=_runtime(),
        system_prompt="你是 evopaw。",
        user_content="你好",
        cwd="/tmp",
        max_turns=4,
    )
    base.update(kw)
    return TurnRequest(**base)


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """造一个 httpx.Response 替身。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
    resp.text = json.dumps(json_data)
    if 200 <= status_code < 300:
        resp.raise_for_status = MagicMock()
    else:
        # 模拟 raise_for_status 抛 HTTPStatusError
        err = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
        resp.raise_for_status = MagicMock(side_effect=err)
    return resp


def _patch_async_client(post_side_effect):
    """patch httpx.AsyncClient context manager；post_side_effect 可为单个值或列表。"""
    instance = MagicMock()

    if isinstance(post_side_effect, list):
        instance.post = AsyncMock(side_effect=post_side_effect)
    elif isinstance(post_side_effect, BaseException) or (
        isinstance(post_side_effect, type) and issubclass(post_side_effect, BaseException)
    ):
        instance.post = AsyncMock(side_effect=post_side_effect)
    else:
        instance.post = AsyncMock(return_value=post_side_effect)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=None)

    client_cls = MagicMock(return_value=cm)
    return patch("evopaw.agent_backends.openai_chat.httpx.AsyncClient", client_cls), instance


# ── 单元层：分类 / 解析辅助 ─────────────────────────────────────────────────


class TestClassifyHttpError:
    def _make_err(self, code: int) -> httpx.HTTPStatusError:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = code
        resp.text = "err body"
        return httpx.HTTPStatusError(
            f"HTTP {code}", request=MagicMock(spec=httpx.Request), response=resp,
        )

    def test_401_to_auth(self):
        assert isinstance(_classify_http_error(self._make_err(401)), ProviderAuthError)

    def test_403_to_auth(self):
        assert isinstance(_classify_http_error(self._make_err(403)), ProviderAuthError)

    def test_429_to_rate_limited(self):
        assert isinstance(_classify_http_error(self._make_err(429)), ProviderRateLimited)

    def test_400_to_invalid_request(self):
        assert isinstance(_classify_http_error(self._make_err(400)), ProviderInvalidRequest)

    def test_404_to_invalid_request(self):
        assert isinstance(_classify_http_error(self._make_err(404)), ProviderInvalidRequest)

    def test_500_to_transient(self):
        assert isinstance(_classify_http_error(self._make_err(500)), ProviderTransientError)

    def test_503_to_transient(self):
        assert isinstance(_classify_http_error(self._make_err(503)), ProviderTransientError)


class TestParseUsage:
    def test_normal(self):
        u = _parse_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        assert u.prompt_tokens == 10
        assert u.completion_tokens == 5
        assert u.total_tokens == 15

    def test_missing_usage(self):
        u = _parse_usage({})
        assert u.prompt_tokens == 0

    def test_invalid_usage(self):
        u = _parse_usage({"usage": "bad"})
        assert u.prompt_tokens == 0


class TestParseToolCallArguments:
    def test_dict_passthrough(self):
        assert _parse_tool_call_arguments({"a": 1}) == {"a": 1}

    def test_json_string(self):
        assert _parse_tool_call_arguments('{"a":1}') == {"a": 1}

    def test_invalid_json_raw_preserved(self):
        out = _parse_tool_call_arguments("not-json")
        assert out == {"_raw": "not-json"}

    def test_non_object_json(self):
        out = _parse_tool_call_arguments("[1,2]")
        assert out == {"_raw": "[1,2]"}

    def test_none(self):
        assert _parse_tool_call_arguments(None) == {}


# ── 集成层：run_turn 主路径 ──────────────────────────────────────────────────


class TestRunTurnTextOnly:
    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        be = OpenAIChatBackend()
        resp = _mock_response({
            "choices": [{
                "message": {"role": "assistant", "content": "你好！"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        })
        patcher, _ = _patch_async_client(resp)
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec:
            result = await be.run_turn(_req())

        assert result.text == "你好！"
        assert result.skills_called == []
        assert result.tool_calls == []
        assert result.usage.prompt_tokens == 7
        assert result.usage.completion_tokens == 3
        assert mock_rec.called
        kwargs = mock_rec.call_args.kwargs
        assert kwargs["outcome"] == "success"
        assert kwargs["provider_id"] == "dashscope"
        assert kwargs["runtime_family"] == "openai_chat"
        assert kwargs["role"] == "main"

    @pytest.mark.asyncio
    async def test_request_body_uses_runtime_model_and_messages(self):
        be = OpenAIChatBackend()
        resp = _mock_response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        patcher, instance = _patch_async_client(resp)
        with patcher:
            await be.run_turn(_req(runtime=_runtime(model="qwen-max")))

        kwargs = instance.post.call_args.kwargs
        assert kwargs["json"]["model"] == "qwen-max"
        msgs = kwargs["json"]["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "你好"

    @pytest.mark.asyncio
    async def test_authorization_header_set(self):
        be = OpenAIChatBackend()
        resp = _mock_response({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})
        patcher, instance = _patch_async_client(resp)
        with patcher:
            await be.run_turn(_req(runtime=_runtime(api_key="sk-ZZZ")))
        headers = instance.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-ZZZ"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_missing_api_base_raises_invalid_request(self):
        be = OpenAIChatBackend()
        with pytest.raises(ProviderInvalidRequest):
            await be.run_turn(_req(runtime=_runtime(api_base=None)))

    @pytest.mark.asyncio
    async def test_request_timeout_passed_to_async_client(self):
        """P2-4：TurnRequest.timeout_s 透传到 httpx.AsyncClient(timeout=...)。"""
        be = OpenAIChatBackend()
        resp = _mock_response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        })

        with patch(
            "evopaw.agent_backends.openai_chat.httpx.AsyncClient"
        ) as mock_client_cls:
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=MagicMock(
                post=AsyncMock(return_value=resp),
            ))
            cm.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = cm

            await be.run_turn(_req(timeout_s=37.5))

        # 第一次实例化 AsyncClient 时传的 timeout 应为 httpx.Timeout(37.5)
        called_timeout = mock_client_cls.call_args.kwargs["timeout"]
        assert isinstance(called_timeout, httpx.Timeout)
        # httpx.Timeout(37.5) 把所有连接/读/写/池超时统一设为 37.5
        assert called_timeout.read == 37.5

    @pytest.mark.asyncio
    async def test_generation_params_from_turn_request(self):
        """P2-1：TurnRequest.max_tokens / temperature / top_p 直接进出站请求体。"""
        be = OpenAIChatBackend()
        resp = _mock_response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        })
        instance = MagicMock()
        instance.post = AsyncMock(return_value=resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=instance)
        cm.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "evopaw.agent_backends.openai_chat.httpx.AsyncClient",
            MagicMock(return_value=cm),
        ):
            await be.run_turn(_req(max_tokens=1024, temperature=0.4, top_p=0.95))

        body = instance.post.call_args.kwargs["json"]
        assert body["max_tokens"] == 1024
        assert body["temperature"] == 0.4
        assert body["top_p"] == 0.95

    @pytest.mark.asyncio
    async def test_generation_params_omitted_when_none(self):
        """P2-1：未指定时不写入请求体（让 provider 用默认）。"""
        be = OpenAIChatBackend()
        resp = _mock_response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        })
        instance = MagicMock()
        instance.post = AsyncMock(return_value=resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=instance)
        cm.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "evopaw.agent_backends.openai_chat.httpx.AsyncClient",
            MagicMock(return_value=cm),
        ):
            await be.run_turn(_req())  # 不传 max_tokens / temperature / top_p

        body = instance.post.call_args.kwargs["json"]
        assert "max_tokens" not in body
        assert "temperature" not in body
        assert "top_p" not in body


class TestRunTurnToolCalls:
    @pytest.mark.asyncio
    async def test_single_skill_loader_call(self):
        be = OpenAIChatBackend()
        first = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "skill_loader",
                            "arguments": '{"skill_name":"ref_skill","task_context":""}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        second = _mock_response({
            "choices": [{"message": {"content": "ref 内容已读"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="<skill_instructions>...</skill_instructions>")

        patcher, instance = _patch_async_client([first, second])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
            ))

        assert result.text == "ref 内容已读"
        assert result.skills_called == ["ref_skill"]
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "skill_loader"
        assert result.tool_calls[0].input["skill_name"] == "ref_skill"
        assert result.usage.prompt_tokens == 22  # 10 + 12 累加
        assert result.usage.completion_tokens == 9
        # dispatcher.dispatch 被实际调用一次
        dispatcher.dispatch.assert_called_once_with("ref_skill", "")

        # 第二次请求体里有 role=tool 消息
        second_call = instance.post.call_args_list[1]
        msgs = second_call.kwargs["json"]["messages"]
        assert any(m["role"] == "tool" and m["tool_call_id"] == "call_1" for m in msgs)

    @pytest.mark.asyncio
    async def test_two_round_tool_calls(self):
        be = OpenAIChatBackend()
        r1 = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "skill_loader", "arguments": '{"skill_name":"ref_skill"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        })
        r2 = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c2", "type": "function",
                        "function": {"name": "skill_loader", "arguments": '{"skill_name":"history_reader"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
        })
        r3 = _mock_response({
            "choices": [{"message": {"content": "搞定"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=["r1-out", "r2-out"])

        patcher, _ = _patch_async_client([r1, r2, r3])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
            ))

        assert result.text == "搞定"
        assert result.skills_called == ["ref_skill", "history_reader"]
        assert dispatcher.dispatch.await_count == 2

    @pytest.mark.asyncio
    async def test_dispatcher_exception_swallowed_into_tool_message(self):
        be = OpenAIChatBackend()
        r1 = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "skill_loader", "arguments": '{"skill_name":"ref_skill"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        r2 = _mock_response({
            "choices": [{"message": {"content": "失败已上报"}, "finish_reason": "stop"}],
            "usage": {},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("boom"))
        patcher, instance = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(backend_hints={"skill_dispatcher": dispatcher}))

        assert result.text == "失败已上报"
        # tool 消息中应有错误说明
        second_call = instance.post.call_args_list[1]
        msgs = second_call.kwargs["json"]["messages"]
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert tool_msgs and "Skill 执行失败" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_no_dispatcher_falls_back_to_friendly_text(self):
        be = OpenAIChatBackend()
        r1 = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "skill_loader", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        })
        r2 = _mock_response({
            "choices": [{"message": {"content": "no-op"}, "finish_reason": "stop"}],
            "usage": {},
        })
        patcher, instance = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req())  # no backend_hints
        assert result.text == "no-op"
        msgs = instance.post.call_args_list[1].kwargs["json"]["messages"]
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert tool_msgs and "skill_dispatcher 未注入" in tool_msgs[0]["content"]


class TestReasoningContentPassthrough:
    """Kimi k2.5/k2.6 / DeepSeek-R1 等 thinking 模型的 reasoning_content 必须原样回填，
    否则下一轮请求会被 400 拒绝（"thinking is enabled but reasoning_content is missing"）。
    """

    @pytest.mark.asyncio
    async def test_reasoning_content_preserved_in_assistant_replay(self):
        be = OpenAIChatBackend()
        r1 = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "我需要先调用 skill_loader 才能回答。",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "skill_loader",
                                     "arguments": '{"skill_name":"ref_skill"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        r2 = _mock_response({
            "choices": [{"message": {"content": "答完"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="<skill_instructions>...</skill_instructions>")

        patcher, instance = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
            ))

        assert result.text == "答完"

        second_msgs = instance.post.call_args_list[1].kwargs["json"]["messages"]
        assistant_replays = [m for m in second_msgs if m["role"] == "assistant"]
        assert len(assistant_replays) == 1
        assert assistant_replays[0]["reasoning_content"] == "我需要先调用 skill_loader 才能回答。"

    @pytest.mark.asyncio
    async def test_no_reasoning_content_when_absent(self):
        """标准 OpenAI 响应没有 reasoning_content；回写时也不应凭空加上该字段。"""
        be = OpenAIChatBackend()
        r1 = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "skill_loader",
                                     "arguments": '{"skill_name":"ref_skill"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        r2 = _mock_response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="x")

        patcher, instance = _patch_async_client([r1, r2])
        with patcher:
            await be.run_turn(_req(backend_hints={"skill_dispatcher": dispatcher}))

        second_msgs = instance.post.call_args_list[1].kwargs["json"]["messages"]
        assistant_replays = [m for m in second_msgs if m["role"] == "assistant"]
        assert len(assistant_replays) == 1
        assert "reasoning_content" not in assistant_replays[0]


class TestRunTurnStreamSink:
    @pytest.mark.asyncio
    async def test_stream_sink_invoked_on_tool_call(self):
        be = OpenAIChatBackend()
        r1 = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "skill_loader", "arguments": '{"skill_name":"ref_skill"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        })
        r2 = _mock_response({
            "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
            "usage": {},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="ok-result")

        sink = MagicMock()
        sink.on_tool_use = AsyncMock()
        sink.on_tool_result = AsyncMock()

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                stream_sink=sink,
            ))

        sink.on_tool_use.assert_called_once()
        use_args = sink.on_tool_use.call_args.args
        assert use_args[0] == "skill_loader"
        sink.on_tool_result.assert_called_once()
        res_args = sink.on_tool_result.call_args.args
        assert res_args[0] == "skill_loader"
        assert res_args[1] == "ok-result"

    @pytest.mark.asyncio
    async def test_stream_sink_exception_swallowed(self):
        be = OpenAIChatBackend()
        r1 = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "skill_loader", "arguments": '{"skill_name":"ref_skill"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        })
        r2 = _mock_response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="x")
        sink = MagicMock()
        sink.on_tool_use = AsyncMock(side_effect=RuntimeError("bad"))
        sink.on_tool_result = AsyncMock(side_effect=RuntimeError("bad"))

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                stream_sink=sink,
            ))
        assert result.text == "ok"  # 主流程未被破坏


# ── 异常归一化 ──────────────────────────────────────────────────────────────


class TestErrorNormalization:
    @pytest.mark.asyncio
    async def test_401_raises_auth(self):
        be = OpenAIChatBackend()
        resp = _mock_response({}, status_code=401)
        patcher, _ = _patch_async_client(resp)
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec, pytest.raises(ProviderAuthError):
            await be.run_turn(_req())
        assert mock_rec.call_args.kwargs["outcome"] == "auth_error"

    @pytest.mark.asyncio
    async def test_429_raises_rate_limited(self):
        be = OpenAIChatBackend()
        resp = _mock_response({}, status_code=429)
        patcher, _ = _patch_async_client(resp)
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec, pytest.raises(ProviderRateLimited):
            await be.run_turn(_req())
        assert mock_rec.call_args.kwargs["outcome"] == "rate_limited"

    @pytest.mark.asyncio
    async def test_400_raises_invalid_request(self):
        be = OpenAIChatBackend()
        resp = _mock_response({}, status_code=400)
        patcher, _ = _patch_async_client(resp)
        with patcher, pytest.raises(ProviderInvalidRequest):
            await be.run_turn(_req())

    @pytest.mark.asyncio
    async def test_500_raises_transient(self):
        be = OpenAIChatBackend()
        resp = _mock_response({}, status_code=500)
        patcher, _ = _patch_async_client(resp)
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec, pytest.raises(ProviderTransientError):
            await be.run_turn(_req())
        assert mock_rec.call_args.kwargs["outcome"] == "transient"

    @pytest.mark.asyncio
    async def test_connect_error_raises_transient(self):
        be = OpenAIChatBackend()
        patcher, _ = _patch_async_client(httpx.ConnectError("dns failed"))
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec, pytest.raises(ProviderTransientError):
            await be.run_turn(_req())
        assert mock_rec.call_args.kwargs["outcome"] == "transient"

    @pytest.mark.asyncio
    async def test_timeout_raises_transient(self):
        be = OpenAIChatBackend()
        patcher, _ = _patch_async_client(httpx.TimeoutException("timed out"))
        with patcher, pytest.raises(ProviderTransientError):
            await be.run_turn(_req())


# ── extra_body 白名单 ───────────────────────────────────────────────────────


class TestExtraBodyWhitelist:
    @pytest.mark.asyncio
    async def test_extra_body_passes_through(self):
        be = OpenAIChatBackend()
        resp = _mock_response({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})
        patcher, instance = _patch_async_client(resp)
        rt = _runtime(extra_body={"enable_thinking": True})
        with patcher:
            await be.run_turn(_req(runtime=rt))
        body = instance.post.call_args.kwargs["json"]
        assert body.get("enable_thinking") is True

    @pytest.mark.asyncio
    async def test_generic_field_in_extra_body_blocked(self):
        # 防御性：即使 runtime.extra_body 不当含有 'model'，也不能覆盖出站 body.model
        be = OpenAIChatBackend()
        resp = _mock_response({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})
        patcher, instance = _patch_async_client(resp)
        rt = _runtime(model="qwen3-turbo", extra_body={"model": "fake-model", "messages": []})
        with patcher:
            await be.run_turn(_req(runtime=rt))
        body = instance.post.call_args.kwargs["json"]
        assert body["model"] == "qwen3-turbo"
        # messages 不能被覆盖（仍是 system + user 两条）
        assert isinstance(body["messages"], list) and len(body["messages"]) == 2


# ── tools schema 注入 ──────────────────────────────────────────────────────


class TestToolsSchemaInjection:
    @pytest.mark.asyncio
    async def test_tools_present_when_dispatcher_given(self):
        be = OpenAIChatBackend()
        resp = _mock_response({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})
        patcher, instance = _patch_async_client(resp)
        dispatcher = MagicMock()
        dispatcher.get_description = MagicMock(return_value="<available_skills></available_skills>")

        with patcher:
            await be.run_turn(_req(backend_hints={"skill_dispatcher": dispatcher}))

        body = instance.post.call_args.kwargs["json"]
        assert "tools" in body
        assert body["tool_choice"] == "auto"
        assert body["tools"][0]["function"]["name"] == "skill_loader"

    @pytest.mark.asyncio
    async def test_tools_absent_when_no_dispatcher(self):
        be = OpenAIChatBackend()
        resp = _mock_response({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})
        patcher, instance = _patch_async_client(resp)
        with patcher:
            await be.run_turn(_req())
        body = instance.post.call_args.kwargs["json"]
        assert "tools" not in body


# ── P2-5：max_turns 耗尽 ────────────────────────────────────────────────────


class TestRunTurnMaxTurns:
    """工具调用循环用尽 max_turns 仍未收敛 → ProviderMaxTurnsExceeded。"""

    @pytest.mark.asyncio
    async def test_tool_loop_exhaustion_raises_max_turns_exceeded(self):
        be = OpenAIChatBackend()
        # 永远返回 tool_calls，从不 finish_reason=stop
        loop_resp = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c", "type": "function",
                        "function": {"name": "skill_loader", "arguments": '{"skill_name":"x"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="loop-result")

        # 每轮都用同一个响应；max_turns=2 → 至少消费两次也不收敛 → 触发 else 分支
        patcher, instance = _patch_async_client([loop_resp, loop_resp])
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec, pytest.raises(ProviderMaxTurnsExceeded):
            await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                max_turns=2,
            ))

        assert mock_rec.called
        assert mock_rec.call_args.kwargs["outcome"] == "max_turns_exceeded"
        # 两轮请求均已发出
        assert instance.post.await_count == 2


# ── P1-4：HTTP backend 工具循环 iteration 事件 ─────────────────────────────


class TestIterationMetric:
    """每轮 for-loop 都应触发 record_llm_tool_iteration；
    final / continue 两种 outcome 与本轮是否带 tool_calls 一一对应。"""

    @pytest.mark.asyncio
    async def test_single_final_iteration_no_tools(self):
        be = OpenAIChatBackend()
        resp = _mock_response({
            "choices": [{
                "message": {"role": "assistant", "content": "你好！"},
                "finish_reason": "stop",
            }],
            "usage": {},
        })
        patcher, _ = _patch_async_client(resp)
        with patcher, patch(
            "evopaw.agent_backends.openai_chat.record_llm_tool_iteration"
        ) as mock_iter:
            await be.run_turn(_req())

        assert mock_iter.call_count == 1
        kw = mock_iter.call_args.kwargs
        assert kw["outcome"] == "final"
        # 位置参数：provider_id, runtime_family, role
        args = mock_iter.call_args.args
        assert args == ("dashscope", "openai_chat", "main")

    @pytest.mark.asyncio
    async def test_two_iterations_continue_then_final(self):
        be = OpenAIChatBackend()
        r1 = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "skill_loader",
                                     "arguments": '{"skill_name":"x"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        })
        r2 = _mock_response({
            "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
            "usage": {},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="r")

        patcher, _ = _patch_async_client([r1, r2])
        with patcher, patch(
            "evopaw.agent_backends.openai_chat.record_llm_tool_iteration"
        ) as mock_iter:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher}, max_turns=4,
            ))

        assert result.text == "done"
        assert mock_iter.call_count == 2
        outcomes = [c.kwargs["outcome"] for c in mock_iter.call_args_list]
        assert outcomes == ["continue", "final"]

    @pytest.mark.asyncio
    async def test_max_turns_exceeded_only_continue_no_final(self):
        """max_turns 耗尽场景：每轮都记 continue，但不会出现 final。
        终态由既有 record_llm_call(outcome=max_turns_exceeded) 体现。"""
        be = OpenAIChatBackend()
        loop_resp = _mock_response({
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c", "type": "function",
                        "function": {"name": "skill_loader",
                                     "arguments": '{"skill_name":"x"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        })
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="r")

        patcher, _ = _patch_async_client([loop_resp, loop_resp])
        with patcher, patch(
            "evopaw.agent_backends.openai_chat.record_llm_tool_iteration"
        ) as mock_iter, pytest.raises(ProviderMaxTurnsExceeded):
            await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher}, max_turns=2,
            ))

        assert mock_iter.call_count == 2
        outcomes = [c.kwargs["outcome"] for c in mock_iter.call_args_list]
        assert outcomes == ["continue", "continue"]
        assert "final" not in outcomes

    @pytest.mark.asyncio
    async def test_iteration_uses_resolved_runtime_labels(self):
        """label 来自 ResolvedRuntime / req.role，不能凭空写死。"""
        be = OpenAIChatBackend()
        resp = _mock_response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        })
        patcher, _ = _patch_async_client(resp)
        rt = _runtime(provider_id="moonshot", runtime_family="openai_chat")
        with patcher, patch(
            "evopaw.agent_backends.openai_chat.record_llm_tool_iteration"
        ) as mock_iter:
            await be.run_turn(_req(role="subagent", runtime=rt))

        args = mock_iter.call_args.args
        assert args == ("moonshot", "openai_chat", "subagent")


# ── P2-2：ToolGate（工具调用拦截 / 改写） ──────────────────────────────────


class TestToolGate:
    """ToolGate.before_tool_use 在 dispatch 前介入：
    - block：reason 作为 tool 结果回写，dispatcher.dispatch 不被调用；
    - allow + rewritten_input：用改写后的 args 替换原 args 进入 dispatch；
    - 异常 / 协议违反：视为 allow（走兜底），主流程不被破坏。
    """

    def _tool_call_payload(self, args_json: str = '{"skill_name":"x"}') -> dict:
        return {
            "choices": [{
                "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "skill_loader", "arguments": args_json},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        }

    def _final_payload(self, text: str = "done") -> dict:
        return {
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {},
        }

    @pytest.mark.asyncio
    async def test_block_skips_dispatch_and_writes_reason(self):
        from evopaw.agent_backends.base import ToolDecision

        be = OpenAIChatBackend()
        r1 = _mock_response(self._tool_call_payload())
        r2 = _mock_response(self._final_payload("after-block"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="should-not-be-called")

        gate = MagicMock()
        gate.before_tool_use = AsyncMock(
            return_value=ToolDecision(action="block", reason="拦截原因：XYZ"),
        )

        patcher, instance = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        assert result.text == "after-block"
        # gate 被调用一次；dispatcher.dispatch 完全没被调
        gate.before_tool_use.assert_awaited_once()
        dispatcher.dispatch.assert_not_called()

        # 第二轮请求里 tool 消息内容是 reason
        msgs = instance.post.call_args_list[1].kwargs["json"]["messages"]
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert tool_msgs and tool_msgs[0]["content"] == "拦截原因：XYZ"

        # ToolCall 记录里 input 仍是原 args，output=block reason
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "skill_loader"
        assert result.tool_calls[0].input == {"skill_name": "x"}
        assert result.tool_calls[0].output == "拦截原因：XYZ"

    @pytest.mark.asyncio
    async def test_block_without_reason_uses_default_text(self):
        from evopaw.agent_backends.base import ToolDecision

        be = OpenAIChatBackend()
        r1 = _mock_response(self._tool_call_payload())
        r2 = _mock_response(self._final_payload())
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="x")
        gate = MagicMock()
        gate.before_tool_use = AsyncMock(return_value=ToolDecision(action="block", reason=""))

        patcher, instance = _patch_async_client([r1, r2])
        with patcher:
            await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        msgs = instance.post.call_args_list[1].kwargs["json"]["messages"]
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        # 默认兜底文案
        assert tool_msgs and "工具调用被拦截" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_block_skipped_skill_does_not_join_skills_called(self):
        from evopaw.agent_backends.base import ToolDecision

        be = OpenAIChatBackend()
        r1 = _mock_response(self._tool_call_payload('{"skill_name":"forbidden"}'))
        r2 = _mock_response(self._final_payload())
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="x")
        gate = MagicMock()
        gate.before_tool_use = AsyncMock(return_value=ToolDecision(action="block", reason="no"))

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        assert result.skills_called == []  # block 后 skill_name 不被记录

    @pytest.mark.asyncio
    async def test_rewrite_input_replaces_args_into_dispatch(self):
        from evopaw.agent_backends.base import ToolDecision

        be = OpenAIChatBackend()
        r1 = _mock_response(self._tool_call_payload(
            '{"skill_name":"orig","task_context":"orig-ctx"}',
        ))
        r2 = _mock_response(self._final_payload())
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="rewritten-ok")

        gate = MagicMock()
        gate.before_tool_use = AsyncMock(return_value=ToolDecision(
            action="allow",
            rewritten_input={"skill_name": "rewritten", "task_context": "new-ctx"},
        ))

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        assert result.text == "done"
        # dispatcher 收到 rewrite 后的参数
        dispatcher.dispatch.assert_awaited_once_with("rewritten", "new-ctx")
        # skills_called 用 rewrite 后的 skill_name
        assert result.skills_called == ["rewritten"]
        # ToolCall.input 也是 rewrite 后的版本
        assert result.tool_calls[0].input == {
            "skill_name": "rewritten", "task_context": "new-ctx",
        }

    @pytest.mark.asyncio
    async def test_allow_without_rewrite_keeps_original_args(self):
        from evopaw.agent_backends.base import ToolDecision

        be = OpenAIChatBackend()
        r1 = _mock_response(self._tool_call_payload(
            '{"skill_name":"keep","task_context":"keep-ctx"}',
        ))
        r2 = _mock_response(self._final_payload())
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="ok")
        gate = MagicMock()
        gate.before_tool_use = AsyncMock(
            return_value=ToolDecision(action="allow"),
        )

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        dispatcher.dispatch.assert_awaited_once_with("keep", "keep-ctx")

    @pytest.mark.asyncio
    async def test_gate_exception_falls_back_to_allow(self):
        be = OpenAIChatBackend()
        r1 = _mock_response(self._tool_call_payload())
        r2 = _mock_response(self._final_payload("ok-after-bug"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="dispatched")
        gate = MagicMock()
        gate.before_tool_use = AsyncMock(side_effect=RuntimeError("gate bug"))

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        # gate 抛错 → 视为 allow，主流程继续；dispatch 仍被调用
        assert result.text == "ok-after-bug"
        dispatcher.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gate_protocol_violation_falls_back_to_allow(self):
        """实现返回非 ToolDecision（如 dict）→ 视为 allow 兜底，不抛错。"""
        be = OpenAIChatBackend()
        r1 = _mock_response(self._tool_call_payload())
        r2 = _mock_response(self._final_payload("ok"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="x")
        gate = MagicMock()
        gate.before_tool_use = AsyncMock(return_value={"action": "block"})  # 非 ToolDecision

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        assert result.text == "ok"
        dispatcher.dispatch.assert_awaited_once()  # 依然按 allow 走

    @pytest.mark.asyncio
    async def test_no_gate_default_behavior_unchanged(self):
        """tool_gate=None（默认）时行为与 P2-2 之前完全一致。"""
        be = OpenAIChatBackend()
        r1 = _mock_response(self._tool_call_payload())
        r2 = _mock_response(self._final_payload())
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="ok")

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
            ))

        assert result.text == "done"
        dispatcher.dispatch.assert_awaited_once()
