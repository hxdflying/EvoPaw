"""OpenAIChatBackend —— OpenAI-compatible chat completions backend。

直接基于 `httpx.AsyncClient` 调 `{api_base}/chat/completions`，覆盖 DashScope /
OpenRouter / Moonshot / DeepSeek / 本地 vLLM 这类 OpenAI-compatible 端点。

1. **单工具形态**：通过 `req.backend_hints["skill_dispatcher"]` 透传 SkillDispatcher，
   只暴露一个 `skill_loader` 工具（schema 走 `skills_runtime.adapters.openai_tools`），
   避免把 18 个 Skill 展开成 18 个 OpenAI tools。
2. **工具调用循环**：`finish_reason == "tool_calls"` 时按顺序 `await dispatcher.dispatch(...)`
   每个 tool_call，把字符串结果作为 `role=tool` content 塞回 messages，再次请求；
   最多 `req.max_turns` 轮。
3. **StreamSink**：每次调用工具前后触发 `on_tool_use(name, input)` / `on_tool_result(name, output)`，
   文案与 ClaudeSDKCompatBackend 字节级一致。
4. **异常归一化**：
   - 401/403  → ProviderAuthError
   - 429      → ProviderRateLimited
   - 4xx 其它 → ProviderInvalidRequest
   - 5xx / httpx.ConnectError / httpx.TimeoutException → ProviderTransientError
   - 其它     → ProviderUnknownError
5. **usage**：直接读响应 JSON 的 `usage.prompt_tokens / completion_tokens / total_tokens`。
6. **extra_body 白名单**：仅注入 `runtime.extra_body` 中已通过 `ProviderSpec.extra_body_whitelist`
   校验的字段（hermes #8591 教训：避免 OpenRouter 专属字段泄漏到 DashScope 请求）。
7. **record_llm_call** 在成功 / 失败两路都打点。

模块级 import `httpx`，让单测可以 patch `evopaw.agent_backends.openai_chat.httpx.AsyncClient`。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, ClassVar

import httpx

from evopaw.observability.metrics import record_error, record_llm_tool_iteration

from ._http_chat_base import _HttpChatBackendBase
from .base import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderMaxTurnsExceeded,
    ProviderRateLimited,
    ProviderTransientError,
    ProviderUnknownError,
    ToolCall,
    ToolDecision,
    TurnRequest,
    TurnResult,
    Usage,
)


_TOOL_BLOCK_DEFAULT_REASON = "工具调用被拦截（无具体原因）。"


async def _consult_tool_gate(req: TurnRequest, name: str, args: dict) -> ToolDecision:
    """调用 req.tool_gate.before_tool_use，返回归一化的 ToolDecision。

    - gate 缺省 → allow（不改写）
    - 异常 → allow（吞掉，仅记 warning），保护主流程不被 gate bug 卡死
    """
    if req.tool_gate is None:
        return ToolDecision(action="allow")
    try:
        decision = await req.tool_gate.before_tool_use(name, args)
    except Exception:  # noqa: BLE001
        logger.warning(
            "tool_gate.before_tool_use 抛错 (tool=%s)，按 allow 兜底",
            name, exc_info=True,
        )
        return ToolDecision(action="allow")
    if not isinstance(decision, ToolDecision):  # 防御：实现违反协议
        logger.warning(
            "tool_gate.before_tool_use 返回非 ToolDecision (tool=%s, type=%s)，按 allow 兜底",
            name, type(decision).__name__,
        )
        return ToolDecision(action="allow")
    return decision

logger = logging.getLogger(__name__)


# 出站请求体的「通用」字段白名单：仅这些字段允许直接进入 chat/completions 请求。
# provider-specific 字段（如 OpenRouter `provider` / DashScope `enable_thinking`）
# 必须通过 ProviderSpec.extra_body_whitelist 显式声明，再由 runtime.extra_body 注入。
_GENERIC_BODY_FIELDS = frozenset({
    "model", "messages", "tools", "tool_choice",
    "max_tokens", "max_completion_tokens",
    "temperature", "top_p",
    "stream", "response_format",
})


def _build_user_content_blocks(user_content: str | list[dict]) -> Any:
    """OpenAI vision 兼容：list[dict] 多模态保持原样；纯字符串保持字符串。

    多模态 block 形态由调用方负责（main_agent.py 在拼装时已经构造），
    本 backend 不做格式转换，避免与 Claude SDK 形态互相污染。
    """
    return user_content


def _build_messages(req: TurnRequest) -> list[dict]:
    """初始 messages：system + user。历史已经被 main_agent.py 拼到 user_content 文本中。"""
    return [
        {"role": "system", "content": req.system_prompt},
        {"role": "user", "content": _build_user_content_blocks(req.user_content)},
    ]


def _build_request_body(
    req: TurnRequest,
    messages: list[dict],
    tools: list[dict] | None,
) -> dict:
    body: dict[str, Any] = {
        "model": req.runtime.model,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    # 通用 generation 参数走 TurnRequest 字段；None 表示不下发。
    if req.max_tokens is not None:
        body["max_tokens"] = req.max_tokens
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.top_p is not None:
        body["top_p"] = req.top_p

    extra = OpenAIChatBackend._extract_extra_body(req)
    if extra:
        body.update(extra)
    return body


def _build_headers(req: TurnRequest) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if req.runtime.api_key:
        headers["Authorization"] = f"Bearer {req.runtime.api_key}"
    return headers


def _parse_usage(payload: dict) -> Usage:
    usage_raw = payload.get("usage") or {}
    if not isinstance(usage_raw, dict):
        return Usage()
    return Usage(
        prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
        completion_tokens=int(usage_raw.get("completion_tokens") or 0),
        total_tokens=int(usage_raw.get("total_tokens") or 0),
    )


def _parse_tool_call_arguments(raw: Any) -> dict:
    """OpenAI tool_calls.arguments 一般是 JSON 字符串；偶尔会是 dict。统一为 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    return {}


