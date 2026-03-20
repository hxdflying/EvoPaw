from __future__ import annotations

"""Logging configuration for XiaoPaw.

- 控制台：人类可读格式
- 文件：JSON 行日志，写入 data/logs/xiaopaw.log（滚动）
"""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """简单的 JSON 日志 formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 附加常见上下文字段（如有）
        for key in ("routing_key", "session_id", "feishu_msg_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(log_dir: Path) -> None:
    """初始化日志：

    - 控制台：人类可读格式，默认 INFO 级别
    - 文件：JSON 行格式，写入 data/logs/xiaopaw.log
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "xiaopaw.log"

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter())

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    # 避免重复添加 handler（例如测试多次调用 setup_logging）
    handler_types = {type(h) for h in root.handlers}
    if RotatingFileHandler not in handler_types:
        root.addHandler(file_handler)
    if logging.StreamHandler not in handler_types:
        root.addHandler(console_handler)

    # 默认使用 INFO 级别，保证关键业务日志可见。
    # Python root logger 默认 level 是 WARNING(30)，不是 NOTSET(0)，
    # 因此必须显式降到 INFO；若已经是 DEBUG 则保留不升。
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)

