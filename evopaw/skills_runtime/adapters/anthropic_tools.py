"""anthropic_tools adapter —— 把 SkillDispatcher 暴露为 Anthropic Messages API tool schema。

- Anthropic 把 schema 平铺：直接 `{name, description, input_schema}`，没有 `type:function` 包一层。
- 字段名是 `input_schema`，不是 OpenAI 的 `parameters`。
- 工具调用响应在 `content` 中以 `{"type":"tool_use","id":..,"name":..,"input":{...}}` 出现，
  工具结果通过 `{"type":"tool_result","tool_use_id":..,"content":..}` 回写为 user 消息——
  这部分由 AnthropicMessagesBackend 处理，本模块只关心出站 schema。

properties / required / 工具名集中在 `skills_runtime/tool_schema.py`。
"""

from __future__ import annotations

from ..dispatcher import SkillDispatcher
from ..tool_schema import SKILL_TOOL_NAME, build_input_schema


def build_anthropic_tool_schema(dispatcher: SkillDispatcher) -> dict:
    """返回单个 Anthropic tool 字典。

    输出形如：

        {
          "name": "skill_loader",
          "description": "<available_skills>...</available_skills>",
          "input_schema": {"type": "object", "properties": {...}, "required": [...]}
        }
    """
    return {
        "name": SKILL_TOOL_NAME,
        "description": dispatcher.get_description(),
        "input_schema": build_input_schema(),
    }
