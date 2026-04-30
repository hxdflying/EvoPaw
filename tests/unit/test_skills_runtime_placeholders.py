"""skills_runtime.placeholders 单元测试（P1-3）。

覆盖：
- ``${EVOPAW_*}`` 新规约的 8 个占位符替换
- ``{skill_base}/{_skill_base}/{session_id}/{session_dir}`` 旧 alias 仍生效
- 新旧并存时双向替换正确
- 空 session_id / routing_key 退化为 ``<session_id>`` / ``<routing_key>``
- ``${EVOPAW_TODAY}`` 时区固定 Asia/Shanghai
- 替换是字符串级 ``str.replace``，不识别反斜杠转义（基线保护）
"""

from __future__ import annotations

import re

from evopaw.skills_runtime.placeholders import (
    _DEFAULT_TZ,
    _SESSIONS_ROOT,
    _SKILLS_MOUNT,
    _WORKSPACE_ROOT,
    render,
)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Za-z+\-0-9:]+$")


class TestNewPlaceholders:
    def test_skill_name(self):
        out = render(
            "name=${EVOPAW_SKILL_NAME}",
            skill_name="pdf", session_id="sid", routing_key="rk",
        )
        assert out == "name=pdf"

    def test_skill_base_maps_to_mnt_skills(self):
        out = render(
            "base=${EVOPAW_SKILL_BASE}",
            skill_name="pdf", session_id="sid", routing_key="rk",
        )
        assert out == f"base={_SKILLS_MOUNT}/pdf"

    def test_session_id(self):
        out = render(
            "sid=${EVOPAW_SESSION_ID}",
            skill_name="x", session_id="sid_007", routing_key="rk",
        )
        assert out == "sid=sid_007"

    def test_session_dir(self):
        out = render(
            "dir=${EVOPAW_SESSION_DIR}",
            skill_name="x", session_id="sid_007", routing_key="rk",
        )
        assert out == f"dir={_SESSIONS_ROOT}/sid_007"

    def test_routing_key(self):
        out = render(
            "rk=${EVOPAW_ROUTING_KEY}",
            skill_name="x", session_id="sid", routing_key="p2p:abc",
        )
        assert out == "rk=p2p:abc"

    def test_workspace_root(self):
        out = render(
            "root=${EVOPAW_WORKSPACE_ROOT}",
            skill_name="x", session_id="sid", routing_key="rk",
        )
        assert out == f"root={_WORKSPACE_ROOT}"

    def test_today_yyyy_mm_dd(self):
        out = render(
            "${EVOPAW_TODAY}",
            skill_name="x", session_id="sid", routing_key="rk",
        )
        assert _DATE_RE.match(out), f"unexpected today: {out!r}"

    def test_now_includes_tz(self):
        out = render(
            "${EVOPAW_NOW}",
            skill_name="x", session_id="sid", routing_key="rk",
        )
        assert _DATETIME_RE.match(out), f"unexpected now: {out!r}"


class TestLegacyAliases:
    def test_skill_base_alias(self):
        out = render(
            "old={skill_base}",
            skill_name="pdf", session_id="sid", routing_key="rk",
        )
        assert out == f"old={_SKILLS_MOUNT}/pdf"

    def test_underscore_skill_base_alias(self):
        out = render(
            "old={_skill_base}",
            skill_name="pdf", session_id="sid", routing_key="rk",
        )
        assert out == f"old={_SKILLS_MOUNT}/pdf"

    def test_session_id_alias(self):
        out = render(
            "{session_id}",
            skill_name="x", session_id="sid_b", routing_key="rk",
        )
        assert out == "sid_b"

    def test_session_dir_alias(self):
        out = render(
            "{session_dir}",
            skill_name="x", session_id="sid_b", routing_key="rk",
        )
        assert out == f"{_SESSIONS_ROOT}/sid_b"


class TestNewAndLegacyEquivalence:
    """新旧两套语法对同一个值应替换成相同字符串。"""

    def test_skill_base_equivalent(self):
        new = render(
            "${EVOPAW_SKILL_BASE}",
            skill_name="pdf", session_id="sid", routing_key="rk",
        )
        old = render(
            "{skill_base}",
            skill_name="pdf", session_id="sid", routing_key="rk",
        )
        assert new == old

    def test_session_dir_equivalent(self):
        new = render(
            "${EVOPAW_SESSION_DIR}",
            skill_name="x", session_id="sid_q", routing_key="rk",
        )
        old = render(
            "{session_dir}",
            skill_name="x", session_id="sid_q", routing_key="rk",
        )
        assert new == old


class TestEmptyDefaults:
    def test_empty_session_id_uses_placeholder(self):
        out = render(
            "${EVOPAW_SESSION_ID}|${EVOPAW_SESSION_DIR}",
            skill_name="x", session_id="", routing_key="rk",
        )
        assert out == f"<session_id>|{_SESSIONS_ROOT}/<session_id>"

    def test_empty_routing_key_uses_placeholder(self):
        out = render(
            "${EVOPAW_ROUTING_KEY}",
            skill_name="x", session_id="sid", routing_key="",
        )
        assert out == "<routing_key>"

    def test_legacy_session_id_empty_default(self):
        out = render(
            "{session_id}|{session_dir}",
            skill_name="x", session_id="", routing_key="",
        )
        assert out == f"<session_id>|{_SESSIONS_ROOT}/<session_id>"


class TestEscapeNotSupported:
    """基线保护：本版本不支持反斜杠转义。"""

    def test_backslash_does_not_escape(self):
        out = render(
            r"\${EVOPAW_TODAY}",
            skill_name="x", session_id="sid", routing_key="rk",
        )
        # 反斜杠仍在，TODAY 仍被替换为日期
        assert out.startswith("\\")
        assert _DATE_RE.match(out[1:])


class TestDefaultTimezone:
    def test_default_tz_is_asia_shanghai(self):
        # 不依赖系统 TZ：固定 Asia/Shanghai
        assert str(_DEFAULT_TZ) == "Asia/Shanghai"

    def test_custom_tz_kwarg(self):
        from zoneinfo import ZoneInfo

        out = render(
            "${EVOPAW_NOW}",
            skill_name="x", session_id="sid", routing_key="rk",
            tz=ZoneInfo("UTC"),
        )
        # UTC 渲染结果末尾的时区 token 应为 "UTC"
        assert out.endswith(" UTC"), f"unexpected: {out!r}"


class TestNoOverlapBetweenNewAndLegacy:
    """${EVOPAW_*} 与 {skill_base} 在文本中可同时出现，互不污染。"""

    def test_mixed_text(self):
        text = (
            "[new] ${EVOPAW_SKILL_BASE}\n"
            "[old] {skill_base}\n"
            "[mix] ${EVOPAW_SESSION_ID}/{session_id}\n"
        )
        out = render(
            text,
            skill_name="pdf", session_id="sid_x", routing_key="rk",
        )
        assert f"[new] {_SKILLS_MOUNT}/pdf" in out
        assert f"[old] {_SKILLS_MOUNT}/pdf" in out
        assert "[mix] sid_x/sid_x" in out
