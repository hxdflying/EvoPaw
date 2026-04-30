"""HTTP-based chat backend 公共骨架。

`OpenAIChatBackend`（chat/completions）与 `AnthropicMessagesBackend`（/v1/messages）
都是 httpx 直连协议族。两边的「异常归一化 / extra_body 防护 / metrics 上报 /
单次 POST 错误映射」逻辑完全一致，被本基类抽出来共用。

子类实现差异部分：
- 端点 URL（chat/completions vs v1/messages）
- 请求头（Authorization Bearer vs x-api-key + anthropic-version）
- 请求体构造、响应解析、工具调用循环（assistant 消息回写形态、tool result 包装方式）
- `runtime_family`（ClassVar，子类必填）
- `_generic_body_fields`（ClassVar frozenset，子类按各自协议决定）

注意：`run_turn` 仍由子类实现——两族的多轮工具调用循环数据形态差异太大，
强行套同一个模板反而会出现"全是 if family==..."的反模式。
"""

from __future__ import annotations

import logging
import time
from typing import ClassVar

import httpx

from evopaw.observability.metrics import record_llm_call

from .base import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderMaxTurnsExceeded,
    ProviderRateLimited,
    ProviderTransientError,
    ProviderUnknownError,
    TurnRequest,
    Usage,
)

logger = logging.getLogger(__name__)


class _HttpChatBackendBase:
    """所有基于 httpx 的 chat backend 公共父类。

    子类必填 ClassVar：
      - `runtime_family`：与 `ResolvedRuntime.runtime_family` 对齐
      - `_generic_body_fields`：通用请求体白名单，用于 `_extract_extra_body` 防护
    """

    runtime_family: ClassVar[str]
    _generic_body_fields: ClassVar[frozenset[str]]

    # ────────────────────────────────────────────────────────────
    # 异常归一化（HTTP 状态码 / 已知 Provider*Error → outcome label）
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _classify_http_error(exc: httpx.HTTPStatusError) -> Exception:
        """4xx/5xx → 对应 Provider*Error。返回新异常实例（调用方自己 raise）。"""
        code = exc.response.status_code
        msg = f"HTTP {code}: {exc.response.text[:300]}"
        if code in (401, 403):
            return ProviderAuthError(msg)
        if code == 429:
            return ProviderRateLimited(msg)
        if 400 <= code < 500:
            return ProviderInvalidRequest(msg)
        if 500 <= code < 600:
            return ProviderTransientError(msg)
        return ProviderUnknownError(msg)

    @staticmethod
    def _outcome_for(exc: Exception) -> str:
        """Provider*Error → metrics label。未识别异常归到 `unknown_error`。"""
        if isinstance(exc, ProviderAuthError):
            return "auth_error"
        if isinstance(exc, ProviderRateLimited):
            return "rate_limited"
        if isinstance(exc, ProviderTransientError):
            return "transient"
        if isinstance(exc, ProviderInvalidRequest):
            return "invalid_request"
        if isinstance(exc, ProviderMaxTurnsExceeded):
            return "max_turns_exceeded"
        return "unknown_error"

    # ────────────────────────────────────────────────────────────
    # extra_body 白名单防护（hermes #8591 教训）
    # ────────────────────────────────────────────────────────────

    @classmethod
    def _extract_extra_body(cls, req: TurnRequest) -> dict:
        """从 runtime.extra_body 取已通过 ProviderSpec 白名单的字段；防御性过滤通用字段。

        P1 已经在 resolve 阶段做过白名单过滤；本层仅做防御性兜底——去除任何与
        `_generic_body_fields` 冲突的键，避免覆盖出站主体字段。
        """
        raw = getattr(req.runtime, "extra_body", None) or {}
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if k not in cls._generic_body_fields}

    # ────────────────────────────────────────────────────────────
    # metrics 打点
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _record(
        req: TurnRequest,
        outcome: str,
        started_at: float,
        usage: Usage,
    ) -> None:
        """单次 record_llm_call 上报；失败仅记 warning，不抛。"""
        try:
            record_llm_call(
                provider_id=req.runtime.provider_id,
                runtime_family=req.runtime.runtime_family,
                role=req.role,
                outcome=outcome,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                latency_seconds=time.monotonic() - started_at,
            )
        except Exception:  # noqa: BLE001
            logger.warning("record_llm_call failed", exc_info=True)

    # ────────────────────────────────────────────────────────────
    # 单次 POST：post + raise_for_status + json，错误映射成 Provider*Error
    # ────────────────────────────────────────────────────────────

    @classmethod
    async def _post_json(
        cls,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        body: dict,
    ) -> dict:
        """单次 HTTP POST + JSON 解析；HTTP 错误映射为 Provider*Error。

        网络错误（ConnectError / TimeoutException）保持原样抛出，由调用方上层
        try/except 转成 ProviderTransientError。
        """
        try:
            resp = await client.post(url, headers=headers, json=body)
        except (httpx.ConnectError, httpx.TimeoutException):
            raise
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise cls._classify_http_error(exc) from exc
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnknownError(f"响应非 JSON：{exc}") from exc
