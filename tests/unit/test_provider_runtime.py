"""provider_runtime 单元测试。

覆盖：
  - registry 加载 / 校验 / 合并
  - capabilities 查询
  - resolver 优先级链（overrides > role > legacy > env > spec.default_model）
  - deprecated 字段兼容（仅写 agent.planner_model 仍能解析为 roles.main）
  - api_key 注入与脱敏
"""

from __future__ import annotations

import logging

import pytest

from evopaw.provider_runtime import (
    DEFAULT_PROVIDERS,
    ProviderSpec,
    ResolveError,
    ResolvedRuntime,
    build_registry,
    get_provider,
    list_providers,
    resolve_runtime,
)
from evopaw.provider_runtime.capabilities import (
    supports_prompt_caching,
    supports_streaming,
    supports_tool_calls,
    supports_vision,
)
from evopaw.provider_runtime import resolve as resolve_module


# ── helpers ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_warn_caches():
    """每个测试前清空 warn-once 集合，确保独立。"""
    resolve_module._LEGACY_WARNED.clear()
    resolve_module._ENV_WARNED.clear()
    yield


# ── DEFAULT_PROVIDERS ────────────────────────────────────────────


class TestDefaultProviders:
    def test_has_three_providers(self):
        assert set(DEFAULT_PROVIDERS) == {"claude_sdk", "anthropic", "dashscope"}

    def test_claude_sdk_uses_claude_sdk_compat_family(self):
        assert DEFAULT_PROVIDERS["claude_sdk"].runtime_family == "claude_sdk_compat"

    def test_anthropic_uses_anthropic_messages_family(self):
        assert DEFAULT_PROVIDERS["anthropic"].runtime_family == "anthropic_messages"

    def test_dashscope_uses_openai_chat_family(self):
        assert DEFAULT_PROVIDERS["dashscope"].runtime_family == "openai_chat"

    def test_dashscope_default_base_is_dashscope_compat_endpoint(self):
        assert DEFAULT_PROVIDERS["dashscope"].default_api_base.endswith(
            "/compatible-mode/v1"
        )

    def test_anthropic_default_base_is_official(self):
        assert DEFAULT_PROVIDERS["anthropic"].default_api_base == "https://api.anthropic.com"

    def test_dashscope_extra_body_whitelist_contains_enable_thinking(self):
        assert "enable_thinking" in DEFAULT_PROVIDERS["dashscope"].extra_body_whitelist

    def test_provider_spec_is_frozen(self):
        spec = DEFAULT_PROVIDERS["claude_sdk"]
        with pytest.raises(Exception):  # ValidationError or TypeError, frozen=True
            spec.provider_id = "modified"


# ── build_registry ──────────────────────────────────────────────


class TestBuildRegistry:
    def test_empty_config_returns_defaults(self):
        registry = build_registry(None)
        assert set(registry) == set(DEFAULT_PROVIDERS)

    def test_empty_dict_returns_defaults(self):
        registry = build_registry({})
        assert set(registry) == set(DEFAULT_PROVIDERS)

    def test_override_existing_provider_default_model(self):
        registry = build_registry({"dashscope": {"default_model": "qwen3-max"}})
        assert registry["dashscope"].default_model == "qwen3-max"
        # 其它字段保持默认
        assert registry["dashscope"].runtime_family == "openai_chat"

    def test_override_does_not_break_other_providers(self):
        registry = build_registry({"dashscope": {"default_model": "qwen3-max"}})
        assert registry["claude_sdk"].default_model == DEFAULT_PROVIDERS["claude_sdk"].default_model

    def test_add_new_provider(self):
        registry = build_registry({
            "moonshot": {
                "runtime_family": "openai_chat",
                "api_key_env": "MOONSHOT_API_KEY",
                "default_api_base": "https://api.moonshot.cn/v1",
                "default_model": "moonshot-v1-32k",
            }
        })
        assert "moonshot" in registry
        assert registry["moonshot"].api_key_env == "MOONSHOT_API_KEY"

    def test_new_provider_missing_runtime_family_raises(self):
        with pytest.raises(ValueError, match="runtime_family"):
            build_registry({"unknown": {"default_model": "x"}})

    def test_invalid_provider_value_type_raises(self):
        with pytest.raises(ValueError, match="必须是 mapping"):
            build_registry({"dashscope": "not-a-mapping"})

    def test_extra_body_whitelist_accepts_list_input(self):
        registry = build_registry({
            "dashscope": {"extra_body_whitelist": ["enable_thinking", "x"]}
        })
        assert registry["dashscope"].extra_body_whitelist == frozenset(
            {"enable_thinking", "x"}
        )

    def test_get_provider_unknown_raises_with_listing(self):
        registry = build_registry({})
        with pytest.raises(KeyError, match="未知 provider_id"):
            get_provider(registry, "no-such-provider")

    def test_list_providers_sorted(self):
        registry = build_registry({})
        names = list_providers(registry)
        assert names == sorted(names)


