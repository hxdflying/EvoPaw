"""AnthropicMessagesBackend —— P4 落地：直连 Anthropic Messages API。

和 P3 的 `OpenAIChatBackend` 是一对镜像实现，差异点（来自计划文档 §5 P4 / 风险表）：

1. **HTTP 协议**：
   - 端点：`{api_base}/v1/messages`
   - 头：`x-api-key: {key}` + `anthropic-version: 2023-06-01`（不是 `Authorization: Bearer`）
   - 请求体：`model / max_tokens(必填) / system(顶级) / messages / tools / tool_choice`

2. **工具 schema**：使用 `input_schema` 而不是 OpenAI 的 `parameters`；
   单工具形态（skill_loader），由 `skills_runtime.adapters.anthropic_tools` 产出。

3. **工具调用循环**：
   - 响应 `stop_reason == "tool_use"` 时遍历 assistant `content` 列表，取出
     所有 `{"type":"tool_use","id","name","input"}` block。
   - 把 assistant 整段 content（含 text 与 tool_use blocks）回写到 messages，
     再把每个 tool_result 作为 user 消息的 `content` 项追加：
       `{"role":"user","content":[{"type":"tool_result","tool_use_id":..,"content":..}]}`

4. **usage**：`response.usage.input_tokens / output_tokens`，多轮累加。

5. **异常归一化**：与 OpenAIChatBackend 完全一致（401/403 Auth；429 RateLimited；
   4xx Invalid；5xx / Connect / Timeout Transient；其它 Unknown）。

6. **StreamSink**：tool_use 调用前后触发 `on_tool_use / on_tool_result`，
   文案与 ClaudeSDKCompatBackend / OpenAIChatBackend 字节级一致。

7. **extra_body 白名单**：与 OpenAI 路径同样的防御性过滤——既允许 provider-specific
   字段（白名单已在 P1 ProviderSpec 校验），也防止 `model / messages / system / tools`
   等通用字段被覆盖。通用 generation 参数（`max_tokens` / `temperature` / `top_p`）
   走 `TurnRequest` 的 first-class 字段（P2-1），不再通过 `extra_body` 注入；
   `max_tokens` 在 `TurnRequest.max_tokens=None` 时回退到 `_DEFAULT_MAX_TOKENS=4096`。

模块级 import `httpx`，便于单测 patch `evopaw.agent_backends.anthropic_messages.httpx.AsyncClient`。
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
    """调用 req.tool_gate.before_tool_use，返回归一化 ToolDecision。

    - gate 缺省 → allow
    - 异常 / 协议违反 → allow（吞掉，仅记 warning），保护主流程不被 gate bug 卡死
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
    if not isinstance(decision, ToolDecision):
        logger.warning(
            "tool_gate.before_tool_use 返回非 ToolDecision (tool=%s, type=%s)，按 allow 兜底",
            name, type(decision).__name__,
        )
        return ToolDecision(action="allow")
    return decision

logger = logging.getLogger(__name__)


# Anthropic 出站请求体的「通用」字段：仅这些字段允许由 backend 直接构造。
# provider-specific 字段（如 `metadata` 等）必须经过 ProviderSpec.extra_body_whitelist
# 显式声明，由 runtime.extra_body 注入。
_GENERIC_BODY_FIELDS = frozenset({
    "model", "messages", "system", "tools", "tool_choice",
    "max_tokens", "temperature", "top_p", "top_k", "stream",
    "stop_sequences",
})

# Anthropic API 默认 max_tokens（必填字段）；可被 runtime.extra_body 覆盖（前提是
# `max_tokens` 在 ProviderSpec.extra_body_whitelist 中显式列出，否则会被防御性过滤）。
_DEFAULT_MAX_TOKENS = 4096

_ANTHROPIC_VERSION = "2023-06-01"


def _normalize_user_content_to_blocks(user_content: str | list[dict]) -> list[dict]:
    """把字符串 / dict 列表统一为 Anthropic content blocks 列表。

    Anthropic Messages API 的 user 消息 content 接受：
      - 字符串（单一文本）
      - blocks 列表（多模态）
    backend 内部统一用 list 形态，便于后续追加 tool_result blocks。
    """
    if isinstance(user_content, str):
        return [{"type": "text", "text": user_content}]
    return list(user_content)


def _build_initial_messages(req: TurnRequest) -> list[dict]:
    """初始 messages：仅 user 一条。system 走顶级字段。"""
    return [{"role": "user", "content": _normalize_user_content_to_blocks(req.user_content)}]


def _build_request_body(
    req: TurnRequest,
    messages: list[dict],
    tools: list[dict] | None,
) -> dict:
    # P2-1：max_tokens Anthropic API 必填，TurnRequest 未指定时回退默认。
    body: dict[str, Any] = {
        "model": req.runtime.model,
        "max_tokens": req.max_tokens if req.max_tokens is not None else _DEFAULT_MAX_TOKENS,
        "system": req.system_prompt,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = {"type": "auto"}

    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.top_p is not None:
        body["top_p"] = req.top_p

    extra = AnthropicMessagesBackend._extract_extra_body(req)
    if extra:
        body.update(extra)
    return body


def _build_headers(req: TurnRequest) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": _ANTHROPIC_VERSION,
    }
    if req.runtime.api_key:
        headers["x-api-key"] = req.runtime.api_key
    return headers


