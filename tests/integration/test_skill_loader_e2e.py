"""Skill loader e2e：三族主 runtime 共享 SkillDispatcher 路径冒烟。

不连真实 LLM 端点：用 FakeBackend mock 主 Agent runtime；
不真启 Sub-Agent：mock run_skill_agent。

覆盖点：
- 所有 enabled skill 在 dispatcher 中能找到正确分发路径。
- 三族主 runtime（claude_sdk_compat / openai_chat / anthropic_messages）都把
  同一份 SkillDispatcher 业务逻辑暴露给 LLM。
- task 型 skill 始终 fallback 到 Claude SDK Sub-Agent（run_skill_agent）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from evopaw.agent_backends import TurnRequest, TurnResult
from evopaw.agents.main_agent import build_agent_fn
from evopaw.provider_runtime import ResolvedRuntime
from evopaw.skills_runtime.dispatcher import SkillDispatcher

_SKILLS_DIR = Path(__file__).parents[2] / "evopaw" / "skills"


# ── 真实 skill 清单 ────────────────────────────────────────────────────────


def _load_enabled_skills() -> list[tuple[str, str]]:
    """读取真实 evopaw/skills/load_skills.yaml，返回 [(name, type), ...]。"""
    manifest = yaml.safe_load((_SKILLS_DIR / "load_skills.yaml").read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for s in manifest.get("skills") or []:
        if not s.get("enabled", True):
            continue
        out.append((s["name"], s.get("type", "task")))
    return out


_SKILL_CATALOG = _load_enabled_skills()
_TASK_SKILLS = [n for n, t in _SKILL_CATALOG if t == "task"]
_REFERENCE_SKILLS = [n for n, t in _SKILL_CATALOG if t == "reference"]


# ── 三族 runtime fixture ────────────────────────────────────────────────────


def _runtime_for(family: str) -> ResolvedRuntime:
    if family == "claude_sdk_compat":
        return ResolvedRuntime(
            role="main",
            provider_id="claude_sdk",
            runtime_family="claude_sdk_compat",
            model="claude-sonnet-4-6",
        )
    if family == "openai_chat":
        return ResolvedRuntime(
            role="main",
            provider_id="openai",
            runtime_family="openai_chat",
            model="gpt-4o",
            api_base="https://api.openai.com",
            api_key="sk-x",
        )
    if family == "anthropic_messages":
        return ResolvedRuntime(
            role="main",
            provider_id="anthropic",
            runtime_family="anthropic_messages",
            model="claude-sonnet-4-6",
            api_base="https://api.anthropic.com",
            api_key="sk-ant-x",
        )
    raise ValueError(f"unknown family: {family}")


_DEFAULT_SUB_RUNTIME = ResolvedRuntime(
    role="subagent",
    provider_id="claude_sdk",
    runtime_family="claude_sdk_compat",
    model="claude-haiku-4-5",
)


_orig_build_agent_fn = build_agent_fn


def build_agent_fn(*args, **kwargs):  # type: ignore[no-redef]
    """test wrapper：默认带上 sub_runtime，避免每个 case 重复传。"""
    kwargs.setdefault("sub_runtime", _DEFAULT_SUB_RUNTIME)
    return _orig_build_agent_fn(*args, **kwargs)


class FakeBackend:
    """记录 TurnRequest，不真调 LLM；返回固定文本。"""

    def __init__(self) -> None:
        self.calls: list[TurnRequest] = []

    async def run_turn(self, req: TurnRequest) -> TurnResult:
        self.calls.append(req)
        return TurnResult(text="ok")


# ── catalog 完整性 ─────────────────────────────────────────────────────────


class TestSkillCatalogIntegrity:
    """守护 evopaw/skills/load_skills.yaml 与 docs/skills-provider-matrix.md 一致性。"""

    def test_at_least_one_reference_skill(self):
        # history_reader 永远是 reference
        assert "history_reader" in _REFERENCE_SKILLS

    def test_majority_are_task(self):
        # 18 task + 1 reference 是当前快照
        assert len(_TASK_SKILLS) >= len(_REFERENCE_SKILLS)

    def test_no_unknown_type(self):
        for name, t in _SKILL_CATALOG:
            assert t in ("task", "reference"), (
                f"skill {name} 的 type={t!r} 不属于 (task, reference)"
            )

    def test_dispatcher_loads_all_enabled_skills(self):
        d = SkillDispatcher(session_id="sid", skills_dir=_SKILLS_DIR)
        for name, _ in _SKILL_CATALOG:
            assert name in d.registry, f"{name} 未被 SkillDispatcher 加载"

    def test_dispatcher_description_mentions_each_skill(self):
        d = SkillDispatcher(session_id="sid", skills_dir=_SKILLS_DIR)
        desc = d.get_description()
        for name, _ in _SKILL_CATALOG:
            assert f"<name>{name}</name>" in desc, (
                f"{name} 未出现在 <available_skills> XML 中"
            )


# ── 三族主 runtime backend_hints 路径 ───────────────────────────────────────


class TestThreeRuntimeFamiliesShareDispatcher:
    """build_agent_fn 在三族 runtime 下都把 SkillDispatcher 逻辑暴露给 backend。"""

    @pytest.mark.asyncio
    async def test_claude_sdk_compat_routes_via_mcp_server(self, tmp_path: Path):
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        fake = FakeBackend()
        runtime = _runtime_for("claude_sdk_compat")
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)

        with patch("evopaw.agents.main_agent.get_backend", return_value=fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""), \
             patch(
                 "evopaw.agents.main_agent.build_skill_loader_server",
                 return_value="<sentinel-mcp-server>",
             ):
            await fn("hi", [], "sid_sdk")

        hints = fake.calls[0].backend_hints
        assert hints.get("mcp_servers", {}).get("evopaw") == "<sentinel-mcp-server>"
        assert "skill_dispatcher" not in hints

    @pytest.mark.asyncio
    @pytest.mark.parametrize("family", ["openai_chat", "anthropic_messages"])
    async def test_http_backends_route_via_skill_dispatcher(
        self, tmp_path: Path, family: str,
    ):
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        fake = FakeBackend()
        runtime = _runtime_for(family)
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)

        with patch("evopaw.agents.main_agent.get_backend", return_value=fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], f"sid_{family}")

        hints = fake.calls[0].backend_hints
        assert "skill_dispatcher" in hints
        assert isinstance(hints["skill_dispatcher"], SkillDispatcher)
        # 不再走 SDK MCP 路径
        assert "mcp_servers" not in hints

    @pytest.mark.asyncio
    @pytest.mark.parametrize("family", ["openai_chat", "anthropic_messages"])
    async def test_dispatcher_loaded_with_real_skills(
        self, tmp_path: Path, family: str,
    ):
        """三族下都得到一个能列出全部 19 个真实 Skill 的 dispatcher。"""
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        fake = FakeBackend()
        fn = build_agent_fn(
            sender, workspace_dir=ws, ctx_dir=ctx,
            main_runtime=_runtime_for(family),
        )

        with patch("evopaw.agents.main_agent.get_backend", return_value=fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", [], f"sid_{family}_disp")

        d: SkillDispatcher = fake.calls[0].backend_hints["skill_dispatcher"]
        names = d.list_skill_names()
        for skill_name, _ in _SKILL_CATALOG:
            assert skill_name in names, f"{skill_name} 不在 dispatcher.registry"


# ── task skills 始终 fallback 到 Claude SDK Sub-Agent ──────────────────────


class TestTaskSkillsAlwaysFallBackToClaudeSubAgent:
    """无论主 runtime 是哪一族，task 型 Skill 都触发 run_skill_agent (Claude SDK Sub-Agent)。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("skill_name", _TASK_SKILLS)
    async def test_task_skill_dispatches_to_run_skill_agent(self, skill_name: str):
        d = SkillDispatcher(session_id="sid", skills_dir=_SKILLS_DIR)
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent",
            new=AsyncMock(return_value=f"sub-agent reply for {skill_name}"),
        ) as mock_agent:
            out = await d.dispatch(skill_name, "{}")

        assert out == f"sub-agent reply for {skill_name}"
        mock_agent.assert_called_once()
        kwargs = mock_agent.call_args.kwargs
        assert kwargs["skill_name"] == skill_name
        # 默认仍走 DEFAULT_SUB_AGENT_MODEL（Claude Haiku）
        assert "haiku" in kwargs["model"].lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("skill_name", _REFERENCE_SKILLS)
    async def test_reference_skill_does_not_call_sub_agent(self, skill_name: str):
        d = SkillDispatcher(
            session_id="sid",
            skills_dir=_SKILLS_DIR,
            history_all=[],
        )
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent",
            new=AsyncMock(),
        ) as mock_agent:
            out = await d.dispatch(skill_name, "{}")

        # reference 不调 Sub-Agent
        mock_agent.assert_not_called()
        # history_reader 是内联 JSON；其它 reference 是 <skill_instructions> 包裹
        if skill_name == "history_reader":
            assert "errcode" in out
        else:
            assert out.startswith("<skill_instructions>")
            assert out.endswith("</skill_instructions>")


