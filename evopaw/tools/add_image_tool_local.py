"""图片加载工具 — 将本地图片转为 Claude 原生 image content block

Claude SDK 原生支持 base64 image block，无需 data URL 格式。
"""

from __future__ import annotations

import base64
import io
import logging
import re
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# 允许读取的根目录（防止路径遍历）
_WORKSPACE_ROOT = (Path(__file__).parent.parent.parent / "data" / "workspace").resolve()
# 单张图片最大读取大小
_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB

# 支持的图片扩展名
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})

# 扩展名到 MIME 映射
_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# 匹配 Runner 构建的附件路径
_ATTACHMENT_PATH_RE = re.compile(
    r"`(/workspace/sessions/[^/]+/uploads/[^`]+)`"
)


def _compress_image(raw: bytes) -> bytes | None:
    """压缩图片到 4K(3840x2160) 以下，保持原始比例。"""
    try:
        image = Image.open(io.BytesIO(raw))
        if image.width > 3840 or image.height > 2160:
            image.thumbnail((3840, 2160))
        out = io.BytesIO()
        image.save(out, format=image.format or "JPEG")
        return out.getvalue()
    except Exception as e:
        logger.debug("compress error: %s", e)
        return None


def _resolve_media_type(path: Path) -> str:
    """根据文件扩展名返回 MIME 类型。"""
    return _MIME_MAP.get(path.suffix.lower(), "image/jpeg")


def load_image_for_claude(
    image_path: str,
    workspace_root: Path = _WORKSPACE_ROOT,
) -> dict | None:
    """将本地图片文件转为 Claude 原生 image content block。

    Args:
        image_path: 本地图片路径
        workspace_root: 允许读取的根目录（可注入，便于测试）

    Returns:
        Claude image content block dict，或 None（路径非法/文件不存在/非图片）。
        格式: {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
    """
    path = Path(image_path).expanduser().resolve()

    # 路径遍历保护
    if not str(path).startswith(str(workspace_root)):
        logger.warning("path traversal blocked: %s", path)
        return None

    if not path.is_file():
        logger.debug("file not found: %s", path)
        return None

    # 扩展名检查
    if path.suffix.lower() not in _IMAGE_EXTENSIONS:
        logger.debug("not an image extension: %s", path.suffix)
        return None

    # 文件大小检查
    if path.stat().st_size > _MAX_IMAGE_BYTES:
        logger.warning("image too large: %d bytes", path.stat().st_size)
        return None

    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    media_type = _resolve_media_type(path)

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": b64,
        },
    }


def extract_image_path(user_message: str) -> str | None:
    """从用户消息中提取图片附件路径（如果有）。

    Runner 下载附件后构建的消息格式：
    "用户发来了文件，已自动保存至沙盒路径：\\n`/workspace/sessions/{sid}/uploads/{filename}`\\n..."

    Returns:
        图片路径（含图片扩展名时），或 None。
    """
    match = _ATTACHMENT_PATH_RE.search(user_message)
    if not match:
        return None
    path = match.group(1)
    suffix = Path(path).suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return path
    return None
