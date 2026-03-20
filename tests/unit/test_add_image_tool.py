"""AddImageToolLocal 单元测试"""

from __future__ import annotations

import io

import pytest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

import xiaopaw.tools.add_image_tool_local as _m
from xiaopaw.tools.add_image_tool_local import (
    AddImageToolLocal,
    _compress_image,
    _local_path_to_base64_data_and_compress_url,
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


class TestLocalPathToBase64:
    def test_path_outside_workspace_blocked(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_bytes(b"secret data")
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            result = _local_path_to_base64_data_and_compress_url(str(outside))
        assert "错误" in result
        assert "路径" in result

    def test_nonexistent_file_returns_error(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        nonexistent = fake_root / "missing.jpg"
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            result = _local_path_to_base64_data_and_compress_url(str(nonexistent))
        assert "不存在" in result

    def test_file_too_large_blocked(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        big = fake_root / "big.jpg"
        big.write_bytes(b"x" * (21 * 1024 * 1024))  # 21 MB > 20 MB limit
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            result = _local_path_to_base64_data_and_compress_url(str(big))
        assert "过大" in result

    def test_valid_jpeg_returns_data_url(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        img_file = fake_root / "photo.jpg"
        img_file.write_bytes(_make_jpeg_bytes())
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            result = _local_path_to_base64_data_and_compress_url(str(img_file))
        assert result is not None
        assert result.startswith("data:image/jpeg;base64,")

    def test_valid_png_returns_data_url(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        img_file = fake_root / "icon.png"
        img_file.write_bytes(_make_png_bytes())
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            result = _local_path_to_base64_data_and_compress_url(str(img_file))
        assert result is not None
        assert result.startswith("data:image/png;base64,")

    def test_webp_mime_type(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        # Use .webp extension even though content is JPEG (mime based on extension)
        img_file = fake_root / "anim.webp"
        img_file.write_bytes(_make_jpeg_bytes())
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            result = _local_path_to_base64_data_and_compress_url(str(img_file))
        assert "image/webp" in result

    def test_gif_mime_type(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        img_file = fake_root / "anim.gif"
        img_file.write_bytes(_make_jpeg_bytes())
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            result = _local_path_to_base64_data_and_compress_url(str(img_file))
        assert "image/gif" in result

    def test_bmp_mime_type(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        img_file = fake_root / "bitmap.bmp"
        img_file.write_bytes(_make_jpeg_bytes())
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            result = _local_path_to_base64_data_and_compress_url(str(img_file))
        assert "image/bmp" in result


class TestAddImageToolLocal:
    def test_http_url_passthrough(self):
        tool = AddImageToolLocal()
        result = tool._run("http://example.com/image.jpg")
        assert result == "http://example.com/image.jpg"

    def test_https_url_passthrough(self):
        tool = AddImageToolLocal()
        result = tool._run("https://cdn.example.com/img.png")
        assert result == "https://cdn.example.com/img.png"

    def test_local_path_blocked_outside_workspace(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        outside = tmp_path / "bad.jpg"
        outside.write_bytes(b"data")
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            tool = AddImageToolLocal()
            result = tool._run(str(outside))
        assert "错误" in result

    def test_local_path_within_workspace_returns_data_url(self, tmp_path):
        fake_root = tmp_path / "workspace"
        fake_root.mkdir()
        img_file = fake_root / "photo.jpg"
        img_file.write_bytes(_make_jpeg_bytes())
        with patch.object(_m, "_WORKSPACE_ROOT", fake_root.resolve()):
            tool = AddImageToolLocal()
            result = tool._run(str(img_file))
        assert result.startswith("data:image/jpeg;base64,")

    def test_tool_name(self):
        tool = AddImageToolLocal()
        assert tool.name == "Add image to content Local"