# ── capabilities ────────────────────────────────────────────────


class TestCapabilities:
    def test_claude_sdk_supports_streaming(self):
        assert supports_streaming(DEFAULT_PROVIDERS["claude_sdk"]) is True

    def test_dashscope_does_not_support_vision(self):
        # ProviderSpec 显式 supports_vision=False；family default 也是 False
        assert supports_vision(DEFAULT_PROVIDERS["dashscope"]) is False

    def test_anthropic_supports_prompt_caching(self):
        assert supports_prompt_caching(DEFAULT_PROVIDERS["anthropic"]) is True

    def test_dashscope_does_not_support_prompt_caching(self):
        assert supports_prompt_caching(DEFAULT_PROVIDERS["dashscope"]) is False

    def test_capability_is_logical_and_of_spec_and_family(self):
        # 强制 ProviderSpec 关掉 streaming 时，supports_streaming 必须为 False
        spec = ProviderSpec(
            provider_id="x",
            runtime_family="claude_sdk_compat",
            supports_streaming=False,
        )
        assert supports_streaming(spec) is False

    def test_tool_calls_default_supported(self):
        for name in ("claude_sdk", "anthropic", "dashscope"):
            assert supports_tool_calls(DEFAULT_PROVIDERS[name]) is True

    def test_vision_explicit_true_overrides_family_default(self):
        # openai_chat family default vision=False，
        # 但 ProviderSpec.supports_vision=True 应显式覆盖。
        spec = ProviderSpec(
            provider_id="custom-vision",
            runtime_family="openai_chat",
            supports_vision=True,
        )
        assert supports_vision(spec) is True

    def test_vision_none_falls_back_to_family_default(self):
        # 不写时按 family default：openai_chat=False, claude_sdk_compat=True。
        oai = ProviderSpec(provider_id="x", runtime_family="openai_chat")
        anth = ProviderSpec(provider_id="y", runtime_family="anthropic_messages")
        assert supports_vision(oai) is False
        assert supports_vision(anth) is True

    def test_resolved_runtime_carries_vision_capability(self):
        # main_agent 直接读 ResolvedRuntime.supports_vision 决定是否构造 image block。
        rt = resolve_runtime("memory_summary", {})
        assert rt.provider_id == "dashscope"
        assert rt.supports_vision is False
        rt_main = resolve_runtime("main", {})
        assert rt_main.supports_vision is True


# ── resolve_runtime: 优先级链 ───────────────────────────────────


