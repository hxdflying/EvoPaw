"""resolve_runtime —— 角色到 ResolvedRuntime 的解析器。

解析优先级（从高到低）：
  1. 显式参数（`overrides=`，主要用于测试）
  2. config.yaml 的 `roles.{role}` 块
  3. 旧字段兼容（`agent.planner_model` → roles.main.model 等）
  4. 环境变量回退（`EVOPAW_MEMORY_SUMMARY_MODEL` 等）
  5. ProviderSpec.default_model / default_api_base
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from .capabilities import supports_vision as _capability_supports_vision
from .models import ProviderSpec, ResolvedRuntime, RoleConfig
from .registry import build_registry, get_provider

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 角色 → 默认 provider 绑定
# ──────────────────────────────────────────────────────────────────
#
# 用户可在 config.yaml 写 `roles.main = {provider: anthropic, model: ...}` 覆盖默认绑定。

DEFAULT_ROLE_BINDINGS: dict[str, str] = {
    "main": "claude_sdk",
    "subagent": "claude_sdk",
    "memory_summary": "dashscope",
    "memory_embedding": "dashscope",
    "memory_extract": "dashscope",
    # ASR / vision / fallback 留位，本阶段不强制
}


# ──────────────────────────────────────────────────────────────────
# 角色 → 默认模型
# ──────────────────────────────────────────────────────────────────
#
# 同一 provider 下不同角色的默认模型不同（如 dashscope 的 chat / embedding
# 是两个完全不同的模型），所以 ProviderSpec.default_model 不足以表达。
# 这里的 default 只在「角色仍走默认 provider」时生效——用户切换 provider
# 后会让位给 spec.default_model（避免把 Claude 默认模型套到非 Claude provider）。

DEFAULT_ROLE_MODELS: dict[str, str] = {
    "main":             "claude-sonnet-4-6",
    "subagent":         "claude-haiku-4-5",
    "memory_summary":   "qwen3-turbo",
    "memory_embedding": "text-embedding-v3",
    "memory_extract":   "qwen3-max",
}


# ──────────────────────────────────────────────────────────────────
# 旧字段兼容（agent.planner_model / agent.sub_agent_model）
# ──────────────────────────────────────────────────────────────────
#
# 启动期会发 deprecation warning（仅一次），鼓励用户迁移到 roles.main / roles.subagent。

_LEGACY_ROLE_FIELDS: dict[str, str] = {
    "main": "planner_model",
    "subagent": "sub_agent_model",
}

_LEGACY_WARNED: set[str] = set()


def _warn_once_legacy(role: str, legacy_key: str) -> None:
    """同一进程内每个旧字段只 warn 一次，避免日志泛滥。"""
    if legacy_key in _LEGACY_WARNED:
        return
    _LEGACY_WARNED.add(legacy_key)
    logger.warning(
        "config.yaml: agent.%s 已被弃用，请改用 roles.%s。"
        " 当前仍按旧字段读取，预计在两个发布周期后移除。",
        legacy_key, role,
    )


# ──────────────────────────────────────────────────────────────────
# 环境变量回退
# ──────────────────────────────────────────────────────────────────
#
# resolver 从这里读取模型名后，memory 模块只依赖 ResolvedRuntime.model。

_ROLE_ENV_MODEL_FALLBACK: dict[str, str] = {
    "memory_summary": "EVOPAW_MEMORY_SUMMARY_MODEL",
    "memory_embedding": "EVOPAW_MEMORY_EMBED_MODEL",
    "memory_extract": "EVOPAW_MEMORY_EXTRACT_MODEL",
}

_ENV_WARNED: set[str] = set()


def _warn_once_env(env_key: str) -> None:
    if env_key in _ENV_WARNED:
        return
    _ENV_WARNED.add(env_key)
    logger.info(
        "%s 仍可作为模型名回退使用；推荐改写到 config.yaml 的 roles 块统一管理。",
        env_key,
    )


# ──────────────────────────────────────────────────────────────────
# 异常
# ──────────────────────────────────────────────────────────────────


class ResolveError(RuntimeError):
    """角色无法解析（找不到 provider / 缺关键字段时）。"""


# ──────────────────────────────────────────────────────────────────
# 主入口：resolve_runtime
# ──────────────────────────────────────────────────────────────────


def _parse_role_config(raw: Mapping | None) -> RoleConfig:
    if raw is None:
        return RoleConfig()
    if not isinstance(raw, Mapping):
        raise ValueError(f"roles 配置必须是 mapping，实际为 {type(raw).__name__}")
    return RoleConfig(**dict(raw))


def _legacy_model_for_role(role: str, agent_cfg: Mapping) -> str | None:
    """从 `agent.{planner_model|sub_agent_model}` 取值；找到则发 deprecation warning。"""
    legacy_key = _LEGACY_ROLE_FIELDS.get(role)
    if legacy_key is None:
        return None
    val = agent_cfg.get(legacy_key)
    if val:
        _warn_once_legacy(role, legacy_key)
        return str(val)
    return None


def _filter_extra_body(
    raw: Mapping[str, Any] | None,
    spec: ProviderSpec,
    *,
    role: str,
) -> dict[str, Any]:
    """按 ProviderSpec.extra_body_whitelist 过滤；白名单外字段记 warning 并丢弃。

    传入 None / 空 dict 返回 {}；非 mapping 抛 ValueError（与 RoleConfig 校验一致）。
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"roles.{role}.extra_body 必须是 mapping，实际为 {type(raw).__name__}"
        )
    if not raw:
        return {}

    whitelist = spec.extra_body_whitelist
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    for k, v in raw.items():
        if k in whitelist:
            kept[str(k)] = v
        else:
            dropped.append(str(k))
    if dropped:
        logger.warning(
            "roles.%s.extra_body 中以下字段不在 provider %r 的白名单内，已丢弃: %s",
            role, spec.provider_id, sorted(dropped),
        )
    return kept


