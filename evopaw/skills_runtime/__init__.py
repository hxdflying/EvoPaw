"""skills_runtime —— Skill 注册 / 指令拼接 / 分发的纯逻辑层（P3）。

设计目标（参见 docs/multi-provider-final-plan-2026-04-27.md §5 P3）：

- **零 SDK 依赖**：本子包及其 `registry / instructions / dispatcher` 模块不 import
  `claude_agent_sdk`；adapter 子包再按 backend 协议族包装。
- **复用**：OpenAIChatBackend 与 ClaudeSDKCompatBackend 共用同一份
  「skill 注册 / 渐进披露 / 三类分发」逻辑；只在 adapter 层选择"包成 SDK MCP server"
  还是"暴露 OpenAI function tool schema + 直接 dispatch"。
- **行为零变化**：本阶段三个核心函数（`_build_skill_registry / _build_description_xml /
  _get_skill_instructions / _handle_history_reader`）原样从 `tools/skill_loader.py`
  搬来，确保 `test_skill_loader.py` 全部通过。

入口：
- `SkillDispatcher`：业务侧的 dispatch 实例，输入 `(skill_name, task_context)`，
  输出纯文本（OpenAI 路径直接吃；MCP adapter 包成 `{"content":[{"type":"text",...}]}`）。
- `build_openai_tool_schema(dispatcher)`：暴露单工具 OpenAI function 描述。
- `build_skill_loader_server(...)`：兼容入口（搬自 `tools/skill_loader.py`），保留
  现有调用方与测试不破。
"""

from __future__ import annotations

from .dispatcher import SkillDispatcher
from .instructions import (
    _build_description_xml,
    _get_skill_instructions,
)
from .registry import (
    _build_skill_registry,
    _extract_frontmatter_description,
)

__all__ = [
    "SkillDispatcher",
    "_build_description_xml",
    "_build_skill_registry",
    "_extract_frontmatter_description",
    "_get_skill_instructions",
]
