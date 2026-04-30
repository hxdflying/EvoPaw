"""content_builders 单元测试（P4）。

覆盖：
- claude_blocks（claude_sdk_compat / anthropic_messages 共用同源协议）
- openai_blocks（vision data URL）
- 纯文本路径（无图）→ 返回字符串
- 文本 + 图片路径 → 返回 list[dict]，schema 与各家协议对齐
- mime_type 缺省 fallback
- pick_content_builder 工厂的 family→module 映射
"""

from __future__ import annotations

import pytest

from evopaw.content_builders import (
    claude_blocks,
    openai_blocks,
    pick_content_builder,
)


class TestClaudeBlocks:
    def test_text_only_returns_string(self):
        assert claude_blocks.build_user_content("hello") == "hello"

    def test_with_image_returns_blocks_list(self):
        out = claude_blocks.build_user_content(
            "看图说话", image_b64="QUJD", mime_type="image/png",
        )
        assert isinstance(out, list)
        assert out[0] == {"type": "text", "text": "看图说话"}
        assert out[1] == {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "QUJD",
            },
        }

    def test_default_mime_when_missing(self):
        out = claude_blocks.build_user_content("x", image_b64="YQ==", mime_type=None)
        assert out[1]["source"]["media_type"] == "image/jpeg"

    def test_empty_image_b64_returns_text(self):
        assert claude_blocks.build_user_content("x", image_b64="") == "x"

    def test_build_image_block_shape(self):
        b = claude_blocks.build_image_block("DAT", "image/jpeg")
        assert b["type"] == "image"
        assert b["source"]["data"] == "DAT"
        assert b["source"]["media_type"] == "image/jpeg"


class TestOpenAIBlocks:
    def test_text_only_returns_string(self):
        assert openai_blocks.build_user_content("hello") == "hello"

    def test_with_image_returns_image_url_block(self):
        out = openai_blocks.build_user_content(
            "看图", image_b64="QUJD", mime_type="image/png",
        )
        assert isinstance(out, list)
        assert out[0] == {"type": "text", "text": "看图"}
        assert out[1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,QUJD"},
        }

    def test_default_mime_when_missing(self):
        out = openai_blocks.build_user_content("x", image_b64="YQ==", mime_type=None)
        assert out[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_empty_image_b64_returns_text(self):
        assert openai_blocks.build_user_content("x", image_b64=None) == "x"

    def test_build_image_block_shape(self):
        b = openai_blocks.build_image_block("ZA==", "image/jpeg")
        assert b["type"] == "image_url"
        assert b["image_url"]["url"] == "data:image/jpeg;base64,ZA=="


class TestPickContentBuilder:
    def test_claude_sdk_compat(self):
        assert pick_content_builder("claude_sdk_compat") is claude_blocks

    def test_openai_chat(self):
        assert pick_content_builder("openai_chat") is openai_blocks

    def test_anthropic_messages_shares_claude_blocks(self):
        assert pick_content_builder("anthropic_messages") is claude_blocks

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            pick_content_builder("bedrock_converse")  # type: ignore[arg-type]
