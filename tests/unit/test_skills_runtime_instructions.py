"""skills_runtime.instructions 单元测试。

覆盖：

- `{skill_base}` / `{_skill_base}` 替换值是 `/mnt/skills/<name>`（不是
  `/workspace/skills/<name>`，这是历史文档常见的错误前提）
- `{session_id}` / `{session_dir}` 替换值
- 缺省 session_id 时退化为 `<session_id>` 占位
- frontmatter 在指令注入前被剥离
- `<execution_directive>` 被追加到末尾，且 routing_key 出现在其中
- `<available_skills>` XML 含 `<available>` / `<unavailable_reason>` 标记
- 已构建的 19 个 enabled skill 都能成功构建 instruction 字符串

注：本文件保护当前占位符与指令拼接行为；调整占位符体系时需要同步更新。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evopaw.skills_runtime.instructions import (
    _build_description_xml,
    _get_skill_instructions,
)
from evopaw.skills_runtime.registry import _build_skill_registry


_REPO_SKILLS_DIR = Path(__file__).parents[2] / "evopaw" / "skills"


@pytest.fixture
def synthetic_registry(tmp_path: Path) -> dict:
    """构造两个 skill：一个 reference 一个 task；都带占位符。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "load_skills.yaml").write_text(
        "skills:\n"
        "  - name: ref_one\n    type: reference\n    enabled: true\n"
        "  - name: task_one\n    type: task\n    enabled: true\n",
        encoding="utf-8",
    )
    (skills_dir / "ref_one").mkdir()
    (skills_dir / "ref_one" / "SKILL.md").write_text(
        "---\nname: ref_one\ndescription: r-desc\ntype: reference\nversion: \"1.0\"\n---\n"
        "Read {skill_base}/data/x.json\n"
        "Session: {session_id}, Dir: {session_dir}\n",
        encoding="utf-8",
    )
    (skills_dir / "task_one").mkdir()
    (skills_dir / "task_one" / "SKILL.md").write_text(
        "---\nname: task_one\ndescription: t-desc\ntype: task\nversion: \"1.0\"\n---\n"
        "Run python {_skill_base}/scripts/run.py with cwd {session_dir}\n",
        encoding="utf-8",
    )
    return _build_skill_registry(skills_dir)


# ──────────────────────────────────────────────────────────────────
# _get_skill_instructions —— 占位符替换
# ──────────────────────────────────────────────────────────────────


class TestPlaceholderSubstitution:
    def test_skill_base_replaced_to_mnt_skills(self, synthetic_registry):
        cache: dict = {}
        out = _get_skill_instructions(
            synthetic_registry, "ref_one",
            session_id="sid_x", routing_key="p2p:u1", cache=cache,
        )
        assert "/mnt/skills/ref_one/data/x.json" in out
        # /workspace/skills 是历史文档的错误前提，必须不出现
        assert "/workspace/skills" not in out

    def test_underscore_skill_base_alias(self, synthetic_registry):
        cache: dict = {}
        out = _get_skill_instructions(
            synthetic_registry, "task_one",
            session_id="sid_y", routing_key="p2p:u2", cache=cache,
        )
        assert "/mnt/skills/task_one/scripts/run.py" in out

    def test_session_id_and_dir_replaced(self, synthetic_registry):
        cache: dict = {}
        out = _get_skill_instructions(
            synthetic_registry, "ref_one",
            session_id="sid_z", routing_key="p2p:u3", cache=cache,
        )
        assert "Session: sid_z" in out
        assert "Dir: /workspace/sessions/sid_z" in out

    def test_empty_session_id_uses_placeholder(self, synthetic_registry):
        cache: dict = {}
        out = _get_skill_instructions(
            synthetic_registry, "ref_one",
            session_id="", routing_key="", cache=cache,
        )
        # 空 session_id 退化为 <session_id> 占位
        assert "Session: <session_id>" in out
        assert "Dir: /workspace/sessions/<session_id>" in out

    def test_frontmatter_stripped(self, synthetic_registry):
        cache: dict = {}
        out = _get_skill_instructions(
            synthetic_registry, "ref_one",
            session_id="sid", routing_key="rk", cache=cache,
        )
        # frontmatter 字段不应进入指令文本
        assert "version:" not in out.split("<execution_directive>")[0]
        assert "type: reference" not in out.split("<execution_directive>")[0]

    def test_execution_directive_appended(self, synthetic_registry):
        cache: dict = {}
        out = _get_skill_instructions(
            synthetic_registry, "ref_one",
            session_id="sid_q", routing_key="p2p:abc", cache=cache,
        )
        assert "<execution_directive>" in out
        assert "</execution_directive>" in out
        # routing_key 显式出现
        assert "p2p:abc" in out
        # session 子目录列举
        assert "/workspace/sessions/sid_q/uploads/" in out
        assert "/workspace/sessions/sid_q/outputs/" in out
        assert "/workspace/sessions/sid_q/tmp/" in out

    def test_routing_key_default_when_empty(self, synthetic_registry):
        cache: dict = {}
        out = _get_skill_instructions(
            synthetic_registry, "ref_one",
            session_id="sid", routing_key="", cache=cache,
        )
        assert "<由系统注入>" in out

    def test_cache_returns_same_string(self, synthetic_registry):
        cache: dict = {}
        a = _get_skill_instructions(
            synthetic_registry, "ref_one",
            session_id="sid_c", routing_key="rk", cache=cache,
        )
        b = _get_skill_instructions(
            synthetic_registry, "ref_one",
            session_id="sid_DIFFERENT", routing_key="rk_DIFFERENT", cache=cache,
        )
        # 第二次命中 cache，session_id/routing_key 不会再生效
        assert a == b


