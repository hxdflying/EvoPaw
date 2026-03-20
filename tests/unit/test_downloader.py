"""FeishuDownloader 单元测试"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from xiaopaw.feishu.downloader import FeishuDownloader
from xiaopaw.models import Attachment


# ── Helpers ────────────────────────────────────────────────────


def _make_attachment(
    msg_type: str = "image",
    file_key: str = "img_001",
    file_name: str = "img_001.jpg",
) -> Attachment:
    return Attachment(msg_type=msg_type, file_key=file_key, file_name=file_name)


def _make_client(success: bool = True, data: bytes = b"binary-data") -> MagicMock:
    """构造 mock lark-oapi Client，模拟 im.v1.message_resource.aget"""
    resp = MagicMock()
    resp.success.return_value = success
    resp.file.read.return_value = data
    resp.code = 0
    resp.msg = "ok"

    client = MagicMock()
    client.im.v1.message_resource.aget = AsyncMock(return_value=resp)
    return client


def _make_fail_client(code: int = 40300, msg: str = "permission denied") -> MagicMock:
    """构造 API 返回失败的 mock Client"""
    resp = MagicMock()
    resp.success.return_value = False
    resp.code = code
    resp.msg = msg

    client = MagicMock()
    client.im.v1.message_resource.aget = AsyncMock(return_value=resp)
    return client


# ── Tests ──────────────────────────────────────────────────────


class TestFeishuDownloaderDownload:
    async def test_image_download_success_returns_path(self, tmp_path):
        """图片下载成功时返回本地路径"""
        client = _make_client(data=b"img-bytes")
        dl = FeishuDownloader(client=client, data_dir=tmp_path)
        att = _make_attachment(msg_type="image", file_key="img_001", file_name="img_001.jpg")

        result = await dl.download("msg_001", att, "s-abc")

        assert result is not None
        assert result.name == "img_001.jpg"
        assert result.read_bytes() == b"img-bytes"

    async def test_file_download_success_returns_path(self, tmp_path):
        """文件下载成功时返回本地路径"""
        client = _make_client(data=b"pdf-bytes")
        dl = FeishuDownloader(client=client, data_dir=tmp_path)
        att = _make_attachment(msg_type="file", file_key="file_001", file_name="report.pdf")

        result = await dl.download("msg_002", att, "s-def")

        assert result is not None
        assert result.name == "report.pdf"
        assert result.read_bytes() == b"pdf-bytes"

    async def test_api_failure_returns_none(self, tmp_path):
        """API 返回失败（success() = False）时返回 None"""
        client = _make_fail_client(code=40300)
        dl = FeishuDownloader(client=client, data_dir=tmp_path)
        att = _make_attachment()

        result = await dl.download("msg_001", att, "s-abc")

        assert result is None

    async def test_exception_returns_none(self, tmp_path):
        """下载过程中抛出异常时返回 None"""
        client = MagicMock()
        client.im.v1.message_resource.aget = AsyncMock(
            side_effect=RuntimeError("network error")
        )
        dl = FeishuDownloader(client=client, data_dir=tmp_path)
        att = _make_attachment()

        result = await dl.download("msg_001", att, "s-abc")

        assert result is None

    async def test_dest_dir_created_automatically(self, tmp_path):
        """下载成功时自动创建目标目录"""
        client = _make_client()
        dl = FeishuDownloader(client=client, data_dir=tmp_path)
        att = _make_attachment(file_name="test.jpg")

        await dl.download("msg_001", att, "s-xyz")

        dest_dir = tmp_path / "workspace" / "sessions" / "s-xyz" / "uploads"
        assert dest_dir.is_dir()

    async def test_dest_path_under_session_uploads(self, tmp_path):
        """文件保存路径位于 {data_dir}/workspace/sessions/{sid}/uploads/{filename}"""
        client = _make_client(data=b"content")
        dl = FeishuDownloader(client=client, data_dir=tmp_path)
        att = _make_attachment(msg_type="file", file_key="fk_123", file_name="doc.txt")

        result = await dl.download("msg_abc", att, "s-test")

        expected = (
            tmp_path / "workspace" / "sessions" / "s-test" / "uploads" / "doc.txt"
        )
        assert result == expected

    async def test_aget_called_once(self, tmp_path):
        """download() 只调用一次 aget"""
        client = _make_client()
        dl = FeishuDownloader(client=client, data_dir=tmp_path)
        att = _make_attachment()

        await dl.download("msg_001", att, "s-abc")

        client.im.v1.message_resource.aget.assert_called_once()

    async def test_api_failure_does_not_write_file(self, tmp_path):
        """API 失败时不应写入任何文件"""
        client = _make_fail_client()
        dl = FeishuDownloader(client=client, data_dir=tmp_path)
        att = _make_attachment(file_name="img.jpg")

        await dl.download("msg_001", att, "s-abc")

        dest_dir = tmp_path / "workspace" / "sessions" / "s-abc" / "uploads"
        # 目录可能被创建，但文件不应存在
        assert not (dest_dir / "img.jpg").exists()
