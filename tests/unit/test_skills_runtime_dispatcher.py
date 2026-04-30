"""SkillDispatcher 单元测试（P3）。

覆盖三类分发：
- 未知 Skill → 友好错误（含可用列表）
- history_reader → 内联返回 JSON 字符串
- reference 型 → <skill_instructions> 包裹的 SKILL.md（剥离 frontmatter）
- task 型 → 调用 run_skill_agent；本测试 mock 该函数避免真实 Sub-Agent 启动

附 dispatcher.get_description() 与 list_skill_names() 的合同测试。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from evopaw.session.models import MessageEntry
from evopaw.skills_runtime.dispatcher import (
    SkillDispatcher,
    _normalize_task_context,
)


@pytest.fixture
def tmp_skills_dir(tmp_path: Path) -> Path:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "load_skills.yaml").write_text(
        "skills:\n"
        "  - name: ref_skill\n"
        "    type: reference\n"
        "    enabled: true\n"
        "  - name: task_skill\n"
        "    type: task\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (skills_dir / "ref_skill").mkdir()
    (skills_dir / "ref_skill" / "SKILL.md").write_text(
        "---\nname: ref_skill\ndescription: 参考型\ntype: reference\nversion: \"1.0\"\n---\n"
        "Ref Skill 内容\n",
        encoding="utf-8",
    )
    (skills_dir / "task_skill").mkdir()
    (skills_dir / "task_skill" / "SKILL.md").write_text(
        "---\nname: task_skill\ndescription: 任务型\ntype: task\nversion: \"1.0\"\n---\n"
        "Task Skill 步骤\n",
        encoding="utf-8",
    )
    return skills_dir


def _msg(role: str, content: str) -> MessageEntry:
    return MessageEntry(role=role, content=content, ts=0)


class TestNormalizeTaskContext:
    def test_string_passthrough(self):
        assert _normalize_task_context("abc") == "abc"

    def test_dict_to_json(self):
        assert _normalize_task_context({"k": 1}) == '{"k": 1}'

    def test_list_to_json(self):
        assert _normalize_task_context([1, 2]) == "[1, 2]"

    def test_none_to_empty(self):
        assert _normalize_task_context(None) == ""

    def test_other_to_str(self):
        assert _normalize_task_context(123) == "123"


class TestDispatcherInit:
    def test_basic_init(self, tmp_skills_dir: Path):
        d = SkillDispatcher(
            session_id="sid",
            routing_key="p2p:ou_x",
            skills_dir=tmp_skills_dir,
        )
        assert d.session_id == "sid"
        assert d.routing_key == "p2p:ou_x"
        assert "ref_skill" in d.registry
        assert "task_skill" in d.registry

    def test_history_all_copied(self, tmp_skills_dir: Path):
        history = [_msg("user", "old")]
        d = SkillDispatcher(
            session_id="sid", skills_dir=tmp_skills_dir, history_all=history,
        )
        history.append(_msg("user", "new"))
        # dispatcher 内部应当保留拷贝快照
        assert len(d.history_all) == 1

    def test_list_skill_names(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        names = d.list_skill_names()
        assert "ref_skill" in names
        assert "task_skill" in names


class TestDispatcherGetDescription:
    def test_xml_contains_skills(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid-test", skills_dir=tmp_skills_dir)
        desc = d.get_description()
        assert "<available_skills>" in desc
        assert "<name>ref_skill</name>" in desc
        assert "<name>task_skill</name>" in desc
        assert "/workspace/sessions/sid-test/" in desc

    def test_empty_registry_returns_placeholder(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        d = SkillDispatcher(session_id="sid", skills_dir=empty)
        assert d.get_description() == "SkillLoaderTool 已初始化，但暂无可用 Skill。"


class TestDispatchUnknown:
    @pytest.mark.asyncio
    async def test_unknown_skill_returns_friendly_error(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        out = await d.dispatch("nonexistent", "")
        assert "未找到 Skill 'nonexistent'" in out
        assert "ref_skill" in out and "task_skill" in out


class TestDispatchHistoryReader:
    @pytest.mark.asyncio
    async def test_history_reader_inline(self, tmp_skills_dir: Path):
        # 不需要在 registry 里也能命中（history_reader 路径在 registry 检查之后）
        # 这里把 history_reader 加入 registry 以走完整路径
        (tmp_skills_dir / "history_reader").mkdir()
        (tmp_skills_dir / "history_reader" / "SKILL.md").write_text(
            "---\nname: history_reader\ndescription: ok\ntype: reference\nversion: \"1.0\"\n---\nstub\n",
            encoding="utf-8",
        )
        (tmp_skills_dir / "load_skills.yaml").write_text(
            "skills:\n  - name: history_reader\n    type: reference\n    enabled: true\n",
            encoding="utf-8",
        )
        history = [_msg("user", f"msg-{i}") for i in range(5)]
        d = SkillDispatcher(
            session_id="sid", skills_dir=tmp_skills_dir, history_all=history,
        )
        out = await d.dispatch("history_reader", '{"page": 1, "page_size": 3}')
        parsed = json.loads(out)
        assert parsed["errcode"] == 0
        assert parsed["data"]["total"] == 5
        assert len(parsed["data"]["messages"]) == 3


class TestDispatchReference:
    @pytest.mark.asyncio
    async def test_reference_returns_wrapped_instructions(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        out = await d.dispatch("ref_skill", "")
        assert out.startswith("<skill_instructions>")
        assert out.endswith("</skill_instructions>")
        # frontmatter 已剥离
        assert "Ref Skill 内容" in out
        assert "version:" not in out.split("<execution_directive>")[0]


class TestDispatchTask:
    @pytest.mark.asyncio
    async def test_task_calls_run_skill_agent(self, tmp_skills_dir: Path):
        d = SkillDispatcher(
            session_id="sid", routing_key="p2p:ou_x", skills_dir=tmp_skills_dir,
        )
        mock_agent = AsyncMock(return_value="子 Agent 返回")
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent", mock_agent,
        ):
            out = await d.dispatch("task_skill", '{"k": "v"}')
        assert out == "子 Agent 返回"
        mock_agent.assert_called_once()
        kwargs = mock_agent.call_args.kwargs
        assert kwargs["skill_name"] == "task_skill"
        assert "Task Skill 步骤" in kwargs["skill_instructions"]
        assert kwargs["task_context"] == '{"k": "v"}'
        assert kwargs["session_path"] == "/workspace"


class TestDispatchTaskContextNormalization:
    @pytest.mark.asyncio
    async def test_dict_context_serialized_before_dispatch(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        mock_agent = AsyncMock(return_value="ok")
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent", mock_agent,
        ):
            await d.dispatch("task_skill", {"q": "hello"})
        kwargs = mock_agent.call_args.kwargs
        assert kwargs["task_context"] == '{"q": "hello"}'


# ──────────────────────────────────────────────────────────────────
# dispatcher 透传 8 字符 hex task_id 给 run_skill_agent
# ──────────────────────────────────────────────────────────────────


class TestDispatchTaskId:
    @pytest.mark.asyncio
    async def test_dispatch_passes_8_hex_task_id(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        mock_agent = AsyncMock(return_value="ok")
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent", mock_agent,
        ):
            await d.dispatch("task_skill", "")
        kwargs = mock_agent.call_args.kwargs
        tid = kwargs.get("task_id")
        assert isinstance(tid, str)
        assert re.match(r"^[0-9a-f]{8}$", tid), f"unexpected task_id: {tid!r}"

    @pytest.mark.asyncio
    async def test_dispatch_returns_str(self, tmp_skills_dir: Path):
        """dispatch 必须返回 str（三个 backend 工具结果契约）。"""
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        mock_agent = AsyncMock(return_value="子 Agent 文本")
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent", mock_agent,
        ):
            out = await d.dispatch("task_skill", "")
        assert isinstance(out, str)
        assert out == "子 Agent 文本"

    @pytest.mark.asyncio
    async def test_each_dispatch_generates_distinct_task_id(self, tmp_skills_dir: Path):
        d = SkillDispatcher(session_id="sid", skills_dir=tmp_skills_dir)
        seen: list[str] = []

        async def capture(*args, **kwargs):
            seen.append(kwargs["task_id"])
            return "ok"

        with patch("evopaw.agents.skill_agent.run_skill_agent", side_effect=capture):
            for _ in range(5):
                await d.dispatch("task_skill", "")

        assert len(seen) == 5
        assert len(set(seen)) == 5, f"task_id 应每次不同：{seen!r}"


# ──────────────────────────────────────────────────────────────────
# unavailable skill 守卫 + description XML 标记
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_skills_dir_with_unavailable(tmp_path: Path) -> Path:
    """两个 skill：可用 ref_skill + 缺凭证文件的 needy_skill。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "load_skills.yaml").write_text(
        "skills:\n"
        "  - name: ref_skill\n"
        "    type: reference\n"
        "    enabled: true\n"
        "  - name: needy_skill\n"
        "    type: task\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (skills_dir / "ref_skill").mkdir()
    (skills_dir / "ref_skill" / "SKILL.md").write_text(
        "---\nname: ref_skill\ndescription: 参考型\ntype: reference\nversion: \"1.0\"\n---\n"
        "Ref 内容\n",
        encoding="utf-8",
    )
    (skills_dir / "needy_skill").mkdir()
    nonexistent = tmp_path / "no_such_creds.json"  # 故意不创建
    (skills_dir / "needy_skill" / "SKILL.md").write_text(
        "---\nname: needy_skill\ndescription: 需要凭证\ntype: task\nversion: \"1.0\"\n"
        f"requires:\n  files:\n    - \"{nonexistent}\"\n---\n"
        "需要凭证才能跑\n",
        encoding="utf-8",
    )
    return skills_dir


class TestDispatchUnavailable:
    @pytest.mark.asyncio
    async def test_unavailable_skill_blocked_before_subagent(
        self, tmp_skills_dir_with_unavailable: Path,
    ):
        d = SkillDispatcher(
            session_id="sid", skills_dir=tmp_skills_dir_with_unavailable,
        )
        mock_agent = AsyncMock(return_value="不应该被调到")
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent", mock_agent,
        ):
            out = await d.dispatch("needy_skill", "")
        assert "不可用" in out
        assert "needy_skill" in out
        # Sub-Agent 不应被启动
        mock_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_available_skill_still_dispatched(
        self, tmp_skills_dir_with_unavailable: Path,
    ):
        d = SkillDispatcher(
            session_id="sid", skills_dir=tmp_skills_dir_with_unavailable,
        )
        out = await d.dispatch("ref_skill", "")
        assert out.startswith("<skill_instructions>")

    def test_description_xml_marks_unavailable(
        self, tmp_skills_dir_with_unavailable: Path,
    ):
        d = SkillDispatcher(
            session_id="sid", skills_dir=tmp_skills_dir_with_unavailable,
        )
        desc = d.get_description()
        assert "<name>ref_skill</name>" in desc
        assert "<name>needy_skill</name>" in desc
        # 不可用 skill 仍然出现在 XML 中（让 LLM 知道能力存在）
        assert "<available>false</available>" in desc
        assert "<unavailable_reason>" in desc
        # 可用 skill 也带 <available>true</available>
        assert "<available>true</available>" in desc


