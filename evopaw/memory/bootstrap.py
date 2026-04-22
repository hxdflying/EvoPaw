"""Bootstrap：从 workspace 文件构建 Agent backstory

💡【第19课·Bootstrap 设计哲学】
backstory 只注入"导航骨架"，不把所有记忆内容塞进来：
  - soul.md   → Agent 身份风格（不变的核心人设）
  - user.md   → 用户画像（由 memory-save Skill 持续更新）
  - agent.md  → 工具使用规范（禁止 FileWriterTool 等约束）
  - memory.md → 记忆索引前 200 行（只写指针，详情按需加载）

这样 Agent 知道"哪里有什么"，需要时用 SkillLoaderTool 主动读取，
不需要时不占用宝贵的 context window。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MEMORY_MAX_LINES = 200


def build_bootstrap_prompt(workspace_dir: Path) -> str:
    """从 workspace 目录读取 4 个文件，构建 Agent backstory。

    Args:
        workspace_dir: workspace 目录路径（可以不存在）

    Returns:
        XML 标签包裹的四段式 backstory 字符串；
        文件缺失或不可读时跳过对应 section；
        workspace 目录不存在或四个文件全缺失时返回空字符串。
    """
    parts: list[str] = []

    # 💡 soul / user / agent 三个文件完整注入（内容相对稳定、体积可控）
    for fname, tag in [
        ("soul.md",  "soul"),
        ("user.md",  "user_profile"),
        ("agent.md", "agent_rules"),
    ]:
        path = workspace_dir / fname
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                parts.append(f"<{tag}>\n{content}\n</{tag}>")
            except OSError:
                # 与"文件缺失时静默跳过"保持一致的容错语义
                logger.warning("bootstrap: cannot read %s, skipping section <%s>", fname, tag)

    # 💡 memory.md 限制 200 行防膨胀：memory.md 是导航索引，不是内容仓库
    memory_path = workspace_dir / "memory.md"
    if memory_path.exists():
        try:
            lines = memory_path.read_text(encoding="utf-8").splitlines()[:_MEMORY_MAX_LINES]
            parts.append(f"<memory_index>\n{chr(10).join(lines)}\n</memory_index>")
        except OSError:
            logger.warning("bootstrap: cannot read memory.md, skipping <memory_index>")

    return "\n\n".join(parts)
