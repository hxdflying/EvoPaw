"""TestAPI 请求/响应模型"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TestAttachment(BaseModel):
    """测试请求中的附件（本地文件路径，非飞书 file_key）"""

    file_path: str
    file_name: str | None = None


class TestRequest(BaseModel):
    """POST /api/test/message 请求体"""

    routing_key: str
    content: str = ""
    msg_id: str | None = None  # 自动生成 "test_{uuid}"
    sender_id: str = "ou_test001"
    attachment: TestAttachment | None = None


class TestResponse(BaseModel):
    """POST /api/test/message 响应体"""

    msg_id: str
    reply: str
    session_id: str
    duration_ms: int
    skills_called: list[str] = Field(default_factory=list)