# ──────────────────────────────────────────────────────────────────
# background 模式 task skill —— 立即返回 + 后台 spawn + callback 回注
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_bg_skills_dir(tmp_path: Path) -> Path:
    """单 skill：execution.mode=background 的 task 型 skill。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "load_skills.yaml").write_text(
        "skills:\n"
        "  - name: bg_skill\n"
        "    type: task\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (skills_dir / "bg_skill").mkdir()
    (skills_dir / "bg_skill" / "SKILL.md").write_text(
        "---\nname: bg_skill\ndescription: 后台型\ntype: task\nversion: \"1.0\"\n"
        "execution:\n  mode: background\n---\n"
        "BG Skill 步骤\n",
        encoding="utf-8",
    )
    return skills_dir


class TestDispatchBackgroundMode:
    @pytest.mark.asyncio
    async def test_background_returns_immediate_prompt_with_task_id(
        self, tmp_bg_skills_dir: Path,
    ):
        from evopaw.agents.sub_agent_registry import _reset_default_registry_for_tests

        _reset_default_registry_for_tests()
        d = SkillDispatcher(
            session_id="sid", routing_key="p2p:ou_x", skills_dir=tmp_bg_skills_dir,
        )

        import asyncio as _asyncio

        # 让 sub-agent 永远 pending，避免任务结束触发 unregister 干扰断言
        gate = _asyncio.Event()

        async def _slow(*args, **kwargs):
            await gate.wait()
            return "迟到的结果"

        with patch("evopaw.agents.skill_agent.run_skill_agent", side_effect=_slow):
            out = await d.dispatch("bg_skill", "")

        assert out.startswith("已启动后台任务 task#")
        # 提取并校验 task_id 格式
        m = re.search(r"task#([0-9a-f]{8})", out)
        assert m, f"未在返回中找到 task#xxxxxxxx：{out!r}"
        # 提示中应携带 skill 名
        assert "bg_skill" in out

        # 后台任务已注册到默认 registry
        from evopaw.agents.sub_agent_registry import get_default_registry

        registry = get_default_registry()
        assert registry.active_count("p2p:ou_x") == 1

        # 收尾：放行任务并等待清理
        gate.set()
        await _asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_background_invokes_result_callback_on_completion(
        self, tmp_bg_skills_dir: Path,
    ):
        from evopaw.agents.sub_agent_registry import _reset_default_registry_for_tests

        _reset_default_registry_for_tests()
        captured: list[tuple[str, str, str]] = []

        async def _cb(task_id: str, skill_name: str, result_text: str) -> None:
            captured.append((task_id, skill_name, result_text))

        d = SkillDispatcher(
            session_id="sid",
            routing_key="p2p:ou_x",
            skills_dir=tmp_bg_skills_dir,
            result_callback=_cb,
        )

        mock_agent = AsyncMock(return_value="后台跑出来的结果")
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent", mock_agent,
        ):
            prompt = await d.dispatch("bg_skill", '{"q": 1}')
            # 让出控制权让后台 task 跑完
            import asyncio as _asyncio
            for _ in range(20):
                if captured:
                    break
                await _asyncio.sleep(0.01)

        assert prompt.startswith("已启动后台任务 task#")
        assert len(captured) == 1
        task_id, skill_name, result_text = captured[0]
        assert re.match(r"^[0-9a-f]{8}$", task_id)
        assert skill_name == "bg_skill"
        assert result_text == "后台跑出来的结果"

        # 任务结束后 registry 应被注销
        from evopaw.agents.sub_agent_registry import get_default_registry
        await _asyncio.sleep(0.02)
        assert get_default_registry().active_count("p2p:ou_x") == 0

    @pytest.mark.asyncio
    async def test_background_callback_receives_error_text_on_crash(
        self, tmp_bg_skills_dir: Path,
    ):
        from evopaw.agents.sub_agent_registry import _reset_default_registry_for_tests

        _reset_default_registry_for_tests()
        captured: list[tuple[str, str, str]] = []

        async def _cb(task_id: str, skill_name: str, result_text: str) -> None:
            captured.append((task_id, skill_name, result_text))

        d = SkillDispatcher(
            session_id="sid",
            routing_key="p2p:ou_x",
            skills_dir=tmp_bg_skills_dir,
            result_callback=_cb,
        )

        async def _boom(*args, **kwargs):
            raise RuntimeError("sub-agent died")

        import asyncio as _asyncio
        with patch("evopaw.agents.skill_agent.run_skill_agent", side_effect=_boom):
            await d.dispatch("bg_skill", "")
            for _ in range(20):
                if captured:
                    break
                await _asyncio.sleep(0.01)

        assert len(captured) == 1
        task_id, skill_name, result_text = captured[0]
        assert skill_name == "bg_skill"
        assert "执行失败" in result_text
        assert f"task#{task_id}" in result_text

    @pytest.mark.asyncio
    async def test_background_cancel_does_not_invoke_callback(
        self, tmp_bg_skills_dir: Path,
    ):
        from evopaw.agents.sub_agent_registry import (
            _reset_default_registry_for_tests,
            get_default_registry,
        )

        _reset_default_registry_for_tests()
        captured: list[tuple[str, str, str]] = []

        async def _cb(task_id: str, skill_name: str, result_text: str) -> None:
            captured.append((task_id, skill_name, result_text))

        d = SkillDispatcher(
            session_id="sid",
            routing_key="p2p:ou_x",
            skills_dir=tmp_bg_skills_dir,
            result_callback=_cb,
        )

        import asyncio as _asyncio
        gate = _asyncio.Event()

        async def _slow(*args, **kwargs):
            await gate.wait()
            return "不该被回注"

        with patch("evopaw.agents.skill_agent.run_skill_agent", side_effect=_slow):
            await d.dispatch("bg_skill", "")
            registry = get_default_registry()
            assert registry.active_count("p2p:ou_x") == 1
            cancelled = await registry.cancel_by_session("p2p:ou_x")
            assert cancelled == 1
            # 让 cancel 事件传播 + done_callback 触发
            for _ in range(20):
                await _asyncio.sleep(0.01)

        # cancel 路径不应触发 callback
        assert captured == []

    @pytest.mark.asyncio
    async def test_background_without_callback_logs_only(
        self, tmp_bg_skills_dir: Path, caplog: pytest.LogCaptureFixture,
    ):
        """callback=None 时任务正常完成，仅写日志，不抛错。"""
        from evopaw.agents.sub_agent_registry import _reset_default_registry_for_tests

        _reset_default_registry_for_tests()
        d = SkillDispatcher(
            session_id="sid",
            routing_key="p2p:ou_x",
            skills_dir=tmp_bg_skills_dir,
            result_callback=None,
        )

        mock_agent = AsyncMock(return_value="ok")
        import asyncio as _asyncio
        with caplog.at_level("INFO", logger="evopaw.skills_runtime.dispatcher"):
            with patch(
                "evopaw.agents.skill_agent.run_skill_agent", mock_agent,
            ):
                await d.dispatch("bg_skill", "")
                # 等任务完成
                for _ in range(20):
                    await _asyncio.sleep(0.01)

        mock_agent.assert_called_once()
        assert any(
            "no result_callback configured" in rec.message
            for rec in caplog.records
        ), "callback=None 时应留下 no-callback 日志"

    @pytest.mark.asyncio
    async def test_background_callback_exception_swallowed(
        self, tmp_bg_skills_dir: Path,
    ):
        """callback 自身抛错不应破坏任务清理路径。"""
        from evopaw.agents.sub_agent_registry import (
            _reset_default_registry_for_tests,
            get_default_registry,
        )

        _reset_default_registry_for_tests()

        async def _broken_cb(task_id: str, skill_name: str, result_text: str) -> None:
            raise RuntimeError("callback 故意抛")

        d = SkillDispatcher(
            session_id="sid",
            routing_key="p2p:ou_x",
            skills_dir=tmp_bg_skills_dir,
            result_callback=_broken_cb,
        )

        mock_agent = AsyncMock(return_value="ok")
        import asyncio as _asyncio
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent", mock_agent,
        ):
            prompt = await d.dispatch("bg_skill", "")
            for _ in range(30):
                await _asyncio.sleep(0.01)

        assert prompt.startswith("已启动后台任务 task#")
        # 即使 callback 抛错，registry 仍应被注销
        assert get_default_registry().active_count("p2p:ou_x") == 0

    @pytest.mark.asyncio
    async def test_foreground_path_does_not_register(
        self, tmp_skills_dir: Path,
    ):
        """foreground（默认）路径不应触碰 SubAgentRegistry。"""
        from evopaw.agents.sub_agent_registry import (
            _reset_default_registry_for_tests,
            get_default_registry,
        )

        _reset_default_registry_for_tests()
        d = SkillDispatcher(
            session_id="sid", routing_key="p2p:ou_x", skills_dir=tmp_skills_dir,
        )

        mock_agent = AsyncMock(return_value="同步结果")
        with patch(
            "evopaw.agents.skill_agent.run_skill_agent", mock_agent,
        ):
            out = await d.dispatch("task_skill", "")

        assert out == "同步结果"
        assert get_default_registry().active_count("p2p:ou_x") == 0
