"""SkillLoaderTool — EvoPaw 核心工具

设计要点：
  1. 渐进式披露（Progressive Disclosure）
     - 阶段一：解析 SKILL.md frontmatter，构建轻量 XML 注入工具 description
     - 阶段二：调用时按需加载完整 SKILL.md
  2. 参考型 vs 任务型
     - reference：返回指令文本，主 Agent 自行消化
     - task：创建 Sub-Agent 执行
  3. 会话隔离
     - 每个 MCP server 实例绑定 session_id，工作目录隔离
  4. history_reader 内联
     - 从系统维护的 history_all 分页读取，不经过 LLM
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from claude_agent_sdk import create_sdk_mcp_server, tool

from evopaw.session.models import MessageEntry

logger = logging.getLogger(__name__)

# SKILLS_DIR：本项目 skills 目录
_SKILLS_DIR = Path(__file__).parents[1] / "skills"

# Skill 脚本在 workspace 中的挂载路径
_SKILLS_MOUNT = "/mnt/skills"


def _extract_frontmatter_description(content: str) -> str:
    """从 SKILL.md 的 YAML frontmatter 中提取 description 字段（最多 200 字符）。"""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return ""
    try:
        front = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return ""
    desc = front.get("description", "") if front else ""
    if not desc:
        return ""
    return desc[:200] + "..." if len(desc) > 200 else desc


def _build_skill_registry(skills_dir: Path) -> dict[str, dict[str, Any]]:
    """解析 load_skills.yaml，构建 skill 注册表。"""
    manifest_path = skills_dir / "load_skills.yaml"
    if not manifest_path.exists():
        logger.warning("load_skills.yaml not found at %s", manifest_path)
        return {}

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        logger.error("Failed to parse load_skills.yaml", exc_info=True)
        return {}

    skills_conf = manifest.get("skills") or []
    skills_root = skills_dir.resolve()
    registry: dict[str, dict[str, Any]] = {}

    for skill_conf in skills_conf:
        if not skill_conf.get("enabled", True):
            continue
        name = skill_conf["name"]
        skill_type = skill_conf.get("type", "task")

        # 路径穿越防护
        skill_path = (skills_dir / name).resolve()
        if not str(skill_path).startswith(str(skills_root)):
            logger.warning("Blocked path traversal attempt, skill name=%r", name)
            continue

        skill_md_path = skill_path / "SKILL.md"
        if not skill_md_path.exists():
            logger.warning("SKILL.md not found for skill: %s, skipping", name)
            continue

        registry[name] = {
            "type": skill_type,
            "path": skill_path,
        }

    return registry


def _build_description_xml(
    registry: dict[str, dict[str, Any]],
    session_id: str,
    skills_dir: Path,
) -> str:
    """构建工具 description 的 XML 列表。"""
    xml_parts = ["<available_skills>"]

    for name, info in registry.items():
        skill_md_path = info["path"] / "SKILL.md"
        content = skill_md_path.read_text(encoding="utf-8")
        desc = _extract_frontmatter_description(content)
        xml_parts.append(
            f"  <skill>\n"
            f"    <name>{name}</name>\n"
            f"    <type>{info['type']}</type>\n"
            f"    <description>{desc}</description>\n"
            f"  </skill>"
        )

    xml_parts.append("</available_skills>")

    session_dir = f"/workspace/sessions/{session_id}" if session_id else "/workspace/sessions/<session_id>"
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

    # 路径常量
    _skill_base = f"{_SKILLS_MOUNT}/{skill_name}"
    _session_dir = f"/workspace/sessions/{session_id}" if session_id else "/workspace/sessions/<session_id>"

    # 替换路径占位符
    stripped = stripped.replace("{skill_base}", _skill_base)
    stripped = stripped.replace("{_skill_base}", _skill_base)
    stripped = stripped.replace("{session_id}", session_id or "<session_id>")
    stripped = stripped.replace("{session_dir}", _session_dir)

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


def _handle_history_reader(history_all: list[MessageEntry], task_context: str) -> str:
    """内联处理 history_reader：从 history_all 分页读取。"""
    try:
        params = json.loads(task_context) if task_context.strip().startswith("{") else {}
    except (json.JSONDecodeError, Exception):
        params = {}

    page = max(1, int(params.get("page", 1)))
    page_size = max(1, min(50, int(params.get("page_size", 20))))

    total = len(history_all)
    total_pages = max(1, (total + page_size - 1) // page_size)

    start = (page - 1) * page_size
    end = start + page_size
    page_msgs = history_all[start:end]

    messages = [
        {"role": m.role, "content": m.content}
        for m in page_msgs
    ]

    result = {
        "errcode": 0,
        "message": f"成功读取第 {page} 页，共 {total} 条消息，本页 {len(messages)} 条",
        "data": {
            "messages": messages,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        },
    }
    return json.dumps(result, ensure_ascii=False)


def build_skill_loader_server(
    session_id: str,
    routing_key: str = "",
    history_all: list[MessageEntry] | None = None,
    skills_dir: Path | None = None,
):
    """工厂：构建 skill_loader MCP server，绑定当前 session。

    每次对话创建一个新的 MCP server 实例，绑定 session_id 和 history_all，
    实现会话隔离。

    Args:
        session_id: 当前 session ID
        routing_key: 飞书消息路由键（供 feishu_ops 等 Skill 使用）
        history_all: 完整对话历史（供 history_reader 内联读取）
        skills_dir: skills 目录路径（测试时覆盖）

    Returns:
        Claude Agent SDK MCP server 配置对象
    """
    _skills_dir = skills_dir or _SKILLS_DIR
    registry = _build_skill_registry(_skills_dir)
    instruction_cache: dict[str, str] = {}
    _history_all = list(history_all) if history_all else []

    # 构建工具描述
    if registry:
        description = _build_description_xml(registry, session_id, _skills_dir)
    else:
        description = "SkillLoaderTool 已初始化，但暂无可用 Skill。"

    @tool(
        "skill_loader",
        description,
        {"skill_name": str, "task_context": str},
    )
    async def skill_loader(args):
        skill_name = args["skill_name"]
        task_context = args.get("task_context", "")

        # task_context 可能是 dict/list，统一转字符串
        if isinstance(task_context, (dict, list)):
            task_context = json.dumps(task_context, ensure_ascii=False)
        elif task_context is None:
            task_context = ""

        # 未知 Skill
        if skill_name not in registry:
            available = list(registry.keys())
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"错误：未找到 Skill '{skill_name}'。\n"
                        f"可用 Skill：{available}\n"
                        f"请从以上列表中选择正确的 skill_name 重新调用。"
                    ),
                }],
            }

        # history_reader 内联处理
        if skill_name == "history_reader":
            result_text = _handle_history_reader(_history_all, task_context)
            return {"content": [{"type": "text", "text": result_text}]}

        skill_info = registry[skill_name]

        if skill_info["type"] == "reference":
            # 参考型：返回完整指令文本
            instructions = _get_skill_instructions(
                registry, skill_name, session_id, routing_key, instruction_cache,
            )
            return {
                "content": [{
                    "type": "text",
                    "text": f"<skill_instructions>\n{instructions}\n</skill_instructions>",
                }],
            }

        # 任务型：调用 Sub-Agent 执行
        instructions = _get_skill_instructions(
            registry, skill_name, session_id, routing_key, instruction_cache,
        )
        _workspace_root = "/workspace"

        from evopaw.agents.skill_agent import run_skill_agent  # noqa: PLC0415

        result_text = await run_skill_agent(
            skill_name=skill_name,
            skill_instructions=instructions,
            task_context=task_context,
            session_path=_workspace_root,
        )
        return {"content": [{"type": "text", "text": result_text}]}

    return create_sdk_mcp_server(
        "evopaw",
        tools=[skill_loader],
    )
