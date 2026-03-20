"""FeishuDownloader — 下载飞书消息附件（图片/文件）到 session workspace."""

from __future__ import annotations

import logging
from pathlib import Path

from lark_oapi.client import Client
from lark_oapi.api.im.v1 import GetMessageResourceRequest

from xiaopaw.models import Attachment

logger = logging.getLogger(__name__)


class FeishuDownloader:
    """从飞书 API 下载消息附件，写入 data/workspace/sessions/{sid}/uploads/。

    沙盒内可见路径为 /workspace/sessions/{sid}/uploads/{filename}。
    """

    def __init__(self, client: Client, data_dir: Path) -> None:
        self._client = client
        self._data_dir = data_dir

    async def download(
        self,
        msg_id: str,
        attachment: Attachment,
        session_id: str,
    ) -> Path | None:
        """下载单个附件到本地。

        Returns:
            下载成功时返回本地绝对路径，失败返回 None。
        """
        dest_dir = (
            self._data_dir / "workspace" / "sessions" / session_id / "uploads"
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / attachment.file_name

        try:
            req = (
                GetMessageResourceRequest.builder()
                .message_id(msg_id)
                .file_key(attachment.file_key)
                .type(attachment.msg_type)
                .build()
            )
            resp = await self._client.im.v1.message_resource.aget(req)
            if not resp.success():
                logger.error(
                    "附件下载失败 msg_id=%s file_key=%s code=%s msg=%s",
                    msg_id,
                    attachment.file_key,
                    resp.code,
                    resp.msg,
                )
                return None

            dest_path.write_bytes(resp.file.read())
            logger.info(
                "附件下载完成 file_key=%s -> %s (%d bytes)",
                attachment.file_key,
                dest_path,
                dest_path.stat().st_size,
            )
            return dest_path

        except Exception:
            logger.exception(
                "下载附件异常 msg_id=%s file_key=%s", msg_id, attachment.file_key
            )
            return None
