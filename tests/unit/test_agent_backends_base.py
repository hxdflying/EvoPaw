"""evopaw.agent_backends.base 单元测试

只验证协议层的契约，不依赖任何 SDK：
- TurnRequest / TurnResult / Usage / ToolCall 字段约束
- StreamSink Protocol：duck typing 满足 isinstance 检查
- AgentBackend Protocol：同上
- 异常类继承关系
- get_backend：三族（claude_sdk_compat / openai_chat / anthropic_messages）均懒加载
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evopaw.agent_backends import (
    AgentBackend,
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderRateLimited,
    ProviderTransientError,
    ProviderUnknownError,
    StreamSink,
    ToolCall,
    TurnRequest,
    TurnResult,
    Usage,
    get_backend,
    register_backend,
)
from evopaw.agent_backends.base import AgentBackend as AgentBackendProto
from evopaw.provider_runtime import ResolvedRuntime


def _runtime(family: str = "claude_sdk_compat") -> ResolvedRuntime:
    return ResolvedRuntime(
        role="main",
        provider_id="provider_x",
        runtime_family=family,
        model="model_y",
    )


class TestUsage:
    def test_default_zero(self):
        u = Usage()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_custom_values(self):
        u = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        assert u.prompt_tokens == 10
        assert u.completion_tokens == 20
        assert u.total_tokens == 30

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            Usage(unexpected="x")


class TestToolCall:
    def test_minimal(self):
        c = ToolCall(name="search")
        assert c.name == "search"
        assert c.input == {}
        assert c.output is None

    def test_with_input_output(self):
        c = ToolCall(name="search", input={"q": "evopaw"}, output={"hits": 3})
        assert c.input["q"] == "evopaw"
        assert c.output["hits"] == 3


class TestTurnRequest:
    def test_minimal(self):
        rt = _runtime()
        req = TurnRequest(
            role="main",
            runtime=rt,
            system_prompt="sys",
            user_content="hi",
            cwd="/tmp",
        )
        assert req.role == "main"
        assert req.runtime is rt
        assert req.user_content == "hi"
        assert req.max_turns == 50
        assert req.stream_sink is None
        assert req.backend_hints == {}

    def test_user_content_can_be_blocks(self):
        req = TurnRequest(
            role="main",
            runtime=_runtime(),
            system_prompt="",
            user_content=[
                {"type": "text", "text": "hi"},
                {"type": "image", "source": {"data": "..."}},
            ],
            cwd="/tmp",
        )
        assert isinstance(req.user_content, list)
        assert len(req.user_content) == 2

    def test_stream_sink_field_accepts_sink(self):
        class Sink:
            async def on_tool_use(self, name, input_data):
                pass

            async def on_tool_result(self, name, output):
                pass

        req = TurnRequest(
            role="main",
            runtime=_runtime(),
            system_prompt="",
            user_content="hi",
            cwd="/tmp",
            stream_sink=Sink(),
        )
        assert req.stream_sink is not None

    def test_backend_hints_pass_through(self):
        req = TurnRequest(
            role="main",
            runtime=_runtime(),
            system_prompt="",
            user_content="hi",
            cwd="/tmp",
            backend_hints={"mcp_servers": {"evopaw": "<server>"}},
        )
        assert req.backend_hints["mcp_servers"]["evopaw"] == "<server>"

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            TurnRequest(
                role="main",
                runtime=_runtime(),
                system_prompt="",
                user_content="hi",
                cwd="/tmp",
                unknown_field=123,
            )


class TestTurnResult:
    def test_default(self):
        r = TurnResult(text="ok")
        assert r.text == "ok"
        assert r.tool_calls == []
        assert r.skills_called == []
        assert isinstance(r.usage, Usage)

    def test_full(self):
        r = TurnResult(
            text="done",
            tool_calls=[ToolCall(name="t1")],
            skills_called=["pdf", "tavily_search"],
            usage=Usage(total_tokens=42),
            raw={"k": 1},
        )
        assert len(r.tool_calls) == 1
        assert r.skills_called == ["pdf", "tavily_search"]
        assert r.usage.total_tokens == 42
        assert r.raw["k"] == 1

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            TurnResult(text="ok", unexpected="x")


class TestStreamSinkProtocol:
    def test_isinstance_with_duck_typed_class(self):
        class Sink:
            async def on_tool_use(self, name, input_data):
                pass

            async def on_tool_result(self, name, output):
                pass

        assert isinstance(Sink(), StreamSink)

    def test_isinstance_rejects_class_missing_method(self):
        class HalfSink:
            async def on_tool_use(self, name, input_data):
                pass

        assert not isinstance(HalfSink(), StreamSink)


class TestAgentBackendProtocol:
    def test_isinstance_with_run_turn(self):
        class FakeBE:
            async def run_turn(self, req):
                return TurnResult(text="x")

        assert isinstance(FakeBE(), AgentBackend)

    def test_isinstance_protocol_via_base_module(self):
        class FakeBE:
            async def run_turn(self, req):
                return TurnResult(text="x")

        # base.AgentBackend 与 __init__.AgentBackend 是同一对象
        assert AgentBackend is AgentBackendProto

    def test_isinstance_rejects_class_without_run_turn(self):
        class NotABackend:
            async def something_else(self, req):
                return None

        assert not isinstance(NotABackend(), AgentBackend)


class TestExceptionHierarchy:
    def test_all_inherit_runtime_error(self):
        for cls in (
            ProviderTransientError,
            ProviderInvalidRequest,
            ProviderAuthError,
            ProviderRateLimited,
            ProviderUnknownError,
        ):
            assert issubclass(cls, RuntimeError)

    def test_distinct_classes(self):
        assert ProviderTransientError is not ProviderUnknownError
        assert ProviderAuthError is not ProviderRateLimited


class TestGetBackend:
    def test_register_backend_takes_precedence(self):
        # 显式 register 后即使 anthropic_messages 已具备懒加载实现也以注册值为准
        from evopaw.agent_backends import _BACKEND_BY_FAMILY  # type: ignore

        _BACKEND_BY_FAMILY.pop("anthropic_messages", None)

        class FakeBE:
            async def run_turn(self, req):
                return TurnResult(text="fake")

        fake = FakeBE()
        register_backend("anthropic_messages", fake)  # type: ignore[arg-type]
        try:
            rt = _runtime("anthropic_messages")
            assert get_backend(rt) is fake
        finally:
            _BACKEND_BY_FAMILY.pop("anthropic_messages", None)

    def test_claude_sdk_compat_lazy_loads(self):
        # 第一次调用会触发懒导入 claude_sdk.ClaudeSDKCompatBackend
        from evopaw.agent_backends import _BACKEND_BY_FAMILY  # type: ignore

        _BACKEND_BY_FAMILY.pop("claude_sdk_compat", None)
        rt = _runtime("claude_sdk_compat")
        be = get_backend(rt)
        assert hasattr(be, "run_turn")
        # 第二次返回缓存，单例
        assert get_backend(rt) is be

    def test_openai_chat_lazy_loads(self):
        # P3 之后 openai_chat 也走懒加载，返回 OpenAIChatBackend 单例
        from evopaw.agent_backends import _BACKEND_BY_FAMILY  # type: ignore

        _BACKEND_BY_FAMILY.pop("openai_chat", None)
        rt = _runtime("openai_chat")
        be = get_backend(rt)
        assert hasattr(be, "run_turn")
        assert getattr(be, "runtime_family", None) == "openai_chat"
        assert get_backend(rt) is be  # 缓存生效

    def test_anthropic_messages_lazy_loads(self):
        # P4 之后 anthropic_messages 也走懒加载，返回 AnthropicMessagesBackend 单例
        from evopaw.agent_backends import _BACKEND_BY_FAMILY  # type: ignore

        _BACKEND_BY_FAMILY.pop("anthropic_messages", None)
        rt = _runtime("anthropic_messages")
        be = get_backend(rt)
        assert hasattr(be, "run_turn")
        assert getattr(be, "runtime_family", None) == "anthropic_messages"
        assert get_backend(rt) is be  # 缓存生效
