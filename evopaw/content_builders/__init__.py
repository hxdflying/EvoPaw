"""跨 provider 的多模态 user_content 构造（P4）。

每个 backend 对图片 / 文档的 content block 形态不同：

- **Claude Agent SDK / Anthropic Messages 直连**：原生
  `{"type":"image","source":{"type":"base64","media_type":..,"data":..}}`
  —— 同源协议、wire 格式完全一致，共用 `claude_blocks` 实现。
- **OpenAI Chat Completions 兼容**：
  `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`

`pick_content_builder(runtime_family)` 按 P1 的协议族返回对应模块（`build_user_content`
统一签名）。`main_agent.py` 装配 `user_content` 时调用之，避免在主流程内 hardcode
任何一种格式。

第一阶段只覆盖「文本 + 单图」组合（与现有 `extract_image_path → load_image_data`
唯一会触发的多模态形态一致）。后续如需支持文档（PDF）等再扩展。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

from evopaw.provider_runtime import RuntimeFamily

from . import claude_blocks, openai_blocks

__all__ = [
    "claude_blocks",
    "openai_blocks",
    "pick_content_builder",
]


def pick_content_builder(runtime_family: RuntimeFamily) -> "ModuleType":
    """按协议族返回 content_builder 模块。

    所有 builder 模块都暴露 `build_user_content(text, image_b64, mime_type)`
    统一签名。未知 family 抛 ValueError。
    """
    if runtime_family in ("claude_sdk_compat", "anthropic_messages"):
        return claude_blocks
    if runtime_family == "openai_chat":
        return openai_blocks
    raise ValueError(f"未知 runtime_family={runtime_family!r}，无对应 content_builder")