class TestResolverPriority:
    def test_explicit_overrides_beats_everything(self, monkeypatch):
        cfg = {
            "agent": {"planner_model": "legacy-model"},
            "roles": {"main": {"provider": "claude_sdk", "model": "role-model"}},
        }
        monkeypatch.setenv("EVOPAW_MEMORY_SUMMARY_MODEL", "env-model")
        rt = resolve_runtime(
            "main",
            cfg,
            overrides={"provider": "anthropic", "model": "override-model"},
        )
        assert rt.provider_id == "anthropic"
        assert rt.model == "override-model"
        assert rt.runtime_family == "anthropic_messages"

    def test_role_config_beats_legacy_field(self):
        cfg = {
            "agent": {"planner_model": "legacy-model"},
            "roles": {"main": {"provider": "claude_sdk", "model": "role-model"}},
        }
        rt = resolve_runtime("main", cfg)
        assert rt.model == "role-model"

    def test_legacy_planner_model_resolves_main(self):
        cfg = {"agent": {"planner_model": "claude-sonnet-4-6"}}
        rt = resolve_runtime("main", cfg)
        assert rt.role == "main"
        assert rt.provider_id == "claude_sdk"
        assert rt.model == "claude-sonnet-4-6"
        assert rt.runtime_family == "claude_sdk_compat"

    def test_legacy_sub_agent_model_resolves_subagent(self):
        cfg = {"agent": {"sub_agent_model": "claude-haiku-4-5"}}
        rt = resolve_runtime("subagent", cfg)
        assert rt.provider_id == "claude_sdk"
        assert rt.model == "claude-haiku-4-5"

    def test_env_model_for_memory_summary(self, monkeypatch):
        monkeypatch.setenv("EVOPAW_MEMORY_SUMMARY_MODEL", "qwen3-max-from-env")
        rt = resolve_runtime("memory_summary", {})
        assert rt.model == "qwen3-max-from-env"
        assert rt.provider_id == "dashscope"

    def test_env_model_overridden_by_role_config(self, monkeypatch):
        monkeypatch.setenv("EVOPAW_MEMORY_SUMMARY_MODEL", "qwen3-max-from-env")
        cfg = {"roles": {"memory_summary": {"model": "qwen3-turbo-cfg"}}}
        rt = resolve_runtime("memory_summary", cfg)
        assert rt.model == "qwen3-turbo-cfg"

    def test_memory_embedding_default_is_role_embedding_model(self):
        # memory_embedding 默认必须是 embedding 模型，不是 chat 模型。
        rt = resolve_runtime("memory_embedding", {})
        assert rt.provider_id == "dashscope"
        assert rt.model == "text-embedding-v3"

    def test_memory_extract_default_is_role_extract_model(self):
        rt = resolve_runtime("memory_extract", {})
        assert rt.provider_id == "dashscope"
        assert rt.model == "qwen3-max"

    def test_memory_summary_default_is_role_summary_model(self):
        rt = resolve_runtime("memory_summary", {})
        assert rt.provider_id == "dashscope"
        assert rt.model == "qwen3-turbo"

    def test_main_default_is_claude_sonnet(self):
        rt = resolve_runtime("main", {})
        assert rt.provider_id == "claude_sdk"
        assert rt.model == "claude-sonnet-4-6"

    def test_subagent_default_is_claude_haiku(self):
        rt = resolve_runtime("subagent", {})
        assert rt.provider_id == "claude_sdk"
        assert rt.model == "claude-haiku-4-5"

    def test_switching_provider_uses_provider_default_not_legacy_claude(self):
        # 用户只写 roles.main.provider 切到 moonshot 时，
        # 旧字段 agent.planner_model 不再传染，应取 provider default_model。
        cfg = {
            "agent": {"planner_model": "claude-sonnet-4-6"},
            "providers": {
                "moonshot": {
                    "runtime_family": "openai_chat",
                    "default_api_base": "https://api.moonshot.cn/v1",
                    "default_model": "moonshot-v1-32k",
                }
            },
            "roles": {"main": {"provider": "moonshot"}},
        }
        rt = resolve_runtime("main", cfg)
        assert rt.provider_id == "moonshot"
        assert rt.model == "moonshot-v1-32k"

    def test_switching_provider_uses_provider_default_for_subagent(self):
        cfg = {
            "agent": {"sub_agent_model": "claude-haiku-4-5"},
            "providers": {
                "moonshot": {
                    "runtime_family": "openai_chat",
                    "default_api_base": "https://api.moonshot.cn/v1",
                    "default_model": "moonshot-v1-32k",
                }
            },
            "roles": {"subagent": {"provider": "moonshot"}},
        }
        rt = resolve_runtime("subagent", cfg)
        assert rt.provider_id == "moonshot"
        assert rt.model == "moonshot-v1-32k"

    def test_role_default_model_let_way_to_explicit_model(self):
        # 用户在 roles.main.model 显式给值时，DEFAULT_ROLE_MODELS 不应覆盖。
        cfg = {"roles": {"main": {"model": "claude-opus-4-7"}}}
        rt = resolve_runtime("main", cfg)
        assert rt.model == "claude-opus-4-7"


