"""evopaw/main.py 中 ASR 装配相关的小单测.

不启动整个 main，只测可独立调用的工厂函数：
- _build_speech_service：enabled / 缺 API Key / 别名警告
- _warn_if_model_is_alias：覆盖快照号判定（§9.1 / Phase 4 第 3 项）
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from evopaw.main import (
    _build_speech_service,
    _validate_subagent_runtime,
    _warn_if_model_is_alias,
)
from evopaw.provider_runtime import resolve_runtime


class TestBuildSpeechService:
    def test_disabled_returns_none(self):
        assert _build_speech_service({"enabled": False}) is None

    def test_enabled_without_api_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        assert _build_speech_service({"enabled": True}) is None

    def test_enabled_with_api_key_returns_service(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-key")
        svc = _build_speech_service({"enabled": True})
        assert svc is not None
        # 客户端层提供的 _provider 应被透传
        assert svc._provider == "aliyun_funasr_realtime"

    def test_alias_model_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-key")
        with caplog.at_level(logging.WARNING, logger="evopaw.main"):
            _build_speech_service({"enabled": True, "model": "fun-asr-realtime"})
        joined = " ".join(r.message for r in caplog.records)
        assert "稳定别名" in joined
        assert "§9.1" in joined or "9.1" in joined


class TestWarnIfModelIsAlias:
    @pytest.mark.parametrize(
        "alias",
        ["fun-asr-realtime", "fun-asr-flash-8k-realtime"],
    )
    def test_alias_warns(self, alias, caplog):
        with caplog.at_level(logging.WARNING, logger="evopaw.main"):
            _warn_if_model_is_alias(alias)
        assert any("稳定别名" in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        "snapshot",
        [
            "fun-asr-realtime-2025-11-07",
            "fun-asr-flash-8k-realtime-2025-11-07",
            "fun-asr-realtime-2026-01-15",
        ],
    )
    def test_snapshot_does_not_warn(self, snapshot, caplog):
        with caplog.at_level(logging.WARNING, logger="evopaw.main"):
            _warn_if_model_is_alias(snapshot)
        assert not any("稳定别名" in r.message for r in caplog.records)


class TestValidateSubagentRuntime:
    """P1-1：roles.subagent 必须解析为 claude_sdk_compat。"""

    def test_default_subagent_passes(self):
        # 默认配置走 claude_sdk，校验通过。
        rt = resolve_runtime("subagent", {})
        _validate_subagent_runtime(rt)  # not raise

    def test_subagent_on_openai_chat_provider_rejected(self):
        cfg = {
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
        with pytest.raises(RuntimeError, match="claude_sdk_compat"):
            _validate_subagent_runtime(rt)

    def test_subagent_on_anthropic_messages_rejected(self):
        cfg = {"roles": {"subagent": {"provider": "anthropic"}}}
        rt = resolve_runtime("subagent", cfg)
        with pytest.raises(RuntimeError, match="claude_sdk_compat"):
            _validate_subagent_runtime(rt)
