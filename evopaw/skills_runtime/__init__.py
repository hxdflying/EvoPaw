"""Skills runtime package.

提供跨 backend 复用的 Skill registry、dispatcher、占位符渲染和 provider tool
schema adapters。核心逻辑不依赖具体 LLM SDK，adapter 层负责包装各 provider 的
工具 schema 或兼容入口。
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
