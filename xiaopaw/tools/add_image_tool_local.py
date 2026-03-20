"""AddImageToolLocal — 本地图片加载工具，配合 AliyunLLM 的多模态能力使用。

功能：
- 从本地路径读取图片（或直接透传 http(s) URL）
- 可选压缩
- 转为 Base64 Data URL，格式兼容 CrewAI 的 AddImageTool
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from PIL import Image

logger = logging.getLogger(__name__)

# 允许读取的根目录（防止路径遍历）
_WORKSPACE_ROOT = (Path(__file__).parent.parent.parent / "data" / "workspace").resolve()
# 单张图片最大读取大小
_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB


class AddImageToolLocalSchema(BaseModel):
    """与 CrewAI AddImageTool 的 schema 保持一致。"""

    image_url: str = Field(
        ...,
        description="The URL or path of the image to add",
    )


def _compress_image(raw: bytes) -> bytes | None:
    """
    压缩图片分辨率在 4K(3840x2160) 以下，并保持图片原始比例。
    暂时未启用，后续可根据需要打开。
    """
    try:
        image = Image.open(io.BytesIO(raw))
        if image.width > 3840 or image.height > 2160:
            image.thumbnail((3840, 2160))
        out = io.BytesIO()
        image.save(out, format=image.format or "JPEG")
        return out.getvalue()
    except Exception as e:  # pragma: no cover - 调试辅助
        logger.debug("AddImageToolLocal: compress error=%s", e)
        return None


def _local_path_to_base64_data_and_compress_url(image_url: str) -> str | None:
    """
    若 image_url 为本地文件路径，则读取并转为 data URL；否则返回错误信息。
    路径必须在允许的工作区根目录内（防止路径遍历）。
    """
    logger.debug("AddImageToolLocal: image_url=%s", image_url)
    path = Path(image_url).expanduser().resolve()
    logger.debug("AddImageToolLocal: resolved path=%s", path)

    # 路径遍历保护
    if not str(path).startswith(str(_WORKSPACE_ROOT)):
        logger.warning("AddImageToolLocal: path traversal blocked: %s", path)
        return f"错误：路径不在允许的工作区范围内: {image_url}"

    if not path.is_file():
        logger.debug("AddImageToolLocal: path is not a file: %s", path)
        return f"图片文件不存在: {image_url}"

    # 文件大小检查
    file_size = path.stat().st_size
    if file_size > _MAX_IMAGE_BYTES:
        return f"错误：图片文件过大（{file_size} bytes），最大支持 {_MAX_IMAGE_BYTES} bytes"

    raw = path.read_bytes()
    logger.debug("AddImageToolLocal: raw size=%d bytes", len(raw))
    # 如需压缩图片，可取消下一行注释：
    # raw = _compress_image(raw) or raw
    b64 = base64.b64encode(raw).decode("utf-8")
    logger.debug("AddImageToolLocal: b64 size=%d chars", len(b64))
    suffix = path.suffix.lower()
    mime = "image/jpeg"
    if suffix == ".png":
        mime = "image/png"
    elif suffix == ".gif":
        mime = "image/gif"
    elif suffix == ".webp":
        mime = "image/webp"
    elif suffix == ".bmp":
        mime = "image/bmp"
    return f"data:{mime};base64,{b64}"


class AddImageToolLocal(BaseTool):
    """将本地图片加入上下文的工具：读取本地文件并转为 Base64 后返回，格式与 AddImageTool 一致。"""

    name: str = "Add image to content Local"
    description: str = (
        "Load a local image file from the given path, compress it if necessary, "
        "and convert it to a base64 data URL format that can be processed by multimodal models. "
        "The tool only handles file loading and encoding - image analysis happens after the image is loaded."
    )
    args_schema: type[BaseModel] = AddImageToolLocalSchema

    def _run(
        self,
        image_url: str,
        **kwargs: Any,
    ) -> str:
        url = image_url.strip()

        # 已是 http(s) URL 则直接返回，让上游工具/模型自行处理
        if url.startswith("http://") or url.startswith("https://"):
            final_url = url
        else:
            data_url = _local_path_to_base64_data_and_compress_url(url)
            final_url = data_url if data_url is not None else url

        return final_url

