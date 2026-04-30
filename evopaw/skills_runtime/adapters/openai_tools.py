"""openai_tools adapter —— 把 SkillDispatcher 暴露为 OpenAI function tool schema。

- **保持单工具形态**：不展开成 18 个独立工具，避免 prompt 工程被打散。LLM 仍然
  通过 `skill_loader(skill_name, task_context)` 单一入口调用 Skill。
- **description = dispatcher.get_description()**：与 SDK MCP 路径完全相同的渐进披露
  阶段一文本（`<available_skills>` XML），保证两个 backend 看到一致的工具说明。
- **parameters JSON Schema**：`{skill_name: string (required), task_context: string}`，
  与 SDK MCP `{"skill_name": str, "task_context": str}` 等价。

properties / required / 工具名集中在 `skills_runtime/tool_schema.py`。
"""

from __future__ import annotations

from ..dispatcher import SkillDispatcher
from ..tool_schema import SKILL_TOOL_NAME, build_input_schema


def build_openai_tool_schema(dispatcher: SkillDispatcher) -> dict:
    """返回单个 OpenAI function tool 字典。

    输出形如：

        {
          "type": "function",
          "function": {
            "name": "skill_loader",
            "description": "<available_skills>...</available_skills>",
            "parameters": {"type": "object", "properties": {...}, "required": [...]}
          }
        }
    """
    return {
        "type": "function",
        "function": {
            "name": SKILL_TOOL_NAME,
            "description": dispatcher.get_description(),
            "parameters": build_input_schema(),
        },
    }
