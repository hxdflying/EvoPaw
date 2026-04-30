"""skills_runtime.registry 单元测试。

覆盖：

- frontmatter 正常解析与 description 截断
- requires.bins / env / files 缺失时 available=False，原因人类可读
- platforms 不匹配时 available=False
- 缺省 frontmatter（无 requires/platforms）保持向后兼容（available=True）
- 路径穿越防护
- load_skills.yaml 缺失或解析失败返回空 dict
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from evopaw.skills_runtime.registry import (
    _build_skill_registry,
    _check_platforms,
    _check_requirements,
    _extract_frontmatter_description,
    _normalize_platform_token,
    _parse_execution_mode,
    _parse_frontmatter,
)


def _write_skill(
    skills_dir: Path,
    name: str,
    *,
    skill_type: str = "task",
    extra_frontmatter: str = "",
    enabled: bool = True,
) -> None:
    """工具函数：创建 skills/<name>/SKILL.md 并追加 load_skills.yaml 一项。"""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    front = (
        f"---\nname: {name}\ndescription: {name}-desc\ntype: {skill_type}\n"
        f"version: \"1.0\"\n"
    )
    if extra_frontmatter:
        front += extra_frontmatter
        if not front.endswith("\n"):
            front += "\n"
    front += "---\n"
    (skill_dir / "SKILL.md").write_text(front + f"# {name}\n", encoding="utf-8")

    manifest_path = skills_dir / "load_skills.yaml"
    if not manifest_path.exists():
        manifest_path.write_text("skills:\n", encoding="utf-8")
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + f"  - name: {name}\n    type: {skill_type}\n    enabled: {str(enabled).lower()}\n",
        encoding="utf-8",
    )


@pytest.fixture
def empty_skills_dir(tmp_path: Path) -> Path:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "load_skills.yaml").write_text("skills:\n", encoding="utf-8")
    return skills_dir


# ──────────────────────────────────────────────────────────────────
# _normalize_platform_token / _check_platforms
# ──────────────────────────────────────────────────────────────────


class TestPlatformNormalization:
    def test_linux_aliases(self):
        assert _normalize_platform_token("linux") == "linux"
        assert _normalize_platform_token("Linux") == "linux"
        assert _normalize_platform_token("linux2") == "linux"

    def test_darwin_aliases(self):
        assert _normalize_platform_token("darwin") == "darwin"
        assert _normalize_platform_token("MacOS") == "darwin"
        assert _normalize_platform_token("osx") == "darwin"

    def test_windows_aliases(self):
        assert _normalize_platform_token("windows") == "win32"
        assert _normalize_platform_token("Win32") == "win32"

    def test_unknown_passthrough(self):
        # 未知平台原样返回（保持显式失败）
        assert _normalize_platform_token("freebsd") == "freebsd"


class TestCheckPlatforms:
    def test_empty_means_unrestricted(self):
        ok, _ = _check_platforms(None)
        assert ok
        ok, _ = _check_platforms([])
        assert ok

    def test_non_list_treated_as_unrestricted(self):
        ok, _ = _check_platforms("linux")
        assert ok

    def test_match_current_linux(self):
        with patch("evopaw.skills_runtime.registry._current_platform", return_value="linux"):
            ok, reason = _check_platforms(["linux", "darwin"])
        assert ok
        assert reason == ""

    def test_mismatch_reports_reason(self):
        with patch("evopaw.skills_runtime.registry._current_platform", return_value="linux"):
            ok, reason = _check_platforms(["darwin"])
        assert not ok
        assert "linux" in reason
        assert "darwin" in reason


# ──────────────────────────────────────────────────────────────────
# _check_requirements
# ──────────────────────────────────────────────────────────────────


class TestCheckRequirements:
    def test_empty_requires_is_available(self):
        ok, _ = _check_requirements(None)
        assert ok
        ok, _ = _check_requirements({})
        assert ok

    def test_non_dict_requires_is_available(self):
        # frontmatter 写错（写成 list 等）时不应误报，按可用处理
        ok, _ = _check_requirements(["python3"])
        assert ok

    def test_missing_bin_reports(self):
        with patch("evopaw.skills_runtime.registry.shutil.which", return_value=None):
            ok, reason = _check_requirements({"bins": ["nonexistent_xyz"]})
        assert not ok
        assert "nonexistent_xyz" in reason
        assert "可执行文件" in reason

    def test_present_bin_passes(self):
        with patch("evopaw.skills_runtime.registry.shutil.which", return_value="/usr/bin/python3"):
            ok, reason = _check_requirements({"bins": ["python3"]})
        assert ok
        assert reason == ""

    def test_missing_env_reports(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("EVOPAW_TEST_VAR", raising=False)
        ok, reason = _check_requirements({"env": ["EVOPAW_TEST_VAR"]})
        assert not ok
        assert "EVOPAW_TEST_VAR" in reason
        assert "环境变量" in reason

    def test_empty_string_env_treated_as_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EVOPAW_TEST_VAR", "")
        ok, _ = _check_requirements({"env": ["EVOPAW_TEST_VAR"]})
        assert not ok

    def test_present_env_passes(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EVOPAW_TEST_VAR", "value")
        ok, _ = _check_requirements({"env": ["EVOPAW_TEST_VAR"]})
        assert ok

    def test_missing_files_reports(self, tmp_path: Path):
        ok, reason = _check_requirements(
            {"files": [str(tmp_path / "nonexistent.json")]},
        )
        assert not ok
        assert "凭证文件" in reason

    def test_existing_files_passes(self, tmp_path: Path):
        f = tmp_path / "creds.json"
        f.write_text("{}", encoding="utf-8")
        ok, _ = _check_requirements({"files": [str(f)]})
        assert ok

    def test_blank_entries_ignored(self):
        # 空字符串 / 仅空白条目应被忽略，不影响检查结果
        ok, _ = _check_requirements({"bins": ["", "   "], "env": [""], "files": [""]})
        assert ok

    def test_mixed_failure_reports_first_kind(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        # bins 缺失优先被报告（实现里检查顺序：bins → env → files）
        monkeypatch.delenv("EVOPAW_TEST_VAR", raising=False)
        with patch("evopaw.skills_runtime.registry.shutil.which", return_value=None):
            ok, reason = _check_requirements(
                {"bins": ["nope"], "env": ["EVOPAW_TEST_VAR"]},
            )
        assert not ok
        assert "nope" in reason


# ──────────────────────────────────────────────────────────────────
# _parse_frontmatter / _extract_frontmatter_description
# ──────────────────────────────────────────────────────────────────


class TestParseFrontmatter:
    def test_standard_frontmatter(self):
        content = "---\nname: x\nrequires:\n  bins: [a]\n---\nbody"
        front = _parse_frontmatter(content)
        assert front["name"] == "x"
        assert front["requires"] == {"bins": ["a"]}

    def test_missing_frontmatter_returns_empty(self):
        assert _parse_frontmatter("# Just markdown") == {}

    def test_invalid_yaml_returns_empty(self):
        # 故意写错（unclosed 单引号）
        bad = "---\nname: 'unterminated\n---\nbody"
        assert _parse_frontmatter(bad) == {}

    def test_non_dict_yaml_returns_empty(self):
        # frontmatter 解析后是 list/str，按空 dict 处理
        front = _parse_frontmatter("---\n- a\n- b\n---\n")
        assert front == {}


class TestExtractDescription:
    def test_short_description(self):
        content = "---\nname: x\ndescription: hello\n---\n"
        assert _extract_frontmatter_description(content) == "hello"

    def test_long_description_truncated(self):
        long_desc = "x" * 250
        content = f"---\nname: x\ndescription: {long_desc}\n---\n"
        out = _extract_frontmatter_description(content)
        assert len(out) == 203  # 200 + "..."
        assert out.endswith("...")

    def test_missing_description_returns_empty(self):
        assert _extract_frontmatter_description("---\nname: x\n---\n") == ""


# ──────────────────────────────────────────────────────────────────
# _build_skill_registry —— 与 frontmatter / requires 集成
# ──────────────────────────────────────────────────────────────────


class TestBuildRegistry:
    def test_minimal_skill_available(self, empty_skills_dir: Path):
        _write_skill(empty_skills_dir, "simple")
        reg = _build_skill_registry(empty_skills_dir)
        assert "simple" in reg
        info = reg["simple"]
        assert info["type"] == "task"
        assert info["available"] is True
        assert info["unavailable_reason"] == ""
        assert info["requires"] == {}
        assert info["platforms"] == []

    def test_disabled_skill_skipped(self, empty_skills_dir: Path):
        _write_skill(empty_skills_dir, "off", enabled=False)
        reg = _build_skill_registry(empty_skills_dir)
        assert "off" not in reg

    def test_missing_skill_md_skipped(self, empty_skills_dir: Path):
        # 在 manifest 里登记，但不创建 SKILL.md
        manifest_path = empty_skills_dir / "load_skills.yaml"
        manifest_path.write_text(
            "skills:\n  - name: ghost\n    type: task\n    enabled: true\n",
            encoding="utf-8",
        )
        reg = _build_skill_registry(empty_skills_dir)
        assert "ghost" not in reg

    def test_requires_files_missing_marks_unavailable(
        self, empty_skills_dir: Path, tmp_path: Path,
    ):
        nonexistent = tmp_path / "creds.json"
        _write_skill(
            empty_skills_dir, "needy",
            extra_frontmatter=f"requires:\n  files:\n    - \"{nonexistent}\"\n",
        )
        reg = _build_skill_registry(empty_skills_dir)
        assert reg["needy"]["available"] is False
        assert "凭证文件" in reg["needy"]["unavailable_reason"]

    def test_requires_env_missing_marks_unavailable(
        self, empty_skills_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.delenv("EVOPAW_REGISTRY_TEST", raising=False)
        _write_skill(
            empty_skills_dir, "envneed",
            extra_frontmatter="requires:\n  env:\n    - EVOPAW_REGISTRY_TEST\n",
        )
        reg = _build_skill_registry(empty_skills_dir)
        assert reg["envneed"]["available"] is False
        assert "EVOPAW_REGISTRY_TEST" in reg["envneed"]["unavailable_reason"]

    def test_requires_bins_missing_marks_unavailable(self, empty_skills_dir: Path):
        _write_skill(
            empty_skills_dir, "binneed",
            extra_frontmatter="requires:\n  bins:\n    - this_bin_does_not_exist_xyz\n",
        )
        with patch("evopaw.skills_runtime.registry.shutil.which", return_value=None):
            reg = _build_skill_registry(empty_skills_dir)
        assert reg["binneed"]["available"] is False
        assert "this_bin_does_not_exist_xyz" in reg["binneed"]["unavailable_reason"]

    def test_platform_mismatch_marks_unavailable(self, empty_skills_dir: Path):
        _write_skill(
            empty_skills_dir, "wrongplat",
            extra_frontmatter="platforms:\n  - darwin\n",
        )
        with patch(
            "evopaw.skills_runtime.registry._current_platform", return_value="linux",
        ):
            reg = _build_skill_registry(empty_skills_dir)
        assert reg["wrongplat"]["available"] is False
        assert "linux" in reg["wrongplat"]["unavailable_reason"]

    def test_platform_mismatch_takes_priority_over_requires(
        self, empty_skills_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        # 同时 platform 不匹配 + env 缺失：应优先报 platform，避免无意义检查
        monkeypatch.delenv("EVOPAW_PRIO_TEST", raising=False)
        _write_skill(
            empty_skills_dir, "both",
            extra_frontmatter=(
                "platforms:\n  - darwin\nrequires:\n  env:\n    - EVOPAW_PRIO_TEST\n"
            ),
        )
        with patch(
            "evopaw.skills_runtime.registry._current_platform", return_value="linux",
        ):
            reg = _build_skill_registry(empty_skills_dir)
        assert reg["both"]["available"] is False
        # 平台原因应出现，env 名不应出现（短路）
        assert "linux" in reg["both"]["unavailable_reason"]
        assert "EVOPAW_PRIO_TEST" not in reg["both"]["unavailable_reason"]

    def test_path_traversal_blocked(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "load_skills.yaml").write_text(
            "skills:\n  - name: ../escape\n    type: task\n    enabled: true\n",
            encoding="utf-8",
        )
        # 不应注册，也不应抛错
        reg = _build_skill_registry(skills_dir)
        assert reg == {}

    def test_missing_manifest_returns_empty(self, tmp_path: Path):
        empty = tmp_path / "no_manifest"
        empty.mkdir()
        assert _build_skill_registry(empty) == {}

    def test_corrupt_frontmatter_treated_as_no_requires(
        self, empty_skills_dir: Path,
    ):
        # 创建 SKILL.md 但 frontmatter YAML 故意写错
        skill_dir = empty_skills_dir / "corrupt"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: 'unterminated\n---\nbody",
            encoding="utf-8",
        )
        (empty_skills_dir / "load_skills.yaml").write_text(
            "skills:\n  - name: corrupt\n    type: task\n    enabled: true\n",
            encoding="utf-8",
        )
        reg = _build_skill_registry(empty_skills_dir)
        # 仍然注册，按可用处理（向后兼容 / 无依赖声明）
        assert "corrupt" in reg
        assert reg["corrupt"]["available"] is True


# ──────────────────────────────────────────────────────────────────
# execution.mode 解析
# ──────────────────────────────────────────────────────────────────


class TestParseExecutionMode:
    def test_default_when_missing(self):
        assert _parse_execution_mode({}) == "foreground"

    def test_default_when_block_not_dict(self):
        assert _parse_execution_mode({"execution": "background"}) == "foreground"

    def test_default_when_mode_missing(self):
        assert _parse_execution_mode({"execution": {}}) == "foreground"

    def test_default_when_mode_not_string(self):
        assert _parse_execution_mode({"execution": {"mode": 123}}) == "foreground"

    def test_explicit_foreground(self):
        assert _parse_execution_mode({"execution": {"mode": "foreground"}}) == "foreground"

    def test_explicit_background(self):
        assert _parse_execution_mode({"execution": {"mode": "background"}}) == "background"

    def test_case_and_whitespace_normalized(self):
        assert _parse_execution_mode({"execution": {"mode": " Background  "}}) == "background"

    def test_unknown_mode_falls_back_to_foreground(self):
        assert _parse_execution_mode({"execution": {"mode": "async"}}) == "foreground"


class TestRegistryExecutionMode:
    def test_default_execution_mode_is_foreground(self, empty_skills_dir: Path):
        _write_skill(empty_skills_dir, "default_mode")
        reg = _build_skill_registry(empty_skills_dir)
        assert reg["default_mode"]["execution_mode"] == "foreground"

    def test_explicit_background_recorded(self, empty_skills_dir: Path):
        _write_skill(
            empty_skills_dir, "bg_skill",
            extra_frontmatter="execution:\n  mode: background\n",
        )
        reg = _build_skill_registry(empty_skills_dir)
        assert reg["bg_skill"]["execution_mode"] == "background"

    def test_invalid_mode_falls_back_to_foreground(self, empty_skills_dir: Path):
        _write_skill(
            empty_skills_dir, "weird_mode",
            extra_frontmatter="execution:\n  mode: weirdo\n",
        )
        reg = _build_skill_registry(empty_skills_dir)
        assert reg["weird_mode"]["execution_mode"] == "foreground"
