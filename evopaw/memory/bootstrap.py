"""Bootstrap：从 workspace 文件构建 Agent backstory。

只注入"导航骨架"——soul.md 身份、user.md 画像、agent.md 工具规范、
memory.md 索引前 200 行。详情按需通过 SkillLoaderTool 加载，
避免占用宝贵 context window。
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

    # soul / user / agent 三个文件体积可控，完整注入。
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

    # memory.md 是导航索引而非内容仓库，限制前 200 行防膨胀。
    memory_path = workspace_dir / "memory.md"
    if memory_path.exists():
        try:
            lines = memory_path.read_text(encoding="utf-8").splitlines()[:_MEMORY_MAX_LINES]
            parts.append(f"<memory_index>\n{chr(10).join(lines)}\n</memory_index>")
        except OSError:
            logger.warning("bootstrap: cannot read memory.md, skipping <memory_index>")

    return "\n\n".join(parts)
