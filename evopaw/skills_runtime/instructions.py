"""Skill 指令 —— 渐进披露阶段一/二的文本拼接。

搬自 `evopaw/tools/skill_loader.py` 的 `_build_description_xml / _get_skill_instructions`，
行为字节级一致：

- `_build_description_xml(registry, session_id)`：阶段一，构建 `<available_skills>` XML
  + session 路径头部，作为「skill_loader 工具描述」注入到 LLM。
- `_get_skill_instructions(registry, name, session_id, routing_key, cache)`：阶段二，
  按需读取完整 SKILL.md，剥离 frontmatter，调用 `placeholders.render` 替换新旧
  占位符（``${EVOPAW_*}`` + ``{skill_base}/{_skill_base}/{session_id}/{session_dir}``），
  并追加 `<execution_directive>` 段。

占位符规约见 [docs/skills-placeholders.md](../../docs/skills-placeholders.md) 与
`evopaw/skills_runtime/placeholders.py`。容器内挂载点固定为 `/mnt/skills/<name>`，
**不是** `/workspace/skills/<name>`（历史文档常见的错误前提）。
"""

from __future__ import annotations

import re
from typing import Any

from .placeholders import _SKILLS_MOUNT, render


def _build_description_xml(
    registry: dict[str, dict[str, Any]],
    session_id: str,
) -> str:
    """构建工具 description 的 XML 列表。"""
    xml_parts = ["<available_skills>"]

    for name, info in registry.items():
        skill_md_path = info["path"] / "SKILL.md"
        content = skill_md_path.read_text(encoding="utf-8")
        from .registry import _extract_frontmatter_description  # 避免循环依赖

        desc = _extract_frontmatter_description(content)
        # P0-1：available / unavailable_reason 让模型看到 skill 存在但当前不可用，
        # 避免「能力清单瘦身」造成 LLM 误以为根本没这个 skill；dispatcher 仍会硬拦截。
        # 旧 registry 没有 available 字段时按可用处理，保持向后兼容。
        available = info.get("available", True)
        reason = info.get("unavailable_reason", "")
        xml_parts.append(
            f"  <skill>\n"
            f"    <name>{name}</name>\n"
            f"    <type>{info['type']}</type>\n"
            f"    <available>{'true' if available else 'false'}</available>\n"
            + (f"    <unavailable_reason>{reason}</unavailable_reason>\n" if not available else "")
            + f"    <description>{desc}</description>\n"
            f"  </skill>"
        )

    xml_parts.append("</available_skills>")

    session_dir = (
        f"/workspace/sessions/{session_id}"
        if session_id
        else "/workspace/sessions/<session_id>"
    )
    return (
        "当需要完成的任务涉及以下 XML 列表中的技能时，调用此工具。\n"
        "根据 XML 列表选择正确的 skill_name；调用 task 类型 Skill 时，task_context 中必须定义 JSON schema。\n"
        f"当前 session 工作目录：{session_dir}/\n"
        f"  - 输入文件（用户上传）：{session_dir}/uploads/\n"
        f"  - 输出文件（Skill 产出）：{session_dir}/outputs/\n\n"
        + "\n".join(xml_parts)
    )


def _get_skill_instructions(
    registry: dict[str, dict[str, Any]],
    skill_name: str,
    session_id: str,
    routing_key: str,
    cache: dict[str, str],
) -> str:
    """渐进式披露第二阶段：读取完整 SKILL.md，替换路径占位符。"""
    if skill_name in cache:
        return cache[skill_name]

    skill_path = registry[skill_name]["path"]
    content = (skill_path / "SKILL.md").read_text(encoding="utf-8")
    # 剥离 YAML frontmatter
    stripped = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL)

    # 路径常量（execution_directive 使用；占位符替换由 placeholders.render 负责）
    _skill_base = f"{_SKILLS_MOUNT}/{skill_name}"
    _session_dir = (
        f"/workspace/sessions/{session_id}"
        if session_id
        else "/workspace/sessions/<session_id>"
    )

    # P1-3：替换新旧占位符（${EVOPAW_*} + {skill_base}/{_skill_base}/{session_id}/
    # {session_dir}）。${EVOPAW_TODAY}/${EVOPAW_NOW} 在首次渲染时刻冻结，与缓存
    # 语义一致——dispatcher 实例每轮 turn 重建，不会出现跨日期复用。
    stripped = render(
        stripped,
        skill_name=skill_name,
        session_id=session_id,
        routing_key=routing_key,
    )

    # 拼接执行环境指令
    execution_directive = (
        f"\n\n<execution_directive>\n"
        f"Skill 资源目录：{_skill_base}/\n"
        f"当前 Session 工作目录：{_session_dir}/\n"
        f"  - 用户上传文件：{_session_dir}/uploads/\n"
        f"  - 输出文件目录：{_session_dir}/outputs/\n"
        f"  - 临时文件目录：{_session_dir}/tmp/\n"
        f"当前用户 routing_key：{routing_key if routing_key else '<由系统注入>'}\n"
        f"</execution_directive>"
    )

    result = stripped + execution_directive
    cache[skill_name] = result
    return result
