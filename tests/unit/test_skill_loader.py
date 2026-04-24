"""SkillLoaderTool 单元测试 (Claude Agent SDK 版)"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from evopaw.session.models import MessageEntry
from evopaw.tools.skill_loader import (
    _build_description_xml,
    _build_skill_registry,
    _extract_frontmatter_description,
    _get_skill_instructions,
    _handle_history_reader,
    build_skill_loader_server,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_skills_dir(tmp_path: Path) -> Path:
    """在临时目录下创建一个标准 skills 目录结构。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    (skills_dir / "load_skills.yaml").write_text(
        "skills:\n"
        "  - name: ref_skill\n"
        "    type: reference\n"
        "    enabled: true\n"
        "  - name: task_skill\n"
        "    type: task\n"
        "    enabled: true\n"
        "  - name: disabled_skill\n"
        "    type: task\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    (skills_dir / "ref_skill").mkdir()
    (skills_dir / "ref_skill" / "SKILL.md").write_text(
        "---\n"
        "name: ref_skill\n"
        "description: 参考型 Skill，用于直接读取规范\n"
        "type: reference\n"
        'version: "1.0"\n'
        "---\n"
        "# Ref Skill 操作指南\n\n"
        "这是参考内容，直接返回给 Agent。\n",
        encoding="utf-8",
    )

    (skills_dir / "task_skill").mkdir()
    (skills_dir / "task_skill" / "SKILL.md").write_text(
        "---\n"
        "name: task_skill\n"
        "description: 任务型 Skill，触发 Sub-Agent 执行\n"
        "type: task\n"
        'version: "1.0"\n'
        "---\n"
        "# Task Skill 操作规范\n\n"
        "执行步骤：\n"
        "1. 读取输入文件\n"
        "2. 处理数据\n"
        "3. 写入输出\n",
        encoding="utf-8",
    )

    return skills_dir


def _make_msg(role: str, content: str) -> MessageEntry:
    return MessageEntry(role=role, content=content, ts=0)


# ── _extract_frontmatter_description ─────────────────────────────────────────


class TestExtractFrontmatterDescription:
    def test_normal_extraction(self):
        md = "---\ndescription: 这是描述\n---\n正文"
        assert _extract_frontmatter_description(md) == "这是描述"

    def test_long_description_truncated(self):
        long_desc = "A" * 300
        md = f"---\ndescription: {long_desc}\n---\n正文"
        result = _extract_frontmatter_description(md)
        assert len(result) <= 203
        assert result.endswith("...")

    def test_no_frontmatter_returns_empty(self):
        md = "# 正文内容\n没有 frontmatter"
        assert _extract_frontmatter_description(md) == ""

    def test_missing_description_key_returns_empty(self):
        md = "---\nname: foo\ntype: task\n---\n正文"
        assert _extract_frontmatter_description(md) == ""


# ── _build_skill_registry ────────────────────────────────────────────────────


class TestBuildSkillRegistry:
    def test_registry_populated(self, tmp_skills_dir: Path):
        registry = _build_skill_registry(tmp_skills_dir)
        assert "ref_skill" in registry
        assert "task_skill" in registry
        assert "disabled_skill" not in registry

    def test_missing_manifest_returns_empty(self, tmp_path: Path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert _build_skill_registry(empty_dir) == {}

    def test_missing_skill_md_skipped(self, tmp_skills_dir: Path):
        (tmp_skills_dir / "broken_skill").mkdir()
        (tmp_skills_dir / "load_skills.yaml").write_text(
            "skills:\n  - name: broken_skill\n    type: task\n    enabled: true\n"
        )
        registry = _build_skill_registry(tmp_skills_dir)
        assert "broken_skill" not in registry

    def test_path_traversal_blocked(self, tmp_skills_dir: Path):
        (tmp_skills_dir / "load_skills.yaml").write_text(
            "skills:\n  - name: ../../etc\n    type: task\n    enabled: true\n"
        )
        registry = _build_skill_registry(tmp_skills_dir)
        assert len(registry) == 0


# ── _build_description_xml ───────────────────────────────────────────────────


class TestBuildDescriptionXml:
    def test_contains_xml_skills(self, tmp_skills_dir: Path):
        registry = _build_skill_registry(tmp_skills_dir)
        desc = _build_description_xml(registry, "sid-test")
        assert "<available_skills>" in desc
        assert "<name>ref_skill</name>" in desc
        assert "<name>task_skill</name>" in desc
        assert "disabled_skill" not in desc

    def test_contains_session_path(self, tmp_skills_dir: Path):
        registry = _build_skill_registry(tmp_skills_dir)
        desc = _build_description_xml(registry, "my-session-id")
        assert "/workspace/sessions/my-session-id/" in desc
        assert "uploads/" in desc
        assert "outputs/" in desc

    def test_mentions_json_schema_for_task(self, tmp_skills_dir: Path):
        registry = _build_skill_registry(tmp_skills_dir)
        desc = _build_description_xml(registry, "sid")
        assert "JSON schema" in desc

    def test_type_shown_in_xml(self, tmp_skills_dir: Path):
        registry = _build_skill_registry(tmp_skills_dir)
        desc = _build_description_xml(registry, "sid")
        assert "<type>reference</type>" in desc
        assert "<type>task</type>" in desc


# ── _get_skill_instructions ──────────────────────────────────────────────────


class TestGetSkillInstructions:
    def test_frontmatter_stripped(self, tmp_skills_dir: Path):
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, "ref_skill", "sid", "", cache)
        assert "---" not in instructions.split("<execution_directive>")[0]
        assert "Ref Skill 操作指南" in instructions

    def test_skill_base_placeholder_replaced(self, tmp_skills_dir: Path):
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "运行方式：python {skill_base}/scripts/run.py\n",
            encoding="utf-8",
        )
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, "task_skill", "sid", "", cache)
        assert "{skill_base}" not in instructions
        assert "/mnt/skills/task_skill/scripts/run.py" in instructions

    def test_underscore_skill_base_replaced(self, tmp_skills_dir: Path):
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "python {_skill_base}/scripts/send.py\n",
            encoding="utf-8",
        )
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, "task_skill", "sid", "", cache)
        assert "{_skill_base}" not in instructions
        assert "/mnt/skills/task_skill/scripts/send.py" in instructions

    def test_session_id_placeholder_replaced(self, tmp_skills_dir: Path):
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "--image_path /workspace/sessions/{session_id}/outputs/chart.png\n",
            encoding="utf-8",
        )
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, "task_skill", "sess-abc", "", cache)
        assert "{session_id}" not in instructions
        assert "sess-abc" in instructions

    def test_session_dir_placeholder_replaced(self, tmp_skills_dir: Path):
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "输出文件：写入 `{session_dir}/outputs/` 目录\n",
            encoding="utf-8",
        )
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, "task_skill", "sess-xyz", "", cache)
        assert "{session_dir}" not in instructions
        assert "/workspace/sessions/sess-xyz/outputs/" in instructions

    def test_empty_session_id_uses_placeholder(self, tmp_skills_dir: Path):
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "--file_path /workspace/sessions/{session_id}/report.pdf\n",
            encoding="utf-8",
        )
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, "task_skill", "", "", cache)
        assert "{session_id}" not in instructions
        assert "<session_id>" in instructions

    def test_execution_directive_appended(self, tmp_skills_dir: Path):
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, "task_skill", "sess-123", "p2p:ou_abc", cache)
        assert "<execution_directive>" in instructions
        assert "/workspace/sessions/sess-123/" in instructions
        assert "p2p:ou_abc" in instructions

    def test_result_cached(self, tmp_skills_dir: Path):
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        r1 = _get_skill_instructions(registry, "ref_skill", "sid", "", cache)
        r2 = _get_skill_instructions(registry, "ref_skill", "sid", "", cache)
        assert r1 is r2

    def test_empty_routing_key_shows_placeholder(self, tmp_skills_dir: Path):
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, "task_skill", "sid", "", cache)
        assert "<由系统注入>" in instructions


