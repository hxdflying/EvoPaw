"""AnthropicMessagesBackend 单元测试（P4）。

镜像 test_openai_chat_backend.py 的矩阵，差异点：
- 端点 /v1/messages
- headers 用 x-api-key + anthropic-version
- 请求体含独立 system 字段、必填 max_tokens
- tool 调用响应 `content` 列表里有 tool_use blocks，stop_reason='tool_use'
- tool_result 通过 user 消息回写
- usage: input_tokens / output_tokens
"""

from __future__ import annotations

import json
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
from evopaw.agent_backends.anthropic_messages import (
    AnthropicMessagesBackend,
    _normalize_tool_input,
    _parse_usage,
)

_classify_http_error = _HttpChatBackendBase._classify_http_error
from evopaw.provider_runtime import ResolvedRuntime


# ── helpers ──────────────────────────────────────────────────────────────────


def _runtime(**kw) -> ResolvedRuntime:
    base = dict(
        role="main",
        provider_id="anthropic",
        runtime_family="anthropic_messages",
        model="claude-sonnet-4-6",
        api_base="https://api.anthropic.com",
        api_key="sk-ant-test",
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
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
    resp.text = json.dumps(json_data)
    if 200 <= status_code < 300:
        resp.raise_for_status = MagicMock()
    else:
        err = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
        resp.raise_for_status = MagicMock(side_effect=err)
    return resp


def _patch_async_client(post_side_effect):
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
    return patch(
        "evopaw.agent_backends.anthropic_messages.httpx.AsyncClient", client_cls,
    ), instance


def _text_only_response(text: str = "你好！", in_tok: int = 7, out_tok: int = 3) -> dict:
    return {
        "id": "msg_x",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


def _tool_use_response(
    skill_name: str,
    tool_use_id: str = "toolu_1",
    in_tok: int = 10,
    out_tok: int = 4,
) -> dict:
    return {
        "id": "msg_y",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": tool_use_id, "name": "skill_loader",
             "input": {"skill_name": skill_name}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


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

    def test_500_to_transient(self):
        assert isinstance(_classify_http_error(self._make_err(500)), ProviderTransientError)


class TestParseUsage:
    def test_normal(self):
        u = _parse_usage({"usage": {"input_tokens": 12, "output_tokens": 5}})
        assert u.prompt_tokens == 12
        assert u.completion_tokens == 5
        assert u.total_tokens == 17

    def test_missing_usage(self):
        u = _parse_usage({})
        assert u.prompt_tokens == 0
        assert u.total_tokens == 0

    def test_invalid_usage(self):
        u = _parse_usage({"usage": "bad"})
        assert u.prompt_tokens == 0


class TestNormalizeToolInput:
    def test_dict_passthrough(self):
        assert _normalize_tool_input({"a": 1}) == {"a": 1}

    def test_json_string(self):
        assert _normalize_tool_input('{"a":1}') == {"a": 1}

    def test_invalid_json(self):
        assert _normalize_tool_input("not-json") == {"_raw": "not-json"}

    def test_non_object_json(self):
        assert _normalize_tool_input("[1,2]") == {"_raw": "[1,2]"}

    def test_none(self):
        assert _normalize_tool_input(None) == {}


# ── 集成层：run_turn 主路径 ──────────────────────────────────────────────────


class TestRunTurnTextOnly:
    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        be = AnthropicMessagesBackend()
        patcher, _ = _patch_async_client(_mock_response(_text_only_response()))
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec:
            result = await be.run_turn(_req())

        assert result.text == "你好！"
        assert result.skills_called == []
        assert result.tool_calls == []
        assert result.usage.prompt_tokens == 7
        assert result.usage.completion_tokens == 3
        assert result.usage.total_tokens == 10
        assert mock_rec.called
        kwargs = mock_rec.call_args.kwargs
        assert kwargs["outcome"] == "success"
        assert kwargs["provider_id"] == "anthropic"
        assert kwargs["runtime_family"] == "anthropic_messages"
        assert kwargs["role"] == "main"

    @pytest.mark.asyncio
    async def test_request_body_uses_runtime_model_and_system_top_level(self):
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        with patcher:
            await be.run_turn(_req(
                runtime=_runtime(model="claude-haiku-4-5"),
                system_prompt="SYS",
            ))

        kwargs = instance.post.call_args.kwargs
        body = kwargs["json"]
        assert body["model"] == "claude-haiku-4-5"
        assert body["system"] == "SYS"
        assert "max_tokens" in body  # 必填
        msgs = body["messages"]
        # 仅一条 user 消息
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        # user_content 字符串被规范化为 [{type:text}]
        assert msgs[0]["content"] == [{"type": "text", "text": "你好"}]

    @pytest.mark.asyncio
    async def test_url_endpoint_is_v1_messages(self):
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        with patcher:
            await be.run_turn(_req())
        url = instance.post.call_args.args[0]
        assert url.endswith("/v1/messages")

    @pytest.mark.asyncio
    async def test_headers_use_x_api_key_and_version(self):
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        with patcher:
            await be.run_turn(_req(runtime=_runtime(api_key="sk-ant-ZZZ")))
        headers = instance.post.call_args.kwargs["headers"]
        assert headers["x-api-key"] == "sk-ant-ZZZ"
        assert headers["anthropic-version"] == "2023-06-01"
        assert headers["Content-Type"] == "application/json"
        # 不能误用 Authorization Bearer 头
        assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_default_max_tokens_4096(self):
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        with patcher:
            await be.run_turn(_req())
        body = instance.post.call_args.kwargs["json"]
        assert body["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_missing_api_base_raises_invalid_request(self):
        be = AnthropicMessagesBackend()
        with pytest.raises(ProviderInvalidRequest):
            await be.run_turn(_req(runtime=_runtime(api_base=None)))

    @pytest.mark.asyncio
    async def test_user_content_blocks_passthrough(self):
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        blocks = [
            {"type": "text", "text": "看图"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
        ]
        with patcher:
            await be.run_turn(_req(user_content=blocks))
        msgs = instance.post.call_args.kwargs["json"]["messages"]
        assert msgs[0]["content"] == blocks

    @pytest.mark.asyncio
    async def test_request_timeout_passed_to_async_client(self):
        """TurnRequest.timeout_s 透传到 httpx.AsyncClient(timeout=...)。"""
        be = AnthropicMessagesBackend()
        resp = _mock_response(_text_only_response())

        with patch(
            "evopaw.agent_backends.anthropic_messages.httpx.AsyncClient"
        ) as mock_client_cls:
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=MagicMock(
                post=AsyncMock(return_value=resp),
            ))
            cm.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = cm

            await be.run_turn(_req(timeout_s=42.0))

        called_timeout = mock_client_cls.call_args.kwargs["timeout"]
        assert isinstance(called_timeout, httpx.Timeout)
        assert called_timeout.read == 42.0

    @pytest.mark.asyncio
    async def test_generation_params_from_turn_request(self):
        """TurnRequest.max_tokens / temperature / top_p 直接进出站请求体。"""
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        with patcher:
            await be.run_turn(_req(max_tokens=2048, temperature=0.3, top_p=0.85))
        body = instance.post.call_args.kwargs["json"]
        assert body["max_tokens"] == 2048
        assert body["temperature"] == 0.3
        assert body["top_p"] == 0.85

    @pytest.mark.asyncio
    async def test_generation_params_omitted_when_none(self):
        """temperature / top_p 不指定时不写入请求体（让 provider 用默认）。
        max_tokens 是 Anthropic API 必填字段，缺省回退到 4096。"""
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        with patcher:
            await be.run_turn(_req())  # 不传 max_tokens / temperature / top_p
        body = instance.post.call_args.kwargs["json"]
        assert body["max_tokens"] == 4096
        assert "temperature" not in body
        assert "top_p" not in body


class TestRunTurnToolUse:
    @pytest.mark.asyncio
    async def test_single_skill_loader_call(self):
        be = AnthropicMessagesBackend()
        first = _mock_response(_tool_use_response("ref_skill"))
        second = _mock_response(_text_only_response("结果已读", in_tok=12, out_tok=4))

        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="<skill_instructions>...</skill_instructions>")

        patcher, instance = _patch_async_client([first, second])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
            ))

        assert result.text == "结果已读"
        assert result.skills_called == ["ref_skill"]
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "skill_loader"
        # usage 累加：round1 in=10/out=4 + round2 in=12/out=4
        assert result.usage.prompt_tokens == 22
        assert result.usage.completion_tokens == 8
        assert result.usage.total_tokens == 30

        dispatcher.dispatch.assert_called_once_with("ref_skill", "")

        # 第二次请求体里有 tool_result（user 消息中）
        second_call = instance.post.call_args_list[1]
        msgs = second_call.kwargs["json"]["messages"]
        # messages: [初始 user, assistant(tool_use), user(tool_result)]
        assert len(msgs) == 3
        assert msgs[1]["role"] == "assistant"
        assert isinstance(msgs[1]["content"], list)
        assert any(
            b.get("type") == "tool_use" and b.get("id") == "toolu_1"
            for b in msgs[1]["content"]
        )
        assert msgs[2]["role"] == "user"
        tr = msgs[2]["content"][0]
        assert tr["type"] == "tool_result"
        assert tr["tool_use_id"] == "toolu_1"
        assert "<skill_instructions>" in tr["content"]

    @pytest.mark.asyncio
    async def test_usage_aggregation_two_rounds(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("ref_skill", in_tok=10, out_tok=4))
        r2 = _mock_response(_text_only_response("ok", in_tok=8, out_tok=2))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="x")
        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(backend_hints={"skill_dispatcher": dispatcher}))
        assert result.usage.prompt_tokens == 18
        assert result.usage.completion_tokens == 6
        assert result.usage.total_tokens == 24

    @pytest.mark.asyncio
    async def test_two_round_tool_use(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("ref_skill", tool_use_id="t1"))
        r2 = _mock_response(_tool_use_response("history_reader", tool_use_id="t2"))
        r3 = _mock_response(_text_only_response("搞定"))
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
    async def test_dispatcher_exception_swallowed_into_tool_result(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("ref_skill"))
        r2 = _mock_response(_text_only_response("失败已上报"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("boom"))
        patcher, instance = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(backend_hints={"skill_dispatcher": dispatcher}))

        assert result.text == "失败已上报"
        msgs = instance.post.call_args_list[1].kwargs["json"]["messages"]
        tool_result = msgs[2]["content"][0]
        assert tool_result["type"] == "tool_result"
        assert "Skill 执行失败" in tool_result["content"]

    @pytest.mark.asyncio
    async def test_no_dispatcher_falls_back_to_friendly_text(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("ref_skill"))
        r2 = _mock_response(_text_only_response("noop"))
        patcher, instance = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req())  # 无 backend_hints
        assert result.text == "noop"
        msgs = instance.post.call_args_list[1].kwargs["json"]["messages"]
        tr = msgs[2]["content"][0]
        assert tr["type"] == "tool_result"
        assert "skill_dispatcher 未注入" in tr["content"]