# ── 主 Agent 跨 runtime 调用 task skill 的 e2e ──────────────────────────────


class TestEndToEndTaskSkillAcrossRuntimes:
    """模拟 LLM 触发 skill_loader → dispatcher → run_skill_agent 的完整链路。

    针对 openai_chat / anthropic_messages 两族（claude_sdk_compat 的 MCP 路径
    由 test_skill_loader.py 单测覆盖，这里关注新引入的 dispatcher 直调路径）。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("family", ["openai_chat", "anthropic_messages"])
    @pytest.mark.parametrize("skill_name", _TASK_SKILLS[:3])  # 抽 3 个就够覆盖逻辑
    async def test_dispatcher_from_hints_invokes_run_skill_agent(
        self, tmp_path: Path, family: str, skill_name: str,
    ):
        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        fake = FakeBackend()
        runtime = _runtime_for(family)
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)

        with patch("evopaw.agents.main_agent.get_backend", return_value=fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("trigger", [], f"sid_{family}_{skill_name}")

        d: SkillDispatcher = fake.calls[0].backend_hints["skill_dispatcher"]

        with patch(
            "evopaw.agents.skill_agent.run_skill_agent",
            new=AsyncMock(return_value="task done"),
        ) as mock_agent:
            result = await d.dispatch(skill_name, '{"query": "x"}')

        assert result == "task done"
        mock_agent.assert_called_once()
        # task_context JSON 字符串原样透传
        assert mock_agent.call_args.kwargs["task_context"] == '{"query": "x"}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize("family", ["openai_chat", "anthropic_messages"])
    async def test_history_reader_inline_across_runtimes(
        self, tmp_path: Path, family: str,
    ):
        from evopaw.session.models import MessageEntry

        sender = MagicMock()
        ws = tmp_path / "ws"; ws.mkdir()
        ctx = tmp_path / "ctx"; ctx.mkdir()
        fake = FakeBackend()
        runtime = _runtime_for(family)
        fn = build_agent_fn(sender, workspace_dir=ws, ctx_dir=ctx, main_runtime=runtime)

        history = [MessageEntry(role="user", content=f"m{i}", ts=i) for i in range(5)]
        with patch("evopaw.agents.main_agent.get_backend", return_value=fake), \
             patch("evopaw.agents.main_agent.build_bootstrap_prompt", return_value=""):
            await fn("hi", history, f"sid_hr_{family}")

        d: SkillDispatcher = fake.calls[0].backend_hints["skill_dispatcher"]

        # history_reader 不应触发 Sub-Agent
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent",
            new=AsyncMock(),
        ) as mock_agent:
            out = await d.dispatch("history_reader", '{"page": 1, "page_size": 10}')

        mock_agent.assert_not_called()
        import json as _json
        parsed = _json.loads(out)
        assert parsed["errcode"] == 0
        assert parsed["data"]["total"] == 5
