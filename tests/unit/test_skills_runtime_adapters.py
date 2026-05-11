"""skills_runtime adapters 单元测试（P3）。

只校验 schema 形状与 description 来源（dispatcher.get_description()）；
不在这里走真实 dispatch（dispatch 路径由 test_skills_runtime_dispatcher.py 覆盖）。

claude_mcp adapter 的兼容入口由原 test_skill_loader.py 继续覆盖（已通过迁移验证），
这里仅验证 thin wrapper 工厂可以接受相同入参且不报错。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evopaw.skills_runtime.adapters.anthropic_tools import build_anthropic_tool_schema
from evopaw.skills_runtime.adapters.openai_tools import build_openai_tool_schema
from evopaw.skills_runtime.dispatcher import SkillDispatcher


@pytest.fixture
def tmp_skills_dir(tmp_path: Path) -> Path:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "load_skills.yaml").write_text(
        "skills:\n  - name: ref_skill\n    type: reference\n    enabled: true\n",
        encoding="utf-8",
    )
    (skills_dir / "ref_skill").mkdir()
    (skills_dir / "ref_skill" / "SKILL.md").write_text(
        "---\nname: ref_skill\ndescription: 示例\ntype: reference\nversion: \"1.0\"\n---\nbody\n",
        encoding="utf-8",
    )
    return skills_dir


class TestOpenAIToolsSchema:
    def test_top_level_shape(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        schema = build_openai_tool_schema(d)
        assert schema["type"] == "function"
        assert "function" in schema

    def test_function_block(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        fn = build_openai_tool_schema(d)["function"]
        assert fn["name"] == "skill_loader"
        assert "<available_skills>" in fn["description"]
        assert "<name>ref_skill</name>" in fn["description"]

    def test_parameters_schema(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        params = build_openai_tool_schema(d)["function"]["parameters"]
        assert params["type"] == "object"
        assert set(params["properties"].keys()) == {"skill_name", "task_context"}
        assert params["properties"]["skill_name"]["type"] == "string"
        assert params["properties"]["task_context"]["type"] == "string"
        assert params["required"] == ["skill_name"]

    def test_description_reflects_session_path(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="abc-123", skills_dir=tmp_skills_dir)
        desc = build_openai_tool_schema(d)["function"]["description"]
        assert "/workspace/sessions/abc-123/" in desc


class TestAnthropicToolsSchema:
    def test_top_level_shape(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        schema = build_anthropic_tool_schema(d)
        # Anthropic 是平铺：name / description / input_schema
        assert schema["name"] == "skill_loader"
        assert "description" in schema
        assert "input_schema" in schema
        # 不能有 OpenAI 那层 type:function 包装
        assert "function" not in schema
        assert schema.get("type") != "function"
        # 不能用 OpenAI 字段名 parameters
        assert "parameters" not in schema

    def test_description_from_dispatcher(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        schema = build_anthropic_tool_schema(d)
        assert schema["description"] == d.get_description()
        assert "<available_skills>" in schema["description"]
        assert "<name>ref_skill</name>" in schema["description"]

    def test_input_schema_structure(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        sch = build_anthropic_tool_schema(d)["input_schema"]
        assert sch["type"] == "object"
        assert set(sch["properties"].keys()) == {"skill_name", "task_context"}
        assert sch["properties"]["skill_name"]["type"] == "string"
        assert sch["properties"]["task_context"]["type"] == "string"
        assert sch["required"] == ["skill_name"]

    def test_description_reflects_session_path(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="abc-123", skills_dir=tmp_skills_dir)
        desc = build_anthropic_tool_schema(d)["description"]
        assert "/workspace/sessions/abc-123/" in desc


class TestClaudeMcpAdapter:
    def test_factory_returns_object(self, tmp_skills_dir: Path):
        from evopaw.skills_runtime.adapters.claude_mcp import build_skill_loader_server

        server = build_skill_loader_server(
            session_id="sid",
            skills_dir=tmp_skills_dir,
        )
        assert server is not None

    def test_result_callback_passthrough_to_dispatcher(
        self, tmp_skills_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """SDK 路径必须把 result_callback 透传给内部 SkillDispatcher。

        过往这条链路漏接，导致 claude_sdk_compat backend 下 background skill
        完成后结果只写日志、不推送给用户（参见 docs/skills-module-review-codex-2026-05-07.md）。
        通过 monkeypatch 拦截 SkillDispatcher 构造调用，断言 callback 真的被注入。
        """
        from evopaw.skills_runtime.adapters import claude_mcp

        captured: dict = {}

        class _StubDispatcher:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def get_description(self) -> str:
                return "<available_skills></available_skills>"

        monkeypatch.setattr(claude_mcp, "SkillDispatcher", _StubDispatcher)

        async def _cb(_t: str, _s: str, _r: str) -> None:
            return None

        claude_mcp.build_skill_loader_server(
            session_id="sid",
            skills_dir=tmp_skills_dir,
            result_callback=_cb,
            workspace_root="/tmp/ws",
        )
        assert captured["result_callback"] is _cb
        assert captured["workspace_root"] == "/tmp/ws"
