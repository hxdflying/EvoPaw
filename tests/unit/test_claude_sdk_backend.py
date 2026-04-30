"""ClaudeSDKCompatBackend 单元测试

通过 patch `evopaw.agent_backends.claude_sdk.query` 注入一个 async generator
来模拟 Claude SDK 行为。覆盖：
- 正常返回：text / skills_called / tool_calls 正确
- StreamSink 在 PreToolUse / PostToolUse 触发时被调用
- 与原 build_verbose_hooks 一致：tool_name 缺失时回退 'unknown'
- CLINotFoundError / CLIConnectionError → ProviderTransientError
- 任意异常 → ProviderUnknownError
- options 构造时 model 取自 req.runtime.model
- backend_hints['mcp_servers'] 透传到 build_main_agent_options(mcp_servers=...)
- usage 从 ResultMessage.usage 提取（缺失时为零）
- record_llm_call 被调用（标签正确）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from evopaw.agent_backends import (
    ProviderTransientError,
    ProviderUnknownError,
    TurnRequest,
)
from evopaw.agent_backends.claude_sdk import (
    ClaudeSDKCompatBackend,
    _build_hooks_from_stream_sink,
    _extract_skill_name,
    _extract_usage,
)
from evopaw.provider_runtime import ResolvedRuntime


# ──────────────────────────────────────────────────────────────────
# 测试夹具
# ──────────────────────────────────────────────────────────────────


def _runtime() -> ResolvedRuntime:
    return ResolvedRuntime(
        role="main",
        provider_id="claude_sdk",
        runtime_family="claude_sdk_compat",
        model="claude-sonnet-4-6",
    )


def _request(stream_sink=None, backend_hints=None, runtime=None) -> TurnRequest:
    return TurnRequest(
        role="main",
        runtime=runtime or _runtime(),
        system_prompt="sys",
        user_content="hi",
        cwd="/tmp/sess",
        max_turns=10,
        stream_sink=stream_sink,
        backend_hints=backend_hints or {},
    )


class _FakeToolUseBlock:
    def __init__(self, name: str, input_data: dict):
        self.name = name
        self.input = input_data


class _FakeAssistantMessage:
    def __init__(self, content):
        self.content = content


class _FakeResultMessage:
    def __init__(self, result: str, usage: dict | None = None):
        self.result = result
        if usage is not None:
            self.usage = usage


def _make_query(messages):
    """生成一个 async generator 模拟 SDK query()。"""
    async def _gen(**kwargs):
        for m in messages:
            yield m
    return _gen


def _patch_sdk(messages=None, *, query_side_effect=None):
    """同时 patch query / AssistantMessage / ResultMessage / ToolUseBlock /
    CLINotFoundError / CLIConnectionError，使 backend 内部的 isinstance / 异常
    类型校验对我们的 fake 对象生效。"""
    if query_side_effect is not None:
        async def _q(**kwargs):
            raise query_side_effect
            yield  # noqa: unreachable

        query_func = _q
    else:
        query_func = _make_query(messages or [])

    return patch.multiple(
        "evopaw.agent_backends.claude_sdk",
        query=query_func,
        AssistantMessage=_FakeAssistantMessage,
        ResultMessage=_FakeResultMessage,
        ToolUseBlock=_FakeToolUseBlock,
        CLINotFoundError=type("CLINotFoundError", (Exception,), {}),
        CLIConnectionError=type("CLIConnectionError", (Exception,), {}),
    )


# ──────────────────────────────────────────────────────────────────
# 内部工具函数
# ──────────────────────────────────────────────────────────────────


class TestExtractSkillName:
    def test_skill_loader_with_skill_name(self):
        b = _FakeToolUseBlock("mcp__evopaw__skill_loader", {"skill_name": "tavily_search"})
        assert _extract_skill_name(b) == "tavily_search"

    def test_skill_loader_without_skill_name(self):
        b = _FakeToolUseBlock("mcp__evopaw__skill_loader", {})
        assert _extract_skill_name(b) is None

    def test_non_skill_loader_tool(self):
        b = _FakeToolUseBlock("Bash", {"command": "ls"})
        assert _extract_skill_name(b) is None

    def test_non_dict_input(self):
        b = _FakeToolUseBlock("mcp__evopaw__skill_loader", "garbage")
        assert _extract_skill_name(b) is None


class TestExtractUsage:
    def test_no_usage_attr(self):
        m = SimpleNamespace(result="x")
        u = _extract_usage(m)
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0

    def test_input_output_keys(self):
        m = SimpleNamespace(
            result="x",
            usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        )
        u = _extract_usage(m)
        assert u.prompt_tokens == 10
        assert u.completion_tokens == 20
        assert u.total_tokens == 30

    def test_prompt_completion_keys(self):
        m = SimpleNamespace(
            result="x",
            usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        )
        u = _extract_usage(m)
        assert u.prompt_tokens == 5
        assert u.completion_tokens == 7

    def test_non_dict_usage(self):
        m = SimpleNamespace(result="x", usage="invalid")
        u = _extract_usage(m)
        assert u.prompt_tokens == 0


# ──────────────────────────────────────────────────────────────────
# StreamSink 适配
# ──────────────────────────────────────────────────────────────────


class TestBuildHooksFromStreamSink:
    def test_none_sink_returns_empty(self):
        assert _build_hooks_from_stream_sink(None) == {}

    @pytest.mark.asyncio
    async def test_pre_post_tool_use_invoke_sink(self):
        events = []

        class Sink:
            async def on_tool_use(self, name, input_data):
                events.append(("use", name, input_data))

            async def on_tool_result(self, name, output):
                events.append(("result", name, output))

        hooks = _build_hooks_from_stream_sink(Sink())
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks

        pre_fn = hooks["PreToolUse"][0].hooks[0]
        post_fn = hooks["PostToolUse"][0].hooks[0]

        await pre_fn({"tool_name": "Bash", "tool_input": {"cmd": "ls"}}, "id_1", {})
        await post_fn({"tool_name": "Bash", "tool_response": "ok"}, "id_1", {})

        assert events == [
            ("use", "Bash", {"cmd": "ls"}),
            ("result", "Bash", "ok"),
        ]

    @pytest.mark.asyncio
    async def test_unknown_tool_name_fallback(self):
        events = []

        class Sink:
            async def on_tool_use(self, name, input_data):
                events.append(name)

            async def on_tool_result(self, name, output):
                events.append(name)

        hooks = _build_hooks_from_stream_sink(Sink())
        pre_fn = hooks["PreToolUse"][0].hooks[0]
        await pre_fn({}, "id_x", {})
        assert events == ["unknown"]

    @pytest.mark.asyncio
    async def test_sink_exception_is_swallowed(self):
        class BoomSink:
            async def on_tool_use(self, name, input_data):
                raise RuntimeError("boom")

            async def on_tool_result(self, name, output):
                raise RuntimeError("boom")

        hooks = _build_hooks_from_stream_sink(BoomSink())
        pre_fn = hooks["PreToolUse"][0].hooks[0]
        post_fn = hooks["PostToolUse"][0].hooks[0]
        # 不应抛异常
        assert await pre_fn({"tool_name": "X"}, "id_1", {}) == {}
        assert await post_fn({"tool_name": "X"}, "id_1", {}) == {}


# ──────────────────────────────────────────────────────────────────
# ClaudeSDKCompatBackend.run_turn
# ──────────────────────────────────────────────────────────────────


class TestRunTurnNormal:
    @pytest.mark.asyncio
    async def test_basic_text_returned(self):
        be = ClaudeSDKCompatBackend()
        messages = [_FakeResultMessage(result="hello world")]

        with _patch_sdk(messages):
            result = await be.run_turn(_request())

        assert result.text == "hello world"
        assert result.skills_called == []
        assert result.tool_calls == []

    @pytest.mark.asyncio
    async def test_empty_result_message_handled(self):
        be = ClaudeSDKCompatBackend()
        messages = [_FakeResultMessage(result=None)]  # type: ignore[arg-type]

        with _patch_sdk(messages):
            result = await be.run_turn(_request())

        assert result.text == ""

    @pytest.mark.asyncio
    async def test_skills_called_collected(self):
        be = ClaudeSDKCompatBackend()
        msg = _FakeAssistantMessage([
            _FakeToolUseBlock("mcp__evopaw__skill_loader", {"skill_name": "pdf"}),
            _FakeToolUseBlock("Bash", {"command": "ls"}),
            _FakeToolUseBlock("mcp__evopaw__skill_loader", {"skill_name": "tavily_search"}),
        ])
        messages = [msg, _FakeResultMessage(result="done")]

        with _patch_sdk(messages):
            result = await be.run_turn(_request())

        assert result.skills_called == ["pdf", "tavily_search"]
        assert len(result.tool_calls) == 3
        assert {tc.name for tc in result.tool_calls} == {
            "mcp__evopaw__skill_loader", "Bash",
        }

    @pytest.mark.asyncio
    async def test_options_built_with_runtime_model(self):
        be = ClaudeSDKCompatBackend()
        rt = ResolvedRuntime(
            role="main",
            provider_id="claude_sdk",
            runtime_family="claude_sdk_compat",
            model="claude-haiku-4-5-explicit",
        )
        messages = [_FakeResultMessage(result="ok")]

        captured = {}

        def fake_build_options(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        with _patch_sdk(messages), \
             patch(
                 "evopaw.agent_backends.claude_sdk.build_main_agent_options",
                 side_effect=fake_build_options,
             ):
            await be.run_turn(_request(runtime=rt))

        assert captured["model"] == "claude-haiku-4-5-explicit"
        assert captured["cwd"] == "/tmp/sess"
        assert captured["max_turns"] == 10

    @pytest.mark.asyncio
    async def test_backend_hints_inject_mcp_servers(self):
        be = ClaudeSDKCompatBackend()
        messages = [_FakeResultMessage(result="ok")]
        captured = {}

        def fake_build_options(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        with _patch_sdk(messages), \
             patch(
                 "evopaw.agent_backends.claude_sdk.build_main_agent_options",
                 side_effect=fake_build_options,
             ):
            await be.run_turn(
                _request(backend_hints={"mcp_servers": {"evopaw": "<srv>"}}),
            )

        assert captured["mcp_servers"] == {"evopaw": "<srv>"}

    @pytest.mark.asyncio
    async def test_stream_sink_invoked_via_pretooluse_hook(self):
        be = ClaudeSDKCompatBackend()
        events = []

        class Sink:
            async def on_tool_use(self, name, input_data):
                events.append(("use", name))

            async def on_tool_result(self, name, output):
                events.append(("result", name))

        messages = [_FakeResultMessage(result="ok")]
        captured = {}

        def fake_build_options(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        with _patch_sdk(messages), \
             patch(
                 "evopaw.agent_backends.claude_sdk.build_main_agent_options",
                 side_effect=fake_build_options,
             ):
            await be.run_turn(_request(stream_sink=Sink()))

        # Pre/Post hook 已注册到 options.hooks
        hooks = captured["hooks"]
        pre_fn = hooks["PreToolUse"][0].hooks[0]
        await pre_fn({"tool_name": "Bash"}, "id_1", {})
        post_fn = hooks["PostToolUse"][0].hooks[0]
        await post_fn({"tool_name": "Bash"}, "id_1", {})
        assert events == [("use", "Bash"), ("result", "Bash")]

    @pytest.mark.asyncio
    async def test_no_stream_sink_no_hooks(self):
        be = ClaudeSDKCompatBackend()
        messages = [_FakeResultMessage(result="ok")]
        captured = {}

        def fake_build_options(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        with _patch_sdk(messages), \
             patch(
                 "evopaw.agent_backends.claude_sdk.build_main_agent_options",
                 side_effect=fake_build_options,
             ):
            await be.run_turn(_request(stream_sink=None))

        assert captured["hooks"] == {}

    @pytest.mark.asyncio
    async def test_usage_extracted(self):
        be = ClaudeSDKCompatBackend()
        messages = [
            _FakeResultMessage(
                result="ok",
                usage={"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
            ),
        ]

        with _patch_sdk(messages):
            result = await be.run_turn(_request())

        assert result.usage.prompt_tokens == 100
        assert result.usage.completion_tokens == 200
        assert result.usage.total_tokens == 300


# ──────────────────────────────────────────────────────────────────
# 异常归一化
# ──────────────────────────────────────────────────────────────────


class TestRunTurnErrors:
    @pytest.mark.asyncio
    async def test_cli_not_found_raises_transient(self):
        be = ClaudeSDKCompatBackend()
        # 关键：query() 抛 CLINotFoundError 实例。先 patch 出 fake 类，再用它
        # 当 side_effect。
        FakeCNF = type("CLINotFoundError", (Exception,), {})

        async def boom_query(**kwargs):
            raise FakeCNF("claude not in PATH")
            yield  # noqa: unreachable

        with patch.multiple(
            "evopaw.agent_backends.claude_sdk",
            query=boom_query,
            AssistantMessage=_FakeAssistantMessage,
            ResultMessage=_FakeResultMessage,
            ToolUseBlock=_FakeToolUseBlock,
            CLINotFoundError=FakeCNF,
            CLIConnectionError=type("CLIConnectionError", (Exception,), {}),
        ):
            with pytest.raises(ProviderTransientError) as exc_info:
                await be.run_turn(_request())
            assert "claude not in PATH" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cli_connection_raises_transient(self):
        be = ClaudeSDKCompatBackend()
        FakeCC = type("CLIConnectionError", (Exception,), {})

        async def boom_query(**kwargs):
            raise FakeCC("disconnected")
            yield  # noqa: unreachable

        with patch.multiple(
            "evopaw.agent_backends.claude_sdk",
            query=boom_query,
            AssistantMessage=_FakeAssistantMessage,
            ResultMessage=_FakeResultMessage,
            ToolUseBlock=_FakeToolUseBlock,
            CLINotFoundError=type("CLINotFoundError", (Exception,), {}),
            CLIConnectionError=FakeCC,
        ):
            with pytest.raises(ProviderTransientError):
                await be.run_turn(_request())

    @pytest.mark.asyncio
    async def test_unexpected_exception_raises_unknown(self):
        be = ClaudeSDKCompatBackend()

        async def boom_query(**kwargs):
            raise ValueError("unexpected")
            yield  # noqa: unreachable

        with _patch_sdk(query_side_effect=ValueError("unexpected")):
            with pytest.raises(ProviderUnknownError) as exc_info:
                await be.run_turn(_request())
            assert "unexpected" in str(exc_info.value)


# ──────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────


class TestMetricsRecording:
    @pytest.mark.asyncio
    async def test_record_llm_call_invoked_on_success(self):
        be = ClaudeSDKCompatBackend()
        messages = [_FakeResultMessage(result="ok")]

        with _patch_sdk(messages), \
             patch("evopaw.agent_backends.claude_sdk.record_llm_call") as mock_rec:
            await be.run_turn(_request())

        assert mock_rec.called
        kwargs = mock_rec.call_args[1]
        assert kwargs["provider_id"] == "claude_sdk"
        assert kwargs["runtime_family"] == "claude_sdk_compat"
        assert kwargs["role"] == "main"
        assert kwargs["outcome"] == "success"
        assert kwargs["latency_seconds"] is not None

    @pytest.mark.asyncio
    async def test_record_llm_call_invoked_on_transient_error(self):
        be = ClaudeSDKCompatBackend()
        FakeCNF = type("CLINotFoundError", (Exception,), {})

        async def boom_query(**kwargs):
            raise FakeCNF("x")
            yield  # noqa: unreachable

        with patch.multiple(
            "evopaw.agent_backends.claude_sdk",
            query=boom_query,
            AssistantMessage=_FakeAssistantMessage,
            ResultMessage=_FakeResultMessage,
            ToolUseBlock=_FakeToolUseBlock,
            CLINotFoundError=FakeCNF,
            CLIConnectionError=type("CLIConnectionError", (Exception,), {}),
        ), patch("evopaw.agent_backends.claude_sdk.record_llm_call") as mock_rec:
            with pytest.raises(ProviderTransientError):
                await be.run_turn(_request())

        assert mock_rec.called
        assert mock_rec.call_args[1]["outcome"] == "transient"


# ──────────────────────────────────────────────────────────────────
# claude_sdk_compat 不发出 iteration metric（轮次由 SDK 驱动）
# ──────────────────────────────────────────────────────────────────


class TestNoIterationMetricForSDK:
    """ClaudeSDKCompatBackend 模块不应导入或调用 record_llm_tool_iteration；
    指标定义存在于 metrics.py，但本 backend 路径下不会触发。
    """

    @pytest.mark.asyncio
    async def test_metric_not_called_during_run_turn(self):
        """patch metrics 模块的指标对象，run_turn 完成后 inc 计数应为 0。"""
        be = ClaudeSDKCompatBackend()
        msg = _FakeAssistantMessage([
            _FakeToolUseBlock("mcp__evopaw__skill_loader", {"skill_name": "pdf"}),
        ])
        messages = [msg, _FakeResultMessage(result="done")]

        # 在 metrics 模块层 patch；如果 SDK backend 任何路径下调用了
        # record_llm_tool_iteration，下面的 mock 都会被命中。
        with _patch_sdk(messages), patch(
            "evopaw.observability.metrics.record_llm_tool_iteration"
        ) as mock_iter:
            await be.run_turn(_request())

        assert mock_iter.call_count == 0

    def test_module_does_not_import_iteration_helper(self):
        """source 级别保护：claude_sdk.py 不应 import record_llm_tool_iteration。
        只对 HTTP backend 暴露这个 helper，避免 SDK 路径出现意外 inc。"""
        from pathlib import Path

        src = (
            Path(__file__).parents[2]
            / "evopaw" / "agent_backends" / "claude_sdk.py"
        ).read_text(encoding="utf-8")
        assert "record_llm_tool_iteration" not in src


# ──────────────────────────────────────────────────────────────────
# ToolGate 仅在 HTTP backend 接入；claude_sdk_compat 不消费
# ──────────────────────────────────────────────────────────────────


class TestNoToolGateForSDK:
    """仅 HTTP backend 接入 ToolGate。
    Claude SDK backend 自管工具调用，不读取 req.tool_gate。"""

    def test_module_does_not_reference_tool_gate(self):
        """source 级保护：claude_sdk.py 不应 import 或引用 ToolGate / ToolDecision。"""
        from pathlib import Path

        src = (
            Path(__file__).parents[2]
            / "evopaw" / "agent_backends" / "claude_sdk.py"
        ).read_text(encoding="utf-8")
        assert "ToolGate" not in src
        assert "ToolDecision" not in src
        assert "tool_gate" not in src