class TestRunTurnStreamSink:
    @pytest.mark.asyncio
    async def test_stream_sink_invoked_on_tool_use(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("ref_skill"))
        r2 = _mock_response(_text_only_response("done"))
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
        assert sink.on_tool_use.call_args.args[0] == "skill_loader"
        sink.on_tool_result.assert_called_once()
        assert sink.on_tool_result.call_args.args[1] == "ok-result"

    @pytest.mark.asyncio
    async def test_stream_sink_exception_swallowed(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("ref_skill"))
        r2 = _mock_response(_text_only_response("ok"))
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
        assert result.text == "ok"


# ── 异常归一化 ──────────────────────────────────────────────────────────────


class TestErrorNormalization:
    @pytest.mark.asyncio
    async def test_401_raises_auth(self):
        be = AnthropicMessagesBackend()
        patcher, _ = _patch_async_client(_mock_response({}, status_code=401))
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec, pytest.raises(ProviderAuthError):
            await be.run_turn(_req())
        assert mock_rec.call_args.kwargs["outcome"] == "auth_error"

    @pytest.mark.asyncio
    async def test_429_raises_rate_limited(self):
        be = AnthropicMessagesBackend()
        patcher, _ = _patch_async_client(_mock_response({}, status_code=429))
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec, pytest.raises(ProviderRateLimited):
            await be.run_turn(_req())
        assert mock_rec.call_args.kwargs["outcome"] == "rate_limited"

    @pytest.mark.asyncio
    async def test_400_raises_invalid_request(self):
        be = AnthropicMessagesBackend()
        patcher, _ = _patch_async_client(_mock_response({}, status_code=400))
        with patcher, pytest.raises(ProviderInvalidRequest):
            await be.run_turn(_req())

    @pytest.mark.asyncio
    async def test_500_raises_transient(self):
        be = AnthropicMessagesBackend()
        patcher, _ = _patch_async_client(_mock_response({}, status_code=500))
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec, pytest.raises(ProviderTransientError):
            await be.run_turn(_req())
        assert mock_rec.call_args.kwargs["outcome"] == "transient"

    @pytest.mark.asyncio
    async def test_connect_error_raises_transient(self):
        be = AnthropicMessagesBackend()
        patcher, _ = _patch_async_client(httpx.ConnectError("dns failed"))
        with patcher, patch(
            "evopaw.agent_backends._http_chat_base.record_llm_call",
        ) as mock_rec, pytest.raises(ProviderTransientError):
            await be.run_turn(_req())
        assert mock_rec.call_args.kwargs["outcome"] == "transient"

    @pytest.mark.asyncio
    async def test_timeout_raises_transient(self):
        be = AnthropicMessagesBackend()
        patcher, _ = _patch_async_client(httpx.TimeoutException("timed out"))
        with patcher, pytest.raises(ProviderTransientError):
            await be.run_turn(_req())


