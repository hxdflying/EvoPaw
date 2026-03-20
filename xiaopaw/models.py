"""XiaoPaw 核心数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Attachment:
    """飞书附件元信息（Listener 解析，Runner 下载）"""

    msg_type: str  # "image" | "file"
    file_key: str  # 飞书 file_key / image_key
    file_name: str  # 文件名（image 无文件名时用 "{image_key}.jpg"）


@dataclass
class InboundMessage:
    """框架内流转的标准化消息对象"""

    routing_key: str  # "p2p:ou_xxx" | "group:oc_xxx" | "thread:oc_xxx:ot_xxx"
    content: str  # 纯文本内容（附件消息时可为空）
    msg_id: str  # 飞书 message_id
    root_id: str  # 话题根消息 ID
    sender_id: str  # open_id
    ts: int  # 创建时间（毫秒时间戳）
    is_cron: bool = False
    attachment: Attachment | None = None


class SenderProtocol(Protocol):
    """FeishuSender 和 CaptureSender 共同实现的协议"""

    async def send(
        self, routing_key: str, content: str, root_id: str
    ) -> None: ...

    async def send_thinking(
        self, routing_key: str, root_id: str
    ) -> str | None: ...

    async def update_card(
        self, card_msg_id: str, content: str
    ) -> None: ...

    async def send_text(
        self, routing_key: str, content: str, root_id: str
    ) -> None: ...