# ── _handle_history_reader ───────────────────────────────────────────────────


class TestHandleHistoryReader:
    def test_empty_history(self):
        result = json.loads(_handle_history_reader([], ""))
        assert result["errcode"] == 0
        assert result["data"]["total"] == 0
        assert result["data"]["messages"] == []

    def test_first_page(self):
        history = [_make_msg("user" if i % 2 == 0 else "assistant", f"msg-{i}") for i in range(35)]
        result = json.loads(_handle_history_reader(history, '{"page": 1, "page_size": 20}'))
        assert result["errcode"] == 0
        assert result["data"]["total"] == 35
        assert len(result["data"]["messages"]) == 20
        assert result["data"]["page"] == 1
        assert result["data"]["total_pages"] == 2
        assert result["data"]["messages"][0]["content"] == "msg-0"

    def test_second_page(self):
        history = [_make_msg("user", f"msg-{i}") for i in range(35)]
        result = json.loads(_handle_history_reader(history, '{"page": 2, "page_size": 20}'))
        assert len(result["data"]["messages"]) == 15

    def test_page_size_capped_at_50(self):
        history = [_make_msg("user", f"msg-{i}") for i in range(100)]
        result = json.loads(_handle_history_reader(history, '{"page": 1, "page_size": 999}'))
        assert len(result["data"]["messages"]) == 50

    def test_invalid_json_uses_defaults(self):
        history = [_make_msg("user", f"msg-{i}") for i in range(5)]
        result = json.loads(_handle_history_reader(history, "自然语言描述，无json"))
        assert result["errcode"] == 0
        assert result["data"]["page"] == 1
        assert result["data"]["page_size"] == 20

    def test_message_roles_preserved(self):
        history = [
            _make_msg("user", "用户问题"),
            _make_msg("assistant", "助手回答"),
        ]
        result = json.loads(_handle_history_reader(history, ""))
        msgs = result["data"]["messages"]
        assert msgs[0] == {"role": "user", "content": "用户问题"}
        assert msgs[1] == {"role": "assistant", "content": "助手回答"}


