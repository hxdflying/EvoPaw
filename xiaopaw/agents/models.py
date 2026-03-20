"""Agents 层 Pydantic 输出模型"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MainTaskOutput(BaseModel):
    """主任务的结构化输出，由 LLM 以 JSON 格式生成。"""

    reply: str = Field(..., description="发送给飞书用户的回复内容")
    used_skills: list[str] = Field(
        default_factory=list,
        description="本次调用的 Skill 名称列表",
    )