class OpenAIChatBackend(_HttpChatBackendBase):
    """OpenAI-compatible chat completions backend。

    一次 `run_turn` 内完成 [LLM 请求 → 工具调用 → 再请求]^N 的循环；
    每轮请求都经 metrics 打点（合并到一次 record_llm_call 里：聚合 token 与总延迟）。

    异常归一化 / extra_body 白名单防护 / metrics 打点 / 单次 POST 错误映射，
    都复用 `_HttpChatBackendBase`；本类只实现 chat/completions 形态的差异部分。
    """

    runtime_family: ClassVar[str] = "openai_chat"
    _generic_body_fields: ClassVar[frozenset[str]] = _GENERIC_BODY_FIELDS

    async def run_turn(self, req: TurnRequest) -> TurnResult:  # noqa: PLR0912, PLR0915
        api_base = (req.runtime.api_base or "").rstrip("/")
        if not api_base:
            raise ProviderInvalidRequest(
                f"runtime.api_base 缺失（provider_id={req.runtime.provider_id}）"
            )
        url = f"{api_base}/chat/completions"

        dispatcher = req.backend_hints.get("skill_dispatcher")
        tools_schema: list[dict] | None = None
        if dispatcher is not None:
            from evopaw.skills_runtime.adapters.openai_tools import (  # noqa: PLC0415
                build_openai_tool_schema,
            )

            tools_schema = [build_openai_tool_schema(dispatcher)]

        messages = _build_messages(req)
        headers = _build_headers(req)

        skills_called: list[str] = []
        tool_calls_collected: list[ToolCall] = []
        agg_usage = Usage()
        final_text = ""
        outcome = "success"
        started_at = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(req.timeout_s)) as client:
                for _turn in range(max(1, req.max_turns)):
                    body = _build_request_body(req, messages, tools_schema)
                    payload = await self._post_json(client, url, headers, body)

                    # 累计 usage（多轮工具调用每轮都返回 usage）
                    turn_usage = _parse_usage(payload)
                    agg_usage = Usage(
                        prompt_tokens=agg_usage.prompt_tokens + turn_usage.prompt_tokens,
                        completion_tokens=agg_usage.completion_tokens + turn_usage.completion_tokens,
                        total_tokens=agg_usage.total_tokens + turn_usage.total_tokens,
                    )

                    choice = (payload.get("choices") or [{}])[0]
                    msg = choice.get("message") or {}
                    finish_reason = choice.get("finish_reason")
                    raw_tool_calls = msg.get("tool_calls") or []

                    if raw_tool_calls and finish_reason in ("tool_calls", None):
                        # 当前响应命中 tool_calls，继续工具循环。
                        record_llm_tool_iteration(
                            req.runtime.provider_id,
                            req.runtime.runtime_family,
                            req.role,
                            outcome="continue",
                        )
                        # assistant 消息（携带 tool_calls）必须先回写 messages。
                        # 注：Kimi k2.5/k2.6 等 thinking 模型在响应里返回 `reasoning_content`，
                        # 多轮回填时必须把同一字段原样写回，否则下一次请求会被 400 拒绝
                        # ("thinking is enabled but reasoning_content is missing")。
                        # 标准 OpenAI 响应没有该字段，msg.get() 为 None 时跳过即可。
                        assistant_msg: dict[str, Any] = {
                            "role": "assistant",
                            "content": msg.get("content") or "",
                            "tool_calls": raw_tool_calls,
                        }
                        reasoning = msg.get("reasoning_content")
                        if reasoning is not None:
                            assistant_msg["reasoning_content"] = reasoning
                        messages.append(assistant_msg)

                        # 顺序执行每个 tool_call
                        for tc in raw_tool_calls:
                            tc_id = tc.get("id") or ""
                            fn = tc.get("function") or {}
                            name = fn.get("name") or "unknown"
                            args = _parse_tool_call_arguments(fn.get("arguments"))

                            # dispatch 前请求 tool_gate 决策。
                            # block → 跳过 dispatch / StreamSink，把 reason 作为 tool 结果回写。
                            # allow + rewritten_input → 用改写后的 args 替换原 args 再走流程。
                            decision = await _consult_tool_gate(req, name, args)
                            if decision.action == "block":
                                logger.warning(
                                    "tool_gate blocked tool call (tool=%s, reason=%s)",
                                    name, decision.reason,
                                )
                                record_error("backend", "tool_blocked")
                                tool_text = decision.reason or _TOOL_BLOCK_DEFAULT_REASON
                                tool_calls_collected.append(
                                    ToolCall(name=name, input=args, output=tool_text),
                                )
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc_id,
                                    "content": tool_text,
                                })
                                continue

                            if decision.rewritten_input is not None:
                                args = decision.rewritten_input

                            tool_calls_collected.append(ToolCall(name=name, input=args))

                            if name == "skill_loader":
                                skill_name = str(args.get("skill_name") or "").strip()
                                if skill_name:
                                    skills_called.append(skill_name)

                            # StreamSink: tool_use
                            if req.stream_sink is not None:
                                try:
                                    await req.stream_sink.on_tool_use(name, args)
                                except Exception:  # noqa: BLE001
                                    logger.warning(
                                        "stream_sink.on_tool_use failed (tool=%s)",
                                        name, exc_info=True,
                                    )

                            # 实际 dispatch
                            if dispatcher is None or name != "skill_loader":
                                tool_text = (
                                    f"工具 {name!r} 不可用（skill_dispatcher 未注入或工具未识别）。"
                                )
                            else:
                                try:
                                    tool_text = await dispatcher.dispatch(
                                        args.get("skill_name", ""),
                                        args.get("task_context", ""),
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    logger.exception("dispatcher.dispatch failed (skill=%s)", name)
                                    tool_text = f"Skill 执行失败：{exc}"

                            # StreamSink: tool_result
                            if req.stream_sink is not None:
                                try:
                                    await req.stream_sink.on_tool_result(name, tool_text)
                                except Exception:  # noqa: BLE001
                                    logger.warning(
                                        "stream_sink.on_tool_result failed (tool=%s)",
                                        name, exc_info=True,
                                    )

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": tool_text,
                            })
                        # 进入下一轮请求
                        continue

                    # 没有 tool_calls 或 finish_reason==stop —— 收尾。
                    record_llm_tool_iteration(
                        req.runtime.provider_id,
                        req.runtime.runtime_family,
                        req.role,
                        outcome="final",
                    )
                    final_text = msg.get("content") or ""
                    break
                else:
                    # 循环耗尽未 break，说明每轮都在 tool_calls 上递归。
                    raise ProviderMaxTurnsExceeded(
                        f"工具调用循环达到 max_turns={req.max_turns} 仍未收敛。"
                    )

        except ProviderMaxTurnsExceeded as exc:
            outcome = "max_turns_exceeded"
            self._record(req, outcome, started_at, agg_usage)
            raise
        except (
            ProviderAuthError, ProviderRateLimited,
            ProviderInvalidRequest, ProviderTransientError,
        ) as exc:
            outcome = self._outcome_for(exc)
            self._record(req, outcome, started_at, agg_usage)
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            outcome = "transient"
            self._record(req, outcome, started_at, agg_usage)
            raise ProviderTransientError(f"网络异常：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            outcome = "unknown_error"
            logger.exception("OpenAIChatBackend unexpected error")
            self._record(req, outcome, started_at, agg_usage)
            raise ProviderUnknownError(str(exc)) from exc

        self._record(req, outcome, started_at, agg_usage)

        return TurnResult(
            text=final_text,
            tool_calls=tool_calls_collected,
            skills_called=skills_called,
            usage=agg_usage,
            raw={},
        )

    # 异常归一化 / extra_body 防护 / metrics / _post_json 全部继承自
    # `_HttpChatBackendBase`，本类无需重写。