# ── extra_body 白名单 ───────────────────────────────────────────────────────


class TestExtraBodyWhitelist:
    @pytest.mark.asyncio
    async def test_extra_body_passes_through(self):
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        rt = _runtime(extra_body={"metadata": {"user_id": "u1"}})
        with patcher:
            await be.run_turn(_req(runtime=rt))
        body = instance.post.call_args.kwargs["json"]
        assert body.get("metadata") == {"user_id": "u1"}

    @pytest.mark.asyncio
    async def test_generic_field_in_extra_body_blocked(self):
        # 防御性：runtime.extra_body 中如果含 model / messages / system / max_tokens
        # 等通用字段，必须被过滤掉，不能覆盖出站 body
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        rt = _runtime(model="claude-sonnet-4-6", extra_body={
            "model": "fake", "messages": [], "system": "x", "max_tokens": 999,
        })
        with patcher:
            await be.run_turn(_req(runtime=rt, system_prompt="real-sys"))
        body = instance.post.call_args.kwargs["json"]
        assert body["model"] == "claude-sonnet-4-6"
        assert body["system"] == "real-sys"
        assert body["max_tokens"] == 4096
        assert isinstance(body["messages"], list) and len(body["messages"]) == 1


# ── tools schema 注入 ──────────────────────────────────────────────────────


