"""DashScope（OpenAI 兼容端）客户端构造器（P1-4 / N-2）。

memory 层有两个角色都连同一类端点：
- `memory_summary`（context_mgmt.py）走 chat/completions
- `memory_extract` / `memory_embedding`（indexer.py）走 chat/completions + embeddings

它们以前各自重复一段「if resolved else fallback 到 DashScope hardcoded base_url」逻辑。
本模块把该逻辑收口为单一 `make_openai_client()`，三个角色都调用同一个工厂。

模块级 lazy singleton（`_resolved_*` / `_*_client` 等）仍保留在各自的 context_mgmt /
indexer 中——单测 patch 这些符号已大量存在（参见 tests/unit/test_indexer.py），
此处只做客户端构造层的 dedupe，不动缓存层。
"""

from __future__ import annotations

import os

from evopaw.provider_runtime import ResolvedRuntime

_DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def make_openai_client(
    resolved: ResolvedRuntime | None,
    *,
    fallback_api_key_env: str = "QWEN_API_KEY",
):
    """构造同步 OpenAI client。

    Args:
        resolved: ResolvedRuntime（来自 resolve_runtime("memory_*")）。非 None 时使用
                  其 api_base / api_key；为空字段 fallback 到 DashScope 默认值。
        fallback_api_key_env: resolved 为 None 时从该环境变量取 api_key。

    Returns:
        openai.OpenAI 实例。
    """
    from openai import OpenAI  # noqa: PLC0415

    if resolved is not None:
        return OpenAI(
            api_key=resolved.api_key or "",
            base_url=resolved.api_base or _DASHSCOPE_DEFAULT_BASE_URL,
        )
    return OpenAI(
        api_key=os.getenv(fallback_api_key_env, ""),
        base_url=_DASHSCOPE_DEFAULT_BASE_URL,
    )


def resolved_extra_body(resolved: ResolvedRuntime | None) -> dict:
    """从 resolved 取 extra_body；resolved 为 None 时给 DashScope 默认值。

    DashScope OpenAI 兼容端要求 `enable_thinking=False` 否则触发 thinking 流式输出，
    会导致 chat.completions.create() 收到 None content 段，下游 strip() 报错。
    """
    if resolved is not None:
        return dict(resolved.extra_body)
    return {"enable_thinking": False}
