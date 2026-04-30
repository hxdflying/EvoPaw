"""add_image_tool_local 单元测试。"""

from __future__ import annotations

import io

import pytest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

import evopaw.tools.add_image_tool_local as _m
from evopaw.tools.add_image_tool_local import (
    _compress_image,
    extract_image_path,
    load_image_data,
)


def _make_jpeg_bytes(width: int = 10, height: int = 10) -> bytes:
    img = Image.new("RGB", (width, height), color=(128, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_bytes(width: int = 10, height: int = 10) -> bytes:
    img = Image.new("RGB", (width, height))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestCompressImage:
    def test_invalid_bytes_returns_none(self):
        result = _compress_image(b"not-an-image")
        assert result is None

    def test_small_image_unchanged_dimensions(self):
        raw = _make_jpeg_bytes(100, 100)
        result = _compress_image(raw)
        assert result is not None
        img = Image.open(io.BytesIO(result))
        assert img.width == 100
        assert img.height == 100

    def test_large_image_shrunk(self):
        raw = _make_jpeg_bytes(5000, 3000)
        result = _compress_image(raw)
        assert result is not None
        img = Image.open(io.BytesIO(result))
        assert img.width <= 3840
        assert img.height <= 2160

    def test_returns_bytes(self):
        raw = _make_jpeg_bytes()
        result = _compress_image(raw)
        assert isinstance(result, bytes)


class TestLoadImageData:
    def test_valid_jpeg_returns_b64_and_mime(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        img_file = ws / "photo.jpg"
        img_file.write_bytes(_make_jpeg_bytes())
        result = load_image_data(str(img_file), workspace_root=ws)
        assert result is not None
        b64, mime = result
        assert mime == "image/jpeg"
        assert len(b64) > 0

    def test_valid_png_returns_correct_media_type(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        img_file = ws / "icon.png"
        img_file.write_bytes(_make_png_bytes())
        result = load_image_data(str(img_file), workspace_root=ws)
        assert result is not None
        assert result[1] == "image/png"

    def test_path_outside_workspace_returns_none(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "secret.jpg"
        outside.write_bytes(_make_jpeg_bytes())
        result = load_image_data(str(outside), workspace_root=ws)
        assert result is None

    def test_nonexistent_file_returns_none(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = load_image_data(str(ws / "missing.jpg"), workspace_root=ws)
        assert result is None

    def test_non_image_extension_returns_none(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        txt_file = ws / "data.txt"
        txt_file.write_text("not an image")
        result = load_image_data(str(txt_file), workspace_root=ws)
        assert result is None

    def test_file_too_large_returns_none(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        big = ws / "big.jpg"
        big.write_bytes(b"x" * (21 * 1024 * 1024))
        result = load_image_data(str(big), workspace_root=ws)
        assert result is None

    def test_webp_media_type(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        img_file = ws / "anim.webp"
        img_file.write_bytes(_make_jpeg_bytes())
        result = load_image_data(str(img_file), workspace_root=ws)
        assert result is not None
        assert result[1] == "image/webp"

    def test_gif_media_type(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        img_file = ws / "anim.gif"
        img_file.write_bytes(_make_jpeg_bytes())
        result = load_image_data(str(img_file), workspace_root=ws)
        assert result is not None
        assert result[1] == "image/gif"


class TestExtractImagePath:
    def test_extracts_image_path_from_runner_message(self):
        msg = (
            "用户发来了文件，已自动保存至沙盒路径：\n"
            "`/workspace/sessions/s-001/uploads/photo.jpg`\n"
            "请根据文件内容和用户意图完成相应处理。"
        )
        result = extract_image_path(msg)
        assert result == "/workspace/sessions/s-001/uploads/photo.jpg"

    def test_returns_none_for_non_image_file(self):
        msg = (
            "用户发来了文件，已自动保存至沙盒路径：\n"
            "`/workspace/sessions/s-001/uploads/report.pdf`\n"
        )
        result = extract_image_path(msg)
        assert result is None

    def test_returns_none_for_plain_text(self):
        result = extract_image_path("你好，请帮我分析这张图")
        assert result is None

    def test_extracts_png_path(self):
        msg = "`/workspace/sessions/s-002/uploads/screenshot.png`"
        result = extract_image_path(msg)
        assert result == "/workspace/sessions/s-002/uploads/screenshot.png"