# ── resolve_runtime: 错误路径 ────────────────────────────────────


class TestResolverErrors:
    def test_unknown_role_raises(self):
        with pytest.raises(ResolveError, match="无法确定 provider"):
            resolve_runtime("nonexistent_role", {})

    def test_role_with_unknown_provider_raises(self):
        cfg = {"roles": {"main": {"provider": "no-such-provider"}}}
        with pytest.raises(KeyError, match="未知 provider_id"):
            resolve_runtime("main", cfg)

    def test_provider_without_default_model_and_no_other_source_raises(self):
        cfg = {
            "providers": {
                "custom": {"runtime_family": "openai_chat"},  # 无 default_model
            },
            "roles": {"main": {"provider": "custom"}},  # 无 model
        }
        with pytest.raises(ResolveError, match="无法确定 model"):
            resolve_runtime("main", cfg)

    def test_invalid_role_config_type_raises(self):
        cfg = {"roles": {"main": "not-a-mapping"}}
        with pytest.raises(ValueError, match="必须是 mapping"):
            resolve_runtime("main", cfg)


# ── resolve_runtime: api_base 与 api_key 注入 ──────────────────


class TestResolverApiCredentials:
    def test_api_base_from_role_config_overrides_default(self):
        cfg = {"roles": {"memory_summary": {"api_base": "http://localhost:8080/v1"}}}
        rt = resolve_runtime("memory_summary", cfg)
        assert rt.api_base == "http://localhost:8080/v1"

    def test_api_base_falls_back_to_provider_default(self):
        rt = resolve_runtime("memory_summary", {})
        assert rt.api_base == DEFAULT_PROVIDERS["dashscope"].default_api_base

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("QWEN_API_KEY", "sk-test-1234")
        rt = resolve_runtime("memory_summary", {})
        assert rt.api_key == "sk-test-1234"

    def test_api_key_empty_env_returns_none(self, monkeypatch):
        monkeypatch.delenv("QWEN_API_KEY", raising=False)
        rt = resolve_runtime("memory_summary", {})
        assert rt.api_key is None

    def test_api_key_env_overrides_via_role_config(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_KEY", "sk-custom")
        monkeypatch.setenv("QWEN_API_KEY", "sk-default")
        cfg = {"roles": {"memory_summary": {"api_key_env": "CUSTOM_KEY"}}}
        rt = resolve_runtime("memory_summary", cfg)
        assert rt.api_key == "sk-custom"

    def test_redacted_strips_api_key(self):
        rt = ResolvedRuntime(
            role="main",
            provider_id="anthropic",
            runtime_family="anthropic_messages",
            model="claude-sonnet-4-6",
            api_key="sk-secret",
        )
        assert rt.redacted().api_key is None


# ── deprecation warnings ────────────────────────────────────────


class TestResolverExtraBody:
    def test_extra_body_from_role_config_passes_whitelist(self):
        cfg = {
            "roles": {
                "memory_summary": {
                    "extra_body": {"enable_thinking": False},
                }
            }
        }
        rt = resolve_runtime("memory_summary", cfg)
        assert rt.extra_body == {"enable_thinking": False}

    def test_extra_body_filtered_outside_whitelist(self, caplog):
        cfg = {
            "roles": {
                "memory_summary": {
                    "extra_body": {
                        "enable_thinking": False,
                        "rogue_field": "should-drop",
                    },
                }
            }
        }
        with caplog.at_level(logging.WARNING):
            rt = resolve_runtime("memory_summary", cfg)
        assert rt.extra_body == {"enable_thinking": False}
        assert any(
            "白名单内" in r.getMessage() and "rogue_field" in r.getMessage()
            for r in caplog.records
        )

    def test_extra_body_overrides_beats_role_config(self):
        cfg = {
            "roles": {
                "memory_summary": {"extra_body": {"enable_thinking": False}}
            }
        }
        rt = resolve_runtime(
            "memory_summary",
            cfg,
            overrides={"extra_body": {"enable_thinking": True}},
        )
        assert rt.extra_body == {"enable_thinking": True}

    def test_extra_body_provider_default_applied_when_no_role_cfg(self):
        # dashscope 默认 default_extra_body={"enable_thinking": False}
        rt = resolve_runtime("memory_summary", {})
        assert rt.extra_body == {"enable_thinking": False}

    def test_extra_body_role_cfg_overrides_provider_default_field_level(self):
        cfg = {
            "roles": {
                "memory_summary": {"extra_body": {"enable_thinking": True}}
            }
        }
        rt = resolve_runtime("memory_summary", cfg)
        assert rt.extra_body == {"enable_thinking": True}

    def test_extra_body_default_empty_for_provider_without_default(self):
        rt = resolve_runtime(
            "main",
            {"roles": {"main": {"provider": "claude_sdk"}}},
        )
        assert rt.extra_body == {}

    def test_extra_body_dropped_when_provider_has_empty_whitelist(self, caplog):
        # claude_sdk provider 没有 extra_body_whitelist
        cfg = {
            "roles": {
                "main": {
                    "provider": "claude_sdk",
                    "extra_body": {"any_field": "x"},
                }
            }
        }
        with caplog.at_level(logging.WARNING):
            rt = resolve_runtime("main", cfg)
        assert rt.extra_body == {}
        assert any("白名单内" in r.getMessage() for r in caplog.records)

    def test_extra_body_invalid_type_raises(self):
        cfg = {"roles": {"memory_summary": {"extra_body": "not-a-mapping"}}}
        # Pydantic 会先在 RoleConfig 解析阶段报错
        with pytest.raises(Exception):  # ValidationError
            resolve_runtime("memory_summary", cfg)


class TestDeprecationWarnings:
    def test_legacy_planner_model_emits_warning_once(self, caplog):
        cfg = {"agent": {"planner_model": "claude-sonnet-4-6"}}
        with caplog.at_level(logging.WARNING):
            resolve_runtime("main", cfg)
            resolve_runtime("main", cfg)
        # 同一进程内同字段只 warn 一次
        warnings = [r for r in caplog.records if "已被弃用" in r.getMessage()]
        assert len(warnings) == 1
        assert "planner_model" in warnings[0].getMessage()

    def test_legacy_sub_agent_model_warns(self, caplog):
        cfg = {"agent": {"sub_agent_model": "claude-haiku-4-5"}}
        with caplog.at_level(logging.WARNING):
            resolve_runtime("subagent", cfg)
        msgs = [r for r in caplog.records if "已被弃用" in r.getMessage()]
        assert any("sub_agent_model" in m.getMessage() for m in msgs)

    def test_no_warning_when_using_role_config(self, caplog):
        cfg = {"roles": {"main": {"provider": "claude_sdk", "model": "x"}}}
        with caplog.at_level(logging.WARNING):
            resolve_runtime("main", cfg)
        warnings = [r for r in caplog.records if "已被弃用" in r.getMessage()]
        assert warnings == []