class TestToolsSchemaInjection:
    @pytest.mark.asyncio
    async def test_tools_present_when_dispatcher_given(self):
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        dispatcher = MagicMock()
        dispatcher.get_description = MagicMock(return_value="<available_skills></available_skills>")
        with patcher:
            await be.run_turn(_req(backend_hints={"skill_dispatcher": dispatcher}))
        body = instance.post.call_args.kwargs["json"]
        assert "tools" in body
        assert body["tool_choice"] == {"type": "auto"}
        # Anthropic 形态：name / description / input_schema 平铺
        assert body["tools"][0]["name"] == "skill_loader"
        assert "input_schema" in body["tools"][0]
        # 没有 OpenAI 形态的 type:function 包装
        assert "function" not in body["tools"][0]
        assert "parameters" not in body["tools"][0]

    @pytest.mark.asyncio
    async def test_tools_absent_when_no_dispatcher(self):
        be = AnthropicMessagesBackend()
        patcher, instance = _patch_async_client(_mock_response(_text_only_response()))
        with patcher:
            await be.run_turn(_req())
        body = instance.post.call_args.kwargs["json"]
        assert "tools" not in body


# ── max_turns 耗尽 ─────────────────────────────────────────────────────────


class TestRunTurnMaxTurns:
    """工具调用循环用尽 max_turns 仍未收敛 → ProviderMaxTurnsExceeded。"""

    @pytest.mark.asyncio
    async def test_tool_loop_exhaustion_raises_max_turns_exceeded(self):
        be = AnthropicMessagesBackend()
        # 永远返回 stop_reason=tool_use，从不进入 end_turn
        loop_resp = _mock_response(_tool_use_response("loop_skill"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="loop-result")

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
        assert instance.post.await_count == 2


# ── HTTP backend 工具循环 iteration 事件 ───────────────────────────────────


class TestIterationMetric:
    """每轮 for-loop 都应触发 record_llm_tool_iteration；
    final / continue 两种 outcome 与当前响应 stop_reason 是否 tool_use 一一对应。"""

    @pytest.mark.asyncio
    async def test_single_final_iteration_no_tools(self):
        be = AnthropicMessagesBackend()
        resp = _mock_response(_text_only_response("你好！"))
        patcher, _ = _patch_async_client(resp)
        with patcher, patch(
            "evopaw.agent_backends.anthropic_messages.record_llm_tool_iteration"
        ) as mock_iter:
            await be.run_turn(_req())

        assert mock_iter.call_count == 1
        kw = mock_iter.call_args.kwargs
        assert kw["outcome"] == "final"
        args = mock_iter.call_args.args
        assert args == ("anthropic", "anthropic_messages", "main")

    @pytest.mark.asyncio
    async def test_two_iterations_continue_then_final(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("x"))
        r2 = _mock_response(_text_only_response("done"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="r")

        patcher, _ = _patch_async_client([r1, r2])
        with patcher, patch(
            "evopaw.agent_backends.anthropic_messages.record_llm_tool_iteration"
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
        be = AnthropicMessagesBackend()
        loop_resp = _mock_response(_tool_use_response("loop_skill"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="loop-result")

        patcher, _ = _patch_async_client([loop_resp, loop_resp])
        with patcher, patch(
            "evopaw.agent_backends.anthropic_messages.record_llm_tool_iteration"
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
        be = AnthropicMessagesBackend()
        resp = _mock_response(_text_only_response("ok"))
        patcher, _ = _patch_async_client(resp)
        rt = _runtime(provider_id="anthropic_alt", runtime_family="anthropic_messages")
        with patcher, patch(
            "evopaw.agent_backends.anthropic_messages.record_llm_tool_iteration"
        ) as mock_iter:
            await be.run_turn(_req(role="subagent", runtime=rt))

        args = mock_iter.call_args.args
        assert args == ("anthropic_alt", "anthropic_messages", "subagent")


# ── ToolGate（工具调用拦截 / 改写） ────────────────────────────────────────


class TestToolGate:
    """Anthropic backend 的 ToolGate 行为镜像 OpenAI 测试。"""

    @pytest.mark.asyncio
    async def test_block_skips_dispatch_and_writes_reason(self):
        from evopaw.agent_backends.base import ToolDecision

        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("forbidden_skill"))
        r2 = _mock_response(_text_only_response("after-block"))
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
        gate.before_tool_use.assert_awaited_once()
        dispatcher.dispatch.assert_not_called()

        # 第二轮请求里 user 消息含 tool_result block，content 为 reason
        msgs = instance.post.call_args_list[1].kwargs["json"]["messages"]
        # messages: [user(原), assistant, user(tool_result)]
        last_user = msgs[-1]
        assert last_user["role"] == "user"
        tr_blocks = [b for b in last_user["content"] if b.get("type") == "tool_result"]
        assert tr_blocks and tr_blocks[0]["content"] == "拦截原因：XYZ"

        # ToolCall 记录里 input 仍是原 args，output=block reason
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "skill_loader"
        assert result.tool_calls[0].input == {"skill_name": "forbidden_skill"}
        assert result.tool_calls[0].output == "拦截原因：XYZ"

    @pytest.mark.asyncio
    async def test_block_without_reason_uses_default_text(self):
        from evopaw.agent_backends.base import ToolDecision

        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("x"))
        r2 = _mock_response(_text_only_response("ok"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="x")
        gate = MagicMock()
        gate.before_tool_use = AsyncMock(return_value=ToolDecision(action="block"))

        patcher, instance = _patch_async_client([r1, r2])
        with patcher:
            await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        last_user = instance.post.call_args_list[1].kwargs["json"]["messages"][-1]
        tr_blocks = [b for b in last_user["content"] if b.get("type") == "tool_result"]
        assert tr_blocks and "工具调用被拦截" in tr_blocks[0]["content"]

    @pytest.mark.asyncio
    async def test_block_skipped_skill_does_not_join_skills_called(self):
        from evopaw.agent_backends.base import ToolDecision

        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("forbidden"))
        r2 = _mock_response(_text_only_response("ok"))
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

        assert result.skills_called == []

    @pytest.mark.asyncio
    async def test_rewrite_input_replaces_args_into_dispatch(self):
        from evopaw.agent_backends.base import ToolDecision

        be = AnthropicMessagesBackend()
        # 原 args 只有 skill_name=orig；rewrite 后改为 rewritten + new-ctx
        r1 = _mock_response({
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu_1", "name": "skill_loader",
                 "input": {"skill_name": "orig", "task_context": "orig-ctx"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
        r2 = _mock_response(_text_only_response("done"))
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
        dispatcher.dispatch.assert_awaited_once_with("rewritten", "new-ctx")
        assert result.skills_called == ["rewritten"]
        assert result.tool_calls[0].input == {
            "skill_name": "rewritten", "task_context": "new-ctx",
        }

    @pytest.mark.asyncio
    async def test_allow_without_rewrite_keeps_original_args(self):
        from evopaw.agent_backends.base import ToolDecision

        be = AnthropicMessagesBackend()
        r1 = _mock_response({
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu_1", "name": "skill_loader",
                 "input": {"skill_name": "keep", "task_context": "keep-ctx"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
        r2 = _mock_response(_text_only_response("ok"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="ok")
        gate = MagicMock()
        gate.before_tool_use = AsyncMock(return_value=ToolDecision(action="allow"))

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        dispatcher.dispatch.assert_awaited_once_with("keep", "keep-ctx")

    @pytest.mark.asyncio
    async def test_gate_exception_falls_back_to_allow(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("x"))
        r2 = _mock_response(_text_only_response("ok-after-bug"))
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

        assert result.text == "ok-after-bug"
        dispatcher.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gate_protocol_violation_falls_back_to_allow(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("x"))
        r2 = _mock_response(_text_only_response("ok"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="x")
        gate = MagicMock()
        gate.before_tool_use = AsyncMock(return_value={"action": "block"})

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
                tool_gate=gate,
            ))

        assert result.text == "ok"
        dispatcher.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_gate_default_behavior_unchanged(self):
        be = AnthropicMessagesBackend()
        r1 = _mock_response(_tool_use_response("x"))
        r2 = _mock_response(_text_only_response("done"))
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value="ok")

        patcher, _ = _patch_async_client([r1, r2])
        with patcher:
            result = await be.run_turn(_req(
                backend_hints={"skill_dispatcher": dispatcher},
            ))

        assert result.text == "done"
        dispatcher.dispatch.assert_awaited_once()
