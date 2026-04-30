"""OpenAI Chat Completions 多模态 content blocks（P4）。

OpenAI vision 用 `image_url` block，url 字段可以是 http(s) 或 data URL：

    {"type": "image_url",
     "image_url": {"url": "data:image/png;base64,..."}}

DashScope / OpenRouter 等大多数 OpenAI-compatible 兼容此 schema；个别 provider
对 base64 长度有限制，必要时再扩 `detail` 字段。
"""

from __future__ import annotations


def build_image_block(image_b64: str, mime_type: str) -> dict:
    """拼装单个 OpenAI vision image_url block（data URL 形态）。"""
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{image_b64}",
        },
    }


def build_user_content(
    text: str,
    image_b64: str | None = None,
    mime_type: str | None = None,
) -> str | list[dict]:
    """构造 user_content。

    - 仅文本：返回字符串（OpenAI 接受 messages[].content=str）。
    - 文本 + 图片：返回 `[{"type":"text",...}, {"type":"image_url",...}]`。
    """
    if not image_b64:
        return text
    if not mime_type:
        mime_type = "image/jpeg"
    return [
        {"type": "text", "text": text},
        build_image_block(image_b64, mime_type),
    ]