# ──────────────────────────────────────────────────────────────────
# _build_description_xml —— XML 头部 + skill 列表
# ──────────────────────────────────────────────────────────────────


class TestBuildDescriptionXml:
    def test_session_dir_in_header(self, synthetic_registry):
        out = _build_description_xml(synthetic_registry, "sid_h")
        assert "/workspace/sessions/sid_h/" in out
        assert "/workspace/sessions/sid_h/uploads/" in out
        assert "/workspace/sessions/sid_h/outputs/" in out

    def test_session_id_empty_uses_placeholder_in_header(self, synthetic_registry):
        out = _build_description_xml(synthetic_registry, "")
        assert "/workspace/sessions/<session_id>/" in out

    def test_xml_marks_available_true_by_default(self, synthetic_registry):
        out = _build_description_xml(synthetic_registry, "sid")
        assert "<available>true</available>" in out
        # 默认不带 unavailable_reason
        assert "<unavailable_reason>" not in out


# ──────────────────────────────────────────────────────────────────
# 集成：仓库内 19 个 enabled skill 必须能 build instruction 不抛错
# ──────────────────────────────────────────────────────────────────


class TestEvopawPlaceholders:
    """在 _get_skill_instructions 路径下，${EVOPAW_*} 新占位符也被替换。"""

    @pytest.fixture
    def registry_with_evopaw_skill(self, tmp_path: Path) -> dict:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "load_skills.yaml").write_text(
            "skills:\n"
            "  - name: ev_one\n    type: reference\n    enabled: true\n",
            encoding="utf-8",
        )
        (skills_dir / "ev_one").mkdir()
        (skills_dir / "ev_one" / "SKILL.md").write_text(
            "---\nname: ev_one\ndescription: ev\ntype: reference\nversion: \"1.0\"\n---\n"
            "Skill: ${EVOPAW_SKILL_NAME}\n"
            "Base: ${EVOPAW_SKILL_BASE}\n"
            "Session: ${EVOPAW_SESSION_ID}\n"
            "Dir: ${EVOPAW_SESSION_DIR}\n"
            "Routing: ${EVOPAW_ROUTING_KEY}\n"
            "Workspace: ${EVOPAW_WORKSPACE_ROOT}\n",
            encoding="utf-8",
        )
        return _build_skill_registry(skills_dir)

    def test_evopaw_placeholders_replaced(self, registry_with_evopaw_skill):
        cache: dict = {}
        out = _get_skill_instructions(
            registry_with_evopaw_skill, "ev_one",
            session_id="sid_n", routing_key="p2p:abc", cache=cache,
        )
        assert "Skill: ev_one" in out
        assert "Base: /mnt/skills/ev_one" in out
        assert "Session: sid_n" in out
        assert "Dir: /workspace/sessions/sid_n" in out
        assert "Routing: p2p:abc" in out
        assert "Workspace: /workspace" in out
        # 不应留下未替换的 ${EVOPAW_*}
        assert "${EVOPAW_" not in out.split("<execution_directive>")[0]


class TestRealRepoSkills:
    def test_all_enabled_skills_build_description(self):
        """仓库内 enabled skill 都能进 description XML，不抛错。"""
        if not _REPO_SKILLS_DIR.exists():
            pytest.skip("repo skills dir not present")
        registry = _build_skill_registry(_REPO_SKILLS_DIR)
        # 当前清单 19 个 enabled skill（含 hk-investment-morning-report）
        assert len(registry) == 19
        out = _build_description_xml(registry, "test_sid")
        assert "<available_skills>" in out
        # /workspace/skills 的错误前提不应出现
        assert "/workspace/skills" not in out

    def test_all_enabled_skills_build_instructions(self):
        """仓库内 enabled skill 的 SKILL.md 替换占位符后不抛错，且替换值正确。"""
        if not _REPO_SKILLS_DIR.exists():
            pytest.skip("repo skills dir not present")
        registry = _build_skill_registry(_REPO_SKILLS_DIR)
        for name in registry:
            cache: dict = {}
            text = _get_skill_instructions(
                registry, name,
                session_id="sid_real", routing_key="p2p:tester", cache=cache,
            )
            # 不应留下未替换的占位符（除非作者故意写双花括号转义，本基线不支持）
            assert "{skill_base}" not in text, f"{name} 残留 {{skill_base}}"
            assert "{_skill_base}" not in text, f"{name} 残留 {{_skill_base}}"
            assert "{session_id}" not in text, f"{name} 残留 {{session_id}}"
            assert "{session_dir}" not in text, f"{name} 残留 {{session_dir}}"
            # 错误前提 /workspace/skills 必须不出现
            assert "/workspace/skills" not in text, f"{name} 仍含 /workspace/skills"
            # execution_directive 必须存在
            assert "<execution_directive>" in text