def _parse_usage(payload: dict) -> Usage:
    raw = payload.get("usage") or {}
    if not isinstance(raw, dict):
        return Usage()
    in_tok = int(raw.get("input_tokens") or 0)
    out_tok = int(raw.get("output_tokens") or 0)
    return Usage(
        prompt_tokens=in_tok,
        completion_tokens=out_tok,
        total_tokens=in_tok + out_tok,
    )


def _extract_text_from_blocks(blocks: list[dict] | None) -> str:
    """从 assistant content blocks 中拼出最终文本（仅 type=text 的 block）。"""
    if not blocks:
        return ""
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            text = b.get("text") or ""
            if text:
                parts.append(text)
    return "".join(parts)


def _normalize_tool_input(raw: Any) -> dict:
    """Anthropic tool_use.input 一般已是 dict；偶尔是 JSON 字符串，统一为 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    return {}


class AnthropicMessagesBackend(_HttpChatBackendBase):
    """直连 Anthropic Messages API 的 backend。

    一次 `run_turn` 内完成 [LLM 请求 → 工具调用 → 再请求]^N 的循环；
    每轮请求 token / 延迟通过单次 record_llm_call 上报（聚合到一次）。

    异常归一化 / extra_body 白名单防护 / metrics 打点 / 单次 POST 错误映射，
    都复用 `_HttpChatBackendBase`；本类只实现 /v1/messages 形态的差异部分。
    """

    runtime_family: ClassVar[str] = "anthropic_messages"
    _generic_body_fields: ClassVar[frozenset[str]] = _GENERIC_BODY_FIELDS

    async def run_turn(self, req: TurnRequest) -> TurnResult:  # noqa: PLR0912, PLR0915
        api_base = (req.runtime.api_base or "").rstrip("/")
        if not api_base:
            raise ProviderInvalidRequest(
                f"runtime.api_base 缺失（provider_id={req.runtime.provider_id}）"
            )
        url = f"{api_base}/v1/messages"

        dispatcher = req.backend_hints.get("skill_dispatcher")
        tools_schema: list[dict] | None = None
        if dispatcher is not None:
            from evopaw.skills_runtime.adapters.anthropic_tools import (  # noqa: PLC0415
                build_anthropic_tool_schema,
            )

            tools_schema = [build_anthropic_tool_schema(dispatcher)]

        messages = _build_initial_messages(req)
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

                    turn_usage = _parse_usage(payload)
                    agg_usage = Usage(
                        prompt_tokens=agg_usage.prompt_tokens + turn_usage.prompt_tokens,
                        completion_tokens=agg_usage.completion_tokens + turn_usage.completion_tokens,
                        total_tokens=agg_usage.total_tokens + turn_usage.total_tokens,
                    )

                    blocks = payload.get("content") or []
                    stop_reason = payload.get("stop_reason")

                    tool_use_blocks = [
                        b for b in blocks
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    ]

                    if tool_use_blocks and stop_reason in ("tool_use", None):
                        # P1-4：本轮命中 tool_use，记 continue。
                        record_llm_tool_iteration(
                            req.runtime.provider_id,
                            req.runtime.runtime_family,
                            req.role,
                            outcome="continue",
                        )
                        # 把 assistant 整段 content（含 text 与 tool_use）回写
                        messages.append({"role": "assistant", "content": blocks})

                        tool_result_blocks: list[dict] = []
                        for tu in tool_use_blocks:
                            tu_id = tu.get("id") or ""
                            name = tu.get("name") or "unknown"
                            args = _normalize_tool_input(tu.get("input"))

                            # P2-2：dispatch 前请求 tool_gate 决策。
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
                                tool_result_blocks.append({
                                    "type": "tool_result",
                                    "tool_use_id": tu_id,
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

                            if req.stream_sink is not None:
                                try:
                                    await req.stream_sink.on_tool_use(name, args)
                                except Exception:  # noqa: BLE001
                                    logger.warning(
                                        "stream_sink.on_tool_use failed (tool=%s)",
                                        name, exc_info=True,
                                    )

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

                            if req.stream_sink is not None:
                                try:
                                    await req.stream_sink.on_tool_result(name, tool_text)
                                except Exception:  # noqa: BLE001
                                    logger.warning(
                                        "stream_sink.on_tool_result failed (tool=%s)",
                                        name, exc_info=True,
                                    )

                            tool_result_blocks.append({
                                "type": "tool_result",
                                "tool_use_id": tu_id,
                                "content": tool_text,
                            })

                        # 单条 user 消息打包所有 tool_result blocks
                        messages.append({"role": "user", "content": tool_result_blocks})
                        continue

                    # stop_reason == 'end_turn' / 'stop_sequence' / 'max_tokens' —— 收尾
                    # P1-4：本轮没有 tool_use，记 final。
                    record_llm_tool_iteration(
                        req.runtime.provider_id,
                        req.runtime.runtime_family,
                        req.role,
                        outcome="final",
                    )
                    final_text = _extract_text_from_blocks(blocks)
                    break
                else:
                    # P2-5：循环耗尽未 break，说明每轮都在 tool_use 上递归。
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
            logger.exception("AnthropicMessagesBackend unexpected error")
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
