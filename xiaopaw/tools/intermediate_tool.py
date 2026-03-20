"""IntermediateTool — 中间思考产物保存工具.

用于在 Agent 执行过程中显式保存中间结果（字符串/列表/字典等），
配合 verbose / trace 回放，对调试复杂推理过程很有帮助。
"""

from __future__ import annotations

import json
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator


class IntermediateToolSchema(BaseModel):
    """Input schema for IntermediateTool."""

    intermediate_product: Any = Field(
        ...,
        description=(
            "中间思考产物，需要保存的内容。"
            "支持任意类型：字符串、列表、字典等，会自动转换为字符串格式。"
            "例如：列表 ['item1', 'item2'] 会自动转换为 'item1\\nitem2'"
        ),
    )

    @field_validator("intermediate_product", mode="before")
    @classmethod
    def convert_to_string(cls, v: Any) -> str:
        """将任意类型的输入转换为字符串。"""
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return "\n".join(str(item) for item in v)
        if isinstance(v, dict):
            try:
                return json.dumps(v, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                return str(v)
        return str(v)


class IntermediateTool(BaseTool):
    """中间结果保存工具。"""

    name: str = "Save_Intermediate_Product_Tool"
    description: str = (
        "用于在 agent 执行过程中保存中间的思考产物，"
        "支持任意类型输入（字符串、列表、字典等），会自动转换为字符串格式。"
    )
    args_schema: type[BaseModel] = IntermediateToolSchema

    def _run(
        self,
        intermediate_product: str,
        **_: Any,
    ) -> str:
        # 这里不真正持久化，只作为语义上的“checkpoint”，方便 Agent 组织思路。
        # 如需持久化，可在 XiaoPaw 后续版本中接入 Session / Trace 存储。
        return "中间结果已保存，可以进行下一步思考。"

