"""SKILL.md 占位符渲染。

集中管理新旧两套占位符语法，对 SKILL.md 文本做字符串替换：

新规约（推荐）：``${EVOPAW_*}`` 前缀，避免和 shell 环境变量混淆。

| 占位符 | 替换值 |
|---|---|
| ``${EVOPAW_SKILL_NAME}``     | skill 名称 |
| ``${EVOPAW_SKILL_BASE}``     | ``/mnt/skills/<name>`` |
| ``${EVOPAW_SESSION_ID}``     | 当前 session_id（缺省 ``<session_id>``） |
| ``${EVOPAW_SESSION_DIR}``    | ``/workspace/sessions/<sid>``（缺省 ``/workspace/sessions/<session_id>``） |
| ``${EVOPAW_ROUTING_KEY}``    | 当前 routing_key（缺省 ``<routing_key>``） |
| ``${EVOPAW_WORKSPACE_ROOT}`` | ``/workspace`` |
| ``${EVOPAW_TODAY}``          | ``YYYY-MM-DD``（``Asia/Shanghai`` 时区） |
| ``${EVOPAW_NOW}``            | ``YYYY-MM-DD HH:MM:SS TZ``（``Asia/Shanghai`` 时区） |

旧 alias（兼容历史 SKILL.md，新写 SKILL.md 优先用新规约）：

| 旧 | 等价新 |
|---|---|
| ``{skill_base}``  | ``${EVOPAW_SKILL_BASE}`` |
| ``{_skill_base}`` | ``${EVOPAW_SKILL_BASE}`` |
| ``{session_id}``  | ``${EVOPAW_SESSION_ID}`` |
| ``{session_dir}`` | ``${EVOPAW_SESSION_DIR}`` |

替换是一次性、字符串级 ``str.replace``，不识别转义。当前实现不支持
``\\${EVOPAW_TODAY}`` 这类反斜杠转义。
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

# ── 路径常量 ────────────────────────────────────────────────────
# 容器内 skill 资源挂载点（注意：不是 /workspace/skills，那是历史文档错误前提）
_SKILLS_MOUNT = "/mnt/skills"
_WORKSPACE_ROOT = "/workspace"
_SESSIONS_ROOT = f"{_WORKSPACE_ROOT}/sessions"

# ── 时区 ────────────────────────────────────────────────────────
# ${EVOPAW_TODAY} / ${EVOPAW_NOW} 的渲染时区。固定为 Asia/Shanghai，与服务器
# 系统时间无关（避免 UTC 容器被误判为东八区）。如需扩展为可配置，新增
# render(..., tz=ZoneInfo("...")) kwarg；不要让"系统时区"成为隐式默认。
_DEFAULT_TZ = ZoneInfo("Asia/Shanghai")


def _resolve_session_id(session_id: str) -> str:
    return session_id or "<session_id>"


def _resolve_session_dir(session_id: str) -> str:
    if session_id:
        return f"{_SESSIONS_ROOT}/{session_id}"
    return f"{_SESSIONS_ROOT}/<session_id>"


def _resolve_routing_key(routing_key: str) -> str:
    return routing_key or "<routing_key>"


def _resolve_skill_base(skill_name: str) -> str:
    return f"{_SKILLS_MOUNT}/{skill_name}"


def _today_str(tz: ZoneInfo = _DEFAULT_TZ) -> str:
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _now_str(tz: ZoneInfo = _DEFAULT_TZ) -> str:
    return datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def render(
    text: str,
    *,
    skill_name: str,
    session_id: str,
    routing_key: str,
    tz: ZoneInfo | None = None,
) -> str:
    """对 SKILL.md 文本做占位符替换（新旧规约共存）。

    Args:
        text: 已剥离 frontmatter 的 SKILL.md 正文。
        skill_name: skill 名称，决定 ``${EVOPAW_SKILL_BASE}``。
        session_id: 当前 session_id；空字符串时 SESSION_ID/SESSION_DIR
            退化为 ``<session_id>`` 占位。
        routing_key: 当前 routing_key；空字符串时退化为 ``<routing_key>``。
        tz: ``${EVOPAW_TODAY}`` / ``${EVOPAW_NOW}`` 的时区。缺省
            ``Asia/Shanghai``。

    Returns:
        替换后的字符串。未出现的占位符不会报错。
    """
    use_tz = tz or _DEFAULT_TZ
    skill_base = _resolve_skill_base(skill_name)
    session_dir = _resolve_session_dir(session_id)
    sid = _resolve_session_id(session_id)
    rkey = _resolve_routing_key(routing_key)
    today = _today_str(use_tz)
    now = _now_str(use_tz)

    # 替换顺序：先替新规约，再替旧 alias，避免 alias 误中新规约的子串。
    # （目前不存在重叠，但留个稳定顺序便于扩展。）
    pairs: list[tuple[str, str]] = [
        # 新规约
        ("${EVOPAW_SKILL_NAME}", skill_name),
        ("${EVOPAW_SKILL_BASE}", skill_base),
        ("${EVOPAW_SESSION_ID}", sid),
        ("${EVOPAW_SESSION_DIR}", session_dir),
        ("${EVOPAW_ROUTING_KEY}", rkey),
        ("${EVOPAW_WORKSPACE_ROOT}", _WORKSPACE_ROOT),
        ("${EVOPAW_TODAY}", today),
        ("${EVOPAW_NOW}", now),
        # 旧 alias
        ("{skill_base}", skill_base),
        ("{_skill_base}", skill_base),
        ("{session_id}", sid),
        ("{session_dir}", session_dir),
    ]
    for needle, value in pairs:
        text = text.replace(needle, value)
    return text
