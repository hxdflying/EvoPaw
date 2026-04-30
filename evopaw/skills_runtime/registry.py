"""Skill 注册表 —— 解析 load_skills.yaml + SKILL.md frontmatter。

历史：

- Phase 6：仅读取 `load_skills.yaml` 中的 `name/type/enabled`，再检查 `SKILL.md`
  是否存在；缺少依赖（`pandoc`、`soffice`、Tavily key、飞书凭证文件等）问题
  要等 Sub-Agent 启动后才暴露。
- 改造方案 P0-1：在不破坏旧 registry 字段（`type / path`）的前提下，新增
  `available / unavailable_reason / requires / platforms` 元数据；缺依赖
  skill 仍出现在 `<available_skills>` XML 中（让 Main Agent 知道能力存在），
  但 dispatcher 拒绝调用，避免 Sub-Agent 启动后再失败。

下划线前缀名仅出于"原模块测试 import 不破"的兼容考虑；新代码可直接调用。
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# requires/platforms 检查时使用的 workspace 根（Skill 脚本最终运行在容器内 /workspace）。
# Path.exists() 会按当前进程视角解析，本地开发环境（无 /workspace）一定走 false 分支，
# 由 unavailable_reason 解释；容器内则与 Skill 脚本看到的路径一致。
_DEFAULT_WORKSPACE_ROOT = "/workspace"

# P2-1：execution.mode 合法值。SKILL.md 未声明或写了非法值时降级为 foreground，
# 保持向后兼容；只有显式 `execution.mode: background` 才进入后台路径。
_EXECUTION_MODES = frozenset({"foreground", "background"})
_DEFAULT_EXECUTION_MODE = "foreground"


def _parse_execution_mode(front: dict[str, Any]) -> str:
    """从 frontmatter 中提取 execution.mode；非法值降级为 foreground。

    支持两种写法（任选其一，向后兼容更宽松）：

    ```yaml
    execution:
      mode: background
    ```

    解析失败 / 缺失 / 非合法枚举值 → foreground。
    """
    block = front.get("execution") if isinstance(front, dict) else None
    if not isinstance(block, dict):
        return _DEFAULT_EXECUTION_MODE
    mode = block.get("mode")
    if not isinstance(mode, str):
        return _DEFAULT_EXECUTION_MODE
    mode = mode.strip().lower()
    if mode in _EXECUTION_MODES:
        return mode
    logger.warning(
        "未知的 execution.mode=%r，降级为 foreground", mode,
    )
    return _DEFAULT_EXECUTION_MODE


def _extract_frontmatter_description(content: str) -> str:
    """从 SKILL.md 的 YAML frontmatter 中提取 description（最多 200 字符）。"""
    front = _parse_frontmatter(content)
    desc = front.get("description", "") if front else ""
    if not desc:
        return ""
    return desc[:200] + "..." if len(desc) > 200 else desc


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """解析 SKILL.md 顶部的 YAML frontmatter；解析失败返回空 dict。

    与 `_extract_frontmatter_description` 共用同一个正则；调用方按需取字段。
    """
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    try:
        front = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return front if isinstance(front, dict) else {}


def _normalize_platform_token(token: str) -> str:
    """把 frontmatter 里写的平台名归一化为 `linux/darwin/win32`。

    - `linux` / `linux2` → `linux`
    - `darwin` / `macos` / `osx` → `darwin`
    - `windows` / `win` / `win32` → `win32`
    其它原样返回（保持显式失败而不是隐式通过）。
    """
    t = token.strip().lower()
    if t in ("linux", "linux2"):
        return "linux"
    if t in ("darwin", "macos", "osx", "mac"):
        return "darwin"
    if t in ("windows", "win", "win32"):
        return "win32"
    return t


def _current_platform() -> str:
    """返回 `linux/darwin/win32` 之一（与 `_normalize_platform_token` 对齐）。"""
    sysname = platform.system().lower()
    if sysname == "linux":
        return "linux"
    if sysname == "darwin":
        return "darwin"
    if sysname == "windows":
        return "win32"
    return sysname  # 其它（如 freebsd）原样返回


def _check_platforms(platforms: Any) -> tuple[bool, str]:
    """检查 SKILL.md 声明的 `platforms` 列表是否包含当前平台。

    - 缺省（None / [] / 非 list）视为不限平台 → True。
    - 非空 list → 当前平台必须出现在归一化后的列表中。
    """
    if not platforms:
        return True, ""
    if not isinstance(platforms, list):
        return True, ""
    normalized = [_normalize_platform_token(str(p)) for p in platforms]
    current = _current_platform()
    if current in normalized:
        return True, ""
    return False, f"当前平台 {current} 不在支持列表 {normalized} 中"


def _check_requirements(
    requires: Any,
    workspace_root: str = _DEFAULT_WORKSPACE_ROOT,
) -> tuple[bool, str]:
    """检查 frontmatter 中的 `requires.bins/env/files`。

    - `bins`：`shutil.which()` 全部命中。
    - `env`：`os.environ` 中均存在且非空。
    - `files`：所有路径 `Path.exists()`；workspace_root 仅作为参考，不重写绝对路径。

    任意一项失败时返回 `(False, "缺少 ...")`；缺省 requires 视为可用。
    """
    if not requires:
        return True, ""
    if not isinstance(requires, dict):
        return True, ""

    missing_bins: list[str] = []
    for binary in requires.get("bins") or []:
        if not isinstance(binary, str) or not binary.strip():
            continue
        if shutil.which(binary) is None:
            missing_bins.append(binary)
    if missing_bins:
        return False, f"缺少可执行文件 {missing_bins}"

    missing_env: list[str] = []
    for var in requires.get("env") or []:
        if not isinstance(var, str) or not var.strip():
            continue
        if not os.environ.get(var):
            missing_env.append(var)
    if missing_env:
        return False, f"缺少环境变量 {missing_env}"

    missing_files: list[str] = []
    for f in requires.get("files") or []:
        if not isinstance(f, str) or not f.strip():
            continue
        if not Path(f).exists():
            missing_files.append(f)
    if missing_files:
        return False, f"缺少凭证文件 {missing_files}"

    return True, ""


def _build_skill_registry(
    skills_dir: Path,
    *,
    workspace_root: str = _DEFAULT_WORKSPACE_ROOT,
) -> dict[str, dict[str, Any]]:
    """解析 load_skills.yaml + SKILL.md frontmatter，构建 skill 注册表。

    返回字段（每个 skill）：

    - `type`：以 `load_skills.yaml` 为准（避免历史配置突然变更）。
    - `path`：skill 目录绝对路径。
    - `available`：bool，依赖 / 平台检查均通过为 True。
    - `unavailable_reason`：available=False 时的人类可读原因；可用时为空串。
    - `requires`：原 frontmatter 中的 requires dict（缺省为空 dict）。
    - `platforms`：原 frontmatter 中的 platforms list（缺省为空 list）。
    """
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

        # 解析 frontmatter；失败时退化为「无依赖声明」，保持向后兼容
        try:
            front = _parse_frontmatter(skill_md_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning(
                "frontmatter parse failed for skill=%s, treating as no-requires",
                name, exc_info=True,
            )
            front = {}

        requires = front.get("requires") if isinstance(front.get("requires"), dict) else {}
        platforms = front.get("platforms") if isinstance(front.get("platforms"), list) else []

        plat_ok, plat_reason = _check_platforms(platforms)
        req_ok, req_reason = (True, "") if not plat_ok else _check_requirements(
            requires, workspace_root=workspace_root,
        )
        # 平台不匹配优先报告（最先暴露），其次才看 requires
        if not plat_ok:
            available, reason = False, plat_reason
        elif not req_ok:
            available, reason = False, req_reason
        else:
            available, reason = True, ""

        registry[name] = {
            "type": skill_type,
            "path": skill_path,
            "available": available,
            "unavailable_reason": reason,
            "requires": requires,
            "platforms": platforms,
            # P2-1：默认 foreground；显式声明 background 时 dispatcher 会立即返回
            # task_id 提示并把执行 spawn 到 SubAgentRegistry。
            "execution_mode": _parse_execution_mode(front),
        }

    return registry