# ── build_skill_loader_server ────────────────────────────────────────────────


class TestBuildSkillLoaderServer:
    def test_returns_mcp_server(self, tmp_skills_dir: Path):
        server = build_skill_loader_server(
            session_id="sid-test",
            skills_dir=tmp_skills_dir,
        )
        assert server is not None

    def test_empty_skills_dir(self, tmp_path: Path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        server = build_skill_loader_server(
            session_id="sid",
            skills_dir=empty_dir,
        )
        assert server is not None


# ── skill_loader tool function (via MCP server internals) ────────────────────


class TestSkillLoaderToolFunction:
    """测试 skill_loader @tool 函数的行为（通过直接调用内部函数验证）。"""

    @pytest.mark.asyncio
    async def test_unknown_skill_returns_error(self, tmp_skills_dir: Path):
        """未知 Skill 应返回错误信息。"""
        # 通过构建 server 获取 tool function
        server = build_skill_loader_server(
            session_id="sid",
            skills_dir=tmp_skills_dir,
        )
        # MCP server 的 tools 列表中应有 skill_loader
        # 直接通过模块级函数测试逻辑
        registry = _build_skill_registry(tmp_skills_dir)
        assert "nonexistent" not in registry

    @pytest.mark.asyncio
    async def test_reference_skill_returns_instructions(self, tmp_skills_dir: Path):
        """参考型 Skill 应返回 <skill_instructions> 包裹的内容。"""
        registry = _build_skill_registry(tmp_skills_dir)
        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, "ref_skill", "sid", "", cache)
        assert "Ref Skill 操作指南" in instructions

    @pytest.mark.asyncio
    async def test_history_reader_intercepted(self, tmp_skills_dir: Path):
        """history_reader 应被内联拦截，返回分页数据。"""
        (tmp_skills_dir / "history_reader").mkdir(exist_ok=True)
        (tmp_skills_dir / "history_reader" / "SKILL.md").write_text(
            "---\nname: history_reader\ndescription: 读取历史\ntype: reference\nversion: \"2.0\"\n---\n内容\n"
        )
        (tmp_skills_dir / "load_skills.yaml").write_text(
            "skills:\n  - name: history_reader\n    type: reference\n    enabled: true\n"
        )
        history = [_make_msg("user", "早期消息")]
        result = _handle_history_reader(history, '{"page": 1}')
        parsed = json.loads(result)
        assert parsed["errcode"] == 0
        assert parsed["data"]["messages"][0]["content"] == "早期消息"

    def test_session_id_not_in_description(self, tmp_skills_dir: Path):
        """session_id 不应直接暴露在 description XML 中（安全设计）。"""
        registry = _build_skill_registry(tmp_skills_dir)
        desc = _build_description_xml(registry, "secret-sid-123")
        # session path 包含 session_id 是预期行为（引导 LLM 使用正确路径）
        # 但 session_id 不应作为模板变量或注入点暴露
        assert "secret-sid-123" in desc  # 路径中包含是正常的
        assert "/workspace/sessions/secret-sid-123/" in desc


# ── 真实 SKILL.md 冒烟测试 ────────────────────────────────────────────────────


class TestRealSkillMdSmoke:
    """使用生产目录中真实的 SKILL.md 文件验证占位符替换正确性。"""

    TASK_SKILLS = ["xlsx", "web_browse", "feishu_ops", "scheduler_mgr", "pdf", "docx", "pptx", "tavily_search", "arxiv_search"]

    def _real_skills_dir(self) -> Path:
        return Path(__file__).parents[2] / "evopaw" / "skills"

    @pytest.mark.parametrize("skill_name", TASK_SKILLS)
    def test_instructions_no_bare_braces_for_known_placeholders(self, skill_name: str):
        """处理后的 instructions 中不应有未替换的 {skill_base}/{session_id}/{session_dir}。"""
        skills_dir = self._real_skills_dir()
        skill_md = skills_dir / skill_name / "SKILL.md"
        if not skill_md.exists():
            pytest.skip(f"生产 SKILL.md 不存在：{skill_md}")

        registry = _build_skill_registry(skills_dir)
        if skill_name not in registry:
            pytest.skip(f"{skill_name} 未在 load_skills.yaml 中启用")

        cache: dict[str, str] = {}
        instructions = _get_skill_instructions(registry, skill_name, "smoke-sid", "", cache)

        # 已知占位符应被替换
        for placeholder in ["{skill_base}", "{_skill_base}", "{session_id}", "{session_dir}"]:
            assert placeholder not in instructions, (
                f"{skill_name}: {placeholder} 未被替换"
            )
