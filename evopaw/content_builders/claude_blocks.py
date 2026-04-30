"""Claude Agent SDK 多模态 content blocks（P4）。

Claude SDK 接受的图片格式：

    {"type": "image",
     "source": {"type": "base64", "media_type": "image/png", "data": "..."}}

配合 `evopaw/tools/add_image_tool_local.py:load_image_data`（返回 `(b64, mime)`）使用：
后者负责「读文件 → 路径校验 → bytes → base64」，本模块只负责把已有的
base64 + mime_type 拼成 block，避免 main_agent.py 的 image bytes 重新读盘。
"""

from __future__ import annotations


def build_image_block(image_b64: str, mime_type: str) -> dict:
    """拼装单个 image block（Claude / Anthropic 共用形态）。"""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime_type,
            "data": image_b64,
        },
    }


def build_user_content(
    text: str,
    image_b64: str | None = None,
    mime_type: str | None = None,
) -> str | list[dict]:
    """构造 user_content。

    - 仅文本：返回字符串（Claude SDK 接受 prompt=str）。
    - 文本 + 图片：返回 `[{"type":"text",...}, {"type":"image",...}]`。
    """
    if not image_b64:
        return text
    if not mime_type:
        mime_type = "image/jpeg"
    return [
        {"type": "text", "text": text},
        build_image_block(image_b64, mime_type),
    ]