def _env_model_for_role(role: str) -> str | None:
    env_key = _ROLE_ENV_MODEL_FALLBACK.get(role)
    if env_key is None:
        return None
    val = os.getenv(env_key, "")
    if val:
        _warn_once_env(env_key)
        return val
    return None


def resolve_runtime(
    role: str,
    app_config: Mapping,
    *,
    overrides: Mapping | None = None,
    registry: Mapping[str, ProviderSpec] | None = None,
) -> ResolvedRuntime:
    """解析单个角色，返回 ResolvedRuntime。

    Args:
        role: 角色名，如 'main' / 'memory_summary'。
        app_config: 完整 config.yaml dict（顶层包含 `agent / providers / roles`）。
        overrides: 显式参数（最高优先级，主要用于测试或运行时切换）。
                   形如 {"provider": "anthropic", "model": "claude-sonnet-4-6"}。
        registry: 已构建的 registry；不传则用 app_config 实时构建。

    Raises:
        ResolveError: 当 provider 无法确定，或最终 model 仍为空时。

    优先级链（首个非空值生效）：
        1. overrides
        2. roles.{role}
        3. agent.{planner_model|sub_agent_model}（仅当 provider 仍是该 role 的
           默认绑定时生效；用户显式切到非默认 provider 后不再继承旧字段）
        4. EVOPAW_MEMORY_*_MODEL（memory 系列）
        5. DEFAULT_ROLE_MODELS[role]（仅在 provider 仍是默认绑定时生效）
        6. provider.default_model

    extra_body 单独走两段链（overrides > role_cfg.extra_body），并按
    `spec.extra_body_whitelist` 过滤；白名单外字段会被丢弃并 warning。
    """
    overrides = overrides or {}

    # 1) registry
    if registry is None:
        registry = build_registry(app_config.get("providers"))

    # 2) 解析 role 配置
    roles_cfg = app_config.get("roles") or {}
    role_cfg = _parse_role_config(roles_cfg.get(role))

    # 3) 选 provider_id（overrides > role.provider > 默认绑定）
    default_provider_id = DEFAULT_ROLE_BINDINGS.get(role)
    provider_id = (
        overrides.get("provider")
        or role_cfg.provider
        or default_provider_id
    )
    if not provider_id:
        raise ResolveError(
            f"角色 {role!r} 无法确定 provider：roles.{role}.provider 与 "
            f"DEFAULT_ROLE_BINDINGS 都未定义。"
        )
    spec = get_provider(registry, provider_id)

    # 4) 解析 model
    agent_cfg = app_config.get("agent") or {}

    # 用户显式切换到非默认 provider 后，legacy 字段（agent.planner_model 等）
    # 与 DEFAULT_ROLE_MODELS 都不应再生效——它们都绑定在「默认 provider」语义上
    # （Claude 模型名套到 moonshot/qwen 上必然 400）。
    using_default_provider = (
        default_provider_id is not None and provider_id == default_provider_id
    )
    legacy_model = (
        _legacy_model_for_role(role, agent_cfg) if using_default_provider else None
    )
    role_default_model = (
        DEFAULT_ROLE_MODELS.get(role) if using_default_provider else None
    )

    model = (
        overrides.get("model")
        or role_cfg.model
        or legacy_model
        or _env_model_for_role(role)
        or role_default_model
        or spec.default_model
    )
    if not model:
        raise ResolveError(
            f"角色 {role!r} 无法确定 model：roles.{role}.model / "
            f"agent.{_LEGACY_ROLE_FIELDS.get(role, '?')} / "
            f"{_ROLE_ENV_MODEL_FALLBACK.get(role, '?')} 均为空，"
            f"且 provider {provider_id!r} 没有 default_model。"
        )

    # 5) 解析 api_base
    api_base = (
        overrides.get("api_base")
        or role_cfg.api_base
        or spec.default_api_base
    )

    # 6) 解析 api_key
    api_key_env = role_cfg.api_key_env or spec.api_key_env
    api_key = os.getenv(api_key_env, "") if api_key_env else ""
    api_key = api_key or None  # 空字符串归一化为 None

    # 7) 解析 extra_body：overrides > role_cfg.extra_body > spec.default_extra_body。
    #    role 配置与 provider 默认按字段级合并（role 覆盖 provider 同名字段），
    #    再按 spec.extra_body_whitelist 过滤。
    if "extra_body" in overrides:
        raw_extra: Mapping[str, Any] | None = overrides.get("extra_body")
    else:
        merged: dict[str, Any] = {}
        merged.update(spec.default_extra_body)
        if role_cfg.extra_body:
            merged.update(role_cfg.extra_body)
        raw_extra = merged or None
    extra_body = _filter_extra_body(raw_extra, spec, role=role)

    return ResolvedRuntime(
        role=role,
        provider_id=provider_id,
        runtime_family=spec.runtime_family,
        model=str(model),
        api_base=api_base,
        api_key=api_key,
        extra_body=extra_body,
        supports_vision=_capability_supports_vision(spec),
    )
