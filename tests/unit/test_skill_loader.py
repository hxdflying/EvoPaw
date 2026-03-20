"""SkillLoaderTool 单元测试"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xiaopaw.session.models import MessageEntry
from xiaopaw.tools.skill_loader import SkillLoaderInput, SkillLoaderTool


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_skills_dir(tmp_path: Path) -> Path:
    """在临时目录下创建一个标准 skills 目录结构。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # load_skills.yaml（无前导换行，直接从 key 开始）
    (skills_dir / "load_skills.yaml").write_text(
        "skills:\n"
        "  - name: ref_skill\n"
        "    type: reference\n"
        "    enabled: true\n"
        "  - name: task_skill\n"
        "    type: task\n"
        "    enabled: true\n"
        "  - name: disabled_skill\n"
        "    type: task\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    # ref_skill SKILL.md（frontmatter 必须从第一行 --- 开始）
    (skills_dir / "ref_skill").mkdir()
    (skills_dir / "ref_skill" / "SKILL.md").write_text(
        "---\n"
        "name: ref_skill\n"
        "description: 参考型 Skill，用于直接读取规范\n"
        "type: reference\n"
        'version: "1.0"\n'
        "---\n"
        "# Ref Skill 操作指南\n\n"
        "这是参考内容，直接返回给 Agent。\n",
        encoding="utf-8",
    )

    # task_skill SKILL.md
    (skills_dir / "task_skill").mkdir()
    (skills_dir / "task_skill" / "SKILL.md").write_text(
        "---\n"
        "name: task_skill\n"
        "description: 任务型 Skill，触发 Sub-Crew 在沙盒执行\n"
        "type: task\n"
        'version: "1.0"\n'
        "---\n"
        "# Task Skill 操作规范\n\n"
        "执行步骤：\n"
        "1. 读取输入文件\n"
        "2. 处理数据\n"
        "3. 写入输出\n",
        encoding="utf-8",
    )

    return skills_dir


def _make_tool(tmp_skills_dir: Path, session_id: str = "sid-test", routing_key: str = "") -> SkillLoaderTool:
    """构建一个指向临时 skills 目录的 SkillLoaderTool。"""
    with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir):
        tool = SkillLoaderTool(session_id=session_id, routing_key=routing_key)
    return tool


# ── SkillLoaderInput validator ─────────────────────────────────────────────────


class TestSkillLoaderInput:
    def test_str_passthrough(self):
        inp = SkillLoaderInput(skill_name="foo", task_context="描述文字")
        assert inp.task_context == "描述文字"

    def test_dict_converted_to_json_str(self):
        inp = SkillLoaderInput(skill_name="foo", task_context={"key": "value"})
        assert '"key"' in inp.task_context
        assert '"value"' in inp.task_context

    def test_list_converted_to_json_str(self):
        inp = SkillLoaderInput(skill_name="foo", task_context=[1, 2, 3])
        assert "[1, 2, 3]" in inp.task_context

    def test_none_becomes_empty_str(self):
        inp = SkillLoaderInput(skill_name="foo", task_context=None)
        assert inp.task_context == ""

    def test_default_empty(self):
        inp = SkillLoaderInput(skill_name="foo")
        assert inp.task_context == ""


# ── SkillLoaderTool._build_description ────────────────────────────────────────


class TestBuildDescription:
    def test_registry_populated(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        assert "ref_skill" in tool._skill_registry
        assert "task_skill" in tool._skill_registry
        # disabled_skill 不应在 registry 中
        assert "disabled_skill" not in tool._skill_registry

    def test_description_contains_xml_skills(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        assert "<available_skills>" in tool.description
        assert "<name>ref_skill</name>" in tool.description
        assert "<name>task_skill</name>" in tool.description
        assert "disabled_skill" not in tool.description

    def test_description_contains_session_path(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir, session_id="my-session-id")
        assert "/workspace/sessions/my-session-id/" in tool.description
        assert "uploads/" in tool.description
        assert "outputs/" in tool.description

    def test_description_mentions_json_schema_for_task_skill(self, tmp_skills_dir: Path):
        """工具 description 应提示：task 类型 Skill 的 task_context 必须定义 JSON schema。"""
        tool = _make_tool(tmp_skills_dir)
        assert "JSON schema" in tool.description

    def test_missing_manifest_graceful(self, tmp_path: Path):
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", empty_dir):
            tool = SkillLoaderTool(session_id="x")
        assert "未找到 load_skills.yaml" in tool.description

    def test_missing_skill_md_skipped(self, tmp_skills_dir: Path):
        """某个 Skill 目录缺少 SKILL.md 时，跳过而不崩溃。"""
        (tmp_skills_dir / "broken_skill").mkdir()
        (tmp_skills_dir / "load_skills.yaml").write_text(
            "skills:\n  - name: broken_skill\n    type: task\n    enabled: true\n"
        )
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir):
            tool = SkillLoaderTool(session_id="x")
        assert "broken_skill" not in tool._skill_registry

    def test_type_shown_in_xml(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        assert "<type>reference</type>" in tool.description
        assert "<type>task</type>" in tool.description


# ── _extract_frontmatter_description ─────────────────────────────────────────


class TestExtractFrontmatterDescription:
    """直接测试 _extract_frontmatter_description，通过一个空 skills 目录避免磁盘依赖。"""

    @pytest.fixture
    def tool(self, tmp_path: Path) -> SkillLoaderTool:
        """构建一个空 registry 的 SkillLoaderTool（空 load_skills.yaml）。"""
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()
        (empty_dir / "load_skills.yaml").write_text("skills: []\n")
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", empty_dir):
            return SkillLoaderTool(session_id="")

    def test_normal_extraction(self, tool: SkillLoaderTool):
        md = "---\ndescription: 这是描述\n---\n正文"
        assert tool._extract_frontmatter_description(md) == "这是描述"

    def test_long_description_truncated(self, tool: SkillLoaderTool):
        long_desc = "A" * 300
        md = f"---\ndescription: {long_desc}\n---\n正文"
        result = tool._extract_frontmatter_description(md)
        assert len(result) <= 203  # 200 chars + "..."
        assert result.endswith("...")

    def test_no_frontmatter_returns_empty(self, tool: SkillLoaderTool):
        md = "# 正文内容\n没有 frontmatter"
        assert tool._extract_frontmatter_description(md) == ""

    def test_missing_description_key_returns_empty(self, tool: SkillLoaderTool):
        md = "---\nname: foo\ntype: task\n---\n正文"
        assert tool._extract_frontmatter_description(md) == ""

    def test_quoted_description_with_colon_parsed(self, tool: SkillLoaderTool):
        """带冒号的 description 必须用引号包裹，否则 YAML 解析失败；引号包裹时应正确提取。"""
        md = '---\ndescription: "浏览网页。适合：阅读文章、填写表单。"\n---\n正文'
        result = tool._extract_frontmatter_description(md)
        assert "适合" in result
        assert result != ""

    def test_unquoted_colon_in_description_returns_empty(self, tool: SkillLoaderTool):
        """未加引号时冒号触发 YAML ScannerError，应静默返回空字符串（记录此边界行为）。"""
        md = "---\ndescription: Use for: reading articles\n---\n正文"
        result = tool._extract_frontmatter_description(md)
        assert result == ""


# ── _get_skill_instructions ────────────────────────────────────────────────────


class TestGetSkillInstructions:
    def test_frontmatter_stripped(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("ref_skill")
        assert "---" not in instructions.split("<sandbox_execution_directive>")[0]
        assert "Ref Skill 操作指南" in instructions

    def test_skill_base_placeholder_replaced(self, tmp_skills_dir: Path):
        """SKILL.md 里的 {skill_base} 占位符必须被实际沙盒路径替换，不能残留给 CrewAI 当模板变量。"""
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "运行方式：python {skill_base}/scripts/run.py\n",
            encoding="utf-8",
        )
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("task_skill")
        # {skill_base} 应被替换为实际路径
        assert "{skill_base}" not in instructions
        assert "/mnt/skills/task_skill/scripts/run.py" in instructions

    def test_underscore_skill_base_placeholder_replaced(self, tmp_skills_dir: Path):
        """feishu_ops 风格的 {_skill_base} 占位符也必须被替换。"""
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "python {_skill_base}/scripts/send.py\n",
            encoding="utf-8",
        )
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("task_skill")
        assert "{_skill_base}" not in instructions
        assert "/mnt/skills/task_skill/scripts/send.py" in instructions

    def test_remaining_braces_escaped(self, tmp_skills_dir: Path):
        """SKILL.md 正文中的其他 {var} 必须被转义为 {{var}}，防止 CrewAI 模板替换报错。"""
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "示例：print(f'Sheet: {sheet_name}')\n"
            "路由格式：p2p:{open_id}\n",
            encoding="utf-8",
        )
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("task_skill")
        # 单层 {var} 应已转义为双层 {{var}}（防止 CrewAI 当成模板变量）
        assert "{{sheet_name}}" in instructions
        assert "{{open_id}}" in instructions
        # 用 regex 确认没有残留的单层 {sheet_name}（不被另一个 { 前置）
        import re
        assert not re.search(r'(?<!\{)\{sheet_name\}(?!\})', instructions)
        assert not re.search(r'(?<!\{)\{open_id\}(?!\})', instructions)

    def test_session_id_placeholder_replaced(self, tmp_skills_dir: Path):
        """{session_id} 占位符（feishu_ops 风格）必须被实际 session_id 替换，不得残留给 CrewAI 模板引擎。"""
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "python /mnt/skills/feishu_ops/scripts/send_image.py \\\n"
            "    --image_path /workspace/sessions/{session_id}/outputs/chart.png\n",
            encoding="utf-8",
        )
        tool = _make_tool(tmp_skills_dir, session_id="sess-abc123")
        instructions = tool._get_skill_instructions("task_skill")
        # {session_id} 应被替换为实际 session_id
        assert "{session_id}" not in instructions
        assert "sess-abc123" in instructions
        assert "/workspace/sessions/sess-abc123/outputs/chart.png" in instructions

    def test_session_dir_placeholder_replaced(self, tmp_skills_dir: Path):
        """{session_dir} 占位符（web_browse 风格）必须被实际工作目录替换。"""
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "输出文件：写入 `{session_dir}/outputs/` 目录\n",
            encoding="utf-8",
        )
        tool = _make_tool(tmp_skills_dir, session_id="sess-xyz")
        instructions = tool._get_skill_instructions("task_skill")
        assert "{session_dir}" not in instructions
        assert "/workspace/sessions/sess-xyz/outputs/" in instructions

    def test_session_id_placeholder_empty_session(self, tmp_skills_dir: Path):
        """session_id 为空时，{session_id} 应替换为 <session_id> 占位符而非空字符串。"""
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "--file_path /workspace/sessions/{session_id}/outputs/report.pdf\n",
            encoding="utf-8",
        )
        tool = _make_tool(tmp_skills_dir, session_id="")
        instructions = tool._get_skill_instructions("task_skill")
        assert "{session_id}" not in instructions
        assert "<session_id>" in instructions

    def test_sandbox_directive_appended(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir, session_id="sess-123", routing_key="p2p:ou_abc")
        instructions = tool._get_skill_instructions("task_skill")
        assert "<sandbox_execution_directive>" in instructions
        assert "/workspace/sessions/sess-123/" in instructions
        assert "/mnt/skills/task_skill/" in instructions
        assert "p2p:ou_abc" in instructions

    def test_result_cached(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        r1 = tool._get_skill_instructions("ref_skill")
        r2 = tool._get_skill_instructions("ref_skill")
        assert r1 is r2  # 同一对象（缓存命中）


# ── _run / _arun dispatch ─────────────────────────────────────────────────────


class TestRunDispatch:
    def test_run_unknown_skill_returns_error(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        result = tool._run(skill_name="nonexistent", task_context="")
        assert "未找到 Skill" in result
        assert "nonexistent" in result

    @pytest.mark.asyncio
    async def test_arun_unknown_skill_returns_error(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        result = await tool._arun(skill_name="nonexistent", task_context="")
        assert "未找到 Skill" in result

    @pytest.mark.asyncio
    async def test_arun_reference_skill_returns_instructions(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        result = await tool._arun(skill_name="ref_skill", task_context="")
        assert "<skill_instructions>" in result
        assert "Ref Skill 操作指南" in result

    @pytest.mark.asyncio
    async def test_arun_task_skill_calls_build_crew(self, tmp_skills_dir: Path):
        """任务型 Skill 应调用 build_skill_crew 并 await akickoff。"""
        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value="task completed")

        with (
            patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir),
            patch(
                "xiaopaw.tools.skill_loader.build_skill_crew",
                return_value=mock_crew,
            ) as mock_build,
        ):
            tool = SkillLoaderTool(session_id="sid-xxx")
            result = await tool._arun(skill_name="task_skill", task_context="do it")

        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args
        assert call_kwargs.kwargs["skill_name"] == "task_skill"
        assert call_kwargs.kwargs["session_id"] == "sid-xxx"
        mock_crew.akickoff.assert_awaited_once()
        assert "task completed" in result

    @pytest.mark.asyncio
    async def test_arun_task_skill_extra_template_vars_as_self_mapping(self, tmp_skills_dir: Path):
        """SKILL.md 中含 {var} 的代码示例（已转义为 {{var}}）时，
        akickoff inputs 应包含这些变量的自映射，防止 CrewAI 'Template variable not found' 报错。"""
        (tmp_skills_dir / "task_skill" / "SKILL.md").write_text(
            "---\nname: task_skill\ndescription: test\ntype: task\nversion: \"1.0\"\n---\n"
            "示例：print(f'Sheet: {sheet_name}')\n"
            "路由格式：p2p:{open_id}\n",
            encoding="utf-8",
        )
        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value="done")

        with (
            patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir),
            patch("xiaopaw.tools.skill_loader.build_skill_crew", return_value=mock_crew),
        ):
            tool = SkillLoaderTool(session_id="sid-test")
            await tool._arun(skill_name="task_skill", task_context="process xlsx")

        inputs = mock_crew.akickoff.await_args.kwargs["inputs"]
        # 基础 inputs 必须存在
        assert "task_context" in inputs
        assert "skill_name" in inputs
        # {{sheet_name}} 和 {{open_id}} 被 CrewAI 扫描器识别，应以自映射方式注入
        assert "sheet_name" in inputs
        assert "open_id" in inputs

    def test_run_reference_skill_sync_path(self, tmp_skills_dir: Path):
        """同步 _run() 应通过 ThreadPoolExecutor 正确执行参考型 Skill。"""
        tool = _make_tool(tmp_skills_dir)
        result = tool._run(skill_name="ref_skill", task_context="")
        assert "skill_instructions" in result
        assert "Ref Skill 操作指南" in result


# ── _handle_history_reader ────────────────────────────────────────────────────


def _make_msg(role: str, content: str) -> MessageEntry:
    return MessageEntry(role=role, content=content, ts=0)


class TestHandleHistoryReader:
    """验证 history_reader 内联处理逻辑（无沙盒、无 session_id）。"""

    def _tool_with_history(self, tmp_skills_dir: Path, n: int) -> SkillLoaderTool:
        history = [_make_msg("user" if i % 2 == 0 else "assistant", f"msg-{i}") for i in range(n)]
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir):
            tool = SkillLoaderTool(session_id="sid", history_all=history)
        return tool

    def test_empty_history(self, tmp_skills_dir: Path):
        tool = self._tool_with_history(tmp_skills_dir, 0)
        import json
        result = json.loads(tool._handle_history_reader(""))
        assert result["errcode"] == 0
        assert result["data"]["total"] == 0
        assert result["data"]["messages"] == []

    def test_first_page(self, tmp_skills_dir: Path):
        import json
        tool = self._tool_with_history(tmp_skills_dir, 35)
        result = json.loads(tool._handle_history_reader('{"page": 1, "page_size": 20}'))
        assert result["errcode"] == 0
        assert result["data"]["total"] == 35
        assert len(result["data"]["messages"]) == 20
        assert result["data"]["page"] == 1
        assert result["data"]["total_pages"] == 2
        # 第一页应包含最旧的消息
        assert result["data"]["messages"][0]["content"] == "msg-0"

    def test_second_page(self, tmp_skills_dir: Path):
        import json
        tool = self._tool_with_history(tmp_skills_dir, 35)
        result = json.loads(tool._handle_history_reader('{"page": 2, "page_size": 20}'))
        assert len(result["data"]["messages"]) == 15  # 35 - 20

    def test_page_size_capped_at_50(self, tmp_skills_dir: Path):
        import json
        tool = self._tool_with_history(tmp_skills_dir, 100)
        result = json.loads(tool._handle_history_reader('{"page": 1, "page_size": 999}'))
        assert len(result["data"]["messages"]) == 50

    def test_invalid_json_uses_defaults(self, tmp_skills_dir: Path):
        import json
        tool = self._tool_with_history(tmp_skills_dir, 5)
        result = json.loads(tool._handle_history_reader("自然语言描述，无json"))
        assert result["errcode"] == 0
        assert result["data"]["page"] == 1
        assert result["data"]["page_size"] == 20

    def test_message_roles_preserved(self, tmp_skills_dir: Path):
        import json
        history = [
            _make_msg("user", "用户问题"),
            _make_msg("assistant", "助手回答"),
        ]
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir):
            tool = SkillLoaderTool(session_id="s", history_all=history)
        result = json.loads(tool._handle_history_reader(""))
        msgs = result["data"]["messages"]
        assert msgs[0] == {"role": "user", "content": "用户问题"}
        assert msgs[1] == {"role": "assistant", "content": "助手回答"}

    @pytest.mark.asyncio
    async def test_arun_history_reader_intercepted(self, tmp_skills_dir: Path):
        """history_reader 调用应被内联拦截，不触发 Sub-Crew。"""
        import json
        # 注册 history_reader 为 reference 类型
        (tmp_skills_dir / "history_reader").mkdir(exist_ok=True)
        (tmp_skills_dir / "history_reader" / "SKILL.md").write_text(
            "---\nname: history_reader\ndescription: 读取历史\ntype: reference\nversion: \"2.0\"\n---\n内容\n"
        )
        (tmp_skills_dir / "load_skills.yaml").write_text(
            "skills:\n  - name: history_reader\n    type: reference\n    enabled: true\n"
        )
        history = [_make_msg("user", "早期消息")]
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir):
            tool = SkillLoaderTool(session_id="s", history_all=history)

        result = await tool._arun(skill_name="history_reader", task_context='{"page": 1}')
        parsed = json.loads(result)
        assert parsed["errcode"] == 0
        assert parsed["data"]["messages"][0]["content"] == "早期消息"


# ── history_all parameter ─────────────────────────────────────────────────────


class TestHistoryAllParam:
    def test_default_empty(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        assert tool._history_all == []

    def test_populated_from_init(self, tmp_skills_dir: Path):
        history = [_make_msg("user", "hello"), _make_msg("assistant", "world")]
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir):
            tool = SkillLoaderTool(session_id="s", history_all=history)
        assert len(tool._history_all) == 2
        assert tool._history_all[0].content == "hello"

    def test_none_history_all_gives_empty_list(self, tmp_skills_dir: Path):
        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", tmp_skills_dir):
            tool = SkillLoaderTool(session_id="s", history_all=None)
        assert tool._history_all == []


# ── routing_key PrivateAttr ───────────────────────────────────────────────────


class TestRoutingKeyParam:
    def test_default_empty(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        assert tool._routing_key == ""

    def test_populated_from_init(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir, routing_key="p2p:ou_abc123")
        assert tool._routing_key == "p2p:ou_abc123"

    def test_group_routing_key(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir, routing_key="group:oc_xyz789")
        assert tool._routing_key == "group:oc_xyz789"

    def test_routing_key_in_sandbox_directive(self, tmp_skills_dir: Path):
        """routing_key 应注入到 sandbox_execution_directive，供 Sub-Crew 使用。"""
        tool = _make_tool(tmp_skills_dir, routing_key="p2p:ou_testuser")
        instructions = tool._get_skill_instructions("task_skill")
        assert "p2p:ou_testuser" in instructions
        # 确保在 sandbox_execution_directive 标签内
        directive_section = instructions.split("<sandbox_execution_directive>")[1]
        assert "p2p:ou_testuser" in directive_section

    def test_empty_routing_key_shows_placeholder(self, tmp_skills_dir: Path):
        """未设置 routing_key 时，显示占位提示而非空字符串。"""
        tool = _make_tool(tmp_skills_dir, routing_key="")
        instructions = tool._get_skill_instructions("task_skill")
        assert "<由系统注入" in instructions


# ── sandbox_directive 新工具说明 ──────────────────────────────────────────────


class TestSandboxDirectiveNewTools:
    """验证 sandbox_directive 中新增的浏览器工具和 sandbox_convert_to_markdown 说明。"""

    def test_sandbox_convert_to_markdown_mentioned(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("task_skill")
        assert "sandbox_convert_to_markdown" in instructions

    def test_browser_navigate_mentioned(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("task_skill")
        assert "browser_navigate" in instructions

    def test_browser_screenshot_mentioned(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("task_skill")
        assert "browser_screenshot" in instructions

    def test_browser_get_clickable_elements_mentioned(self, tmp_skills_dir: Path):
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("task_skill")
        assert "browser_get_clickable_elements" in instructions

    def test_core_tools_still_present(self, tmp_skills_dir: Path):
        """原有 4 个核心工具说明不应丢失。"""
        tool = _make_tool(tmp_skills_dir)
        instructions = tool._get_skill_instructions("task_skill")
        assert "sandbox_execute_bash" in instructions
        assert "sandbox_execute_code" in instructions
        assert "sandbox_file_operations" in instructions
        assert "sandbox_str_replace_editor" in instructions


# ── 真实 SKILL.md 冒烟测试 ────────────────────────────────────────────────────


class TestRealSkillMdSmoke:
    """使用生产目录中真实的 SKILL.md 文件，验证 _get_skill_instructions
    不会遗留未转义的 {var} 占位符，且 akickoff inputs 包含所有必要的自映射。

    【补缺口】这组测试弥补了以下两处盲点：
      1. 单元测试 fixture 用的 SKILL.md 内容简单，不含 {var} 代码示例；
      2. 触发真实 SKILL.md 的集成测试需要 sandbox mark，日常 CI 不运行。
    通过直接读取生产 SKILL.md 并 mock akickoff，在无需 sandbox 的情况下
    验证 inputs 构建逻辑对真实文件有效。
    """

    # 只检验 task 型（reference 型不走 akickoff）
    TASK_SKILLS = ["xlsx", "web_browse", "baidu_search", "feishu_ops", "scheduler_mgr", "pdf", "docx", "pptx"]

    def _real_skills_dir(self) -> Path:
        """返回生产 skills 目录，测试需要该目录存在。"""
        return Path(__file__).parents[2] / "xiaopaw" / "skills"

    @pytest.mark.parametrize("skill_name", TASK_SKILLS)
    def test_instructions_have_no_bare_single_braces(self, skill_name: str):
        """处理后的 instructions 中不应有未转义的 {var}（只允许 {{var}} 形式）。

        如果 {skill_base}/{session_id}/{session_dir} 替换逻辑漏掉某个占位符，
        这里会直接捕获。
        """
        import re

        skills_dir = self._real_skills_dir()
        skill_md = skills_dir / skill_name / "SKILL.md"
        if not skill_md.exists():
            pytest.skip(f"生产 SKILL.md 不存在：{skill_md}")

        # 构建一个指向真实 skills 目录的 SkillLoaderTool
        load_skills = skills_dir / "load_skills.yaml"
        if not load_skills.exists():
            pytest.skip("load_skills.yaml 不存在")

        with patch("xiaopaw.tools.skill_loader._SKILLS_DIR", skills_dir):
            tool = SkillLoaderTool(session_id="smoke-test-sid")

        if skill_name not in tool._skill_registry:
            pytest.skip(f"{skill_name} 未在 load_skills.yaml 中启用")

        instructions = tool._get_skill_instructions(skill_name)

        # 在 sandbox_execution_directive 之前的部分（来自 SKILL.md 正文）
        # 不应存在单层 {var}（已被转义为 {{var}}）
        skill_body = instructions.split("<sandbox_execution_directive>")[0]
        bare = re.findall(r'(?<!\{)\{([A-Za-z_][A-Za-z0-9_\-]*)\}(?!\})', skill_body)
        assert not bare, (
            f"{skill_name} SKILL.md 处理后仍有未转义的 {{var}}：{bare}\n"
            "请检查是否有新的占位符需要在 _get_skill_instructions 中显式替换。"
        )

    @pytest.mark.parametrize("skill_name", TASK_SKILLS)
    @pytest.mark.asyncio
    async def test_akickoff_inputs_cover_all_crewai_vars(self, skill_name: str):
        """akickoff 收到的 inputs 应包含 CrewAI 正则能找到的所有变量名。

        【直接复现 bug】这是 'Template variable not found' 的精确回归测试：
        用真实 SKILL.md + mock akickoff，检验 inputs 中不缺少任何变量。
        """
        import re
        from xiaopaw.tools.skill_loader import _CREWAI_VAR_PATTERN

        skills_dir = self._real_skills_dir()
        skill_md = skills_dir / skill_name / "SKILL.md"
        if not skill_md.exists():
            pytest.skip(f"生产 SKILL.md 不存在：{skill_md}")

        mock_crew = MagicMock()
        mock_crew.akickoff = AsyncMock(return_value="ok")

        with (
            patch("xiaopaw.tools.skill_loader._SKILLS_DIR", skills_dir),
            patch("xiaopaw.tools.skill_loader.build_skill_crew", return_value=mock_crew),
        ):
            tool = SkillLoaderTool(session_id="smoke-test-sid")
            if skill_name not in tool._skill_registry:
                pytest.skip(f"{skill_name} 未在 load_skills.yaml 中启用")
            await tool._arun(skill_name=skill_name, task_context="smoke test")

        inputs = mock_crew.akickoff.await_args.kwargs["inputs"]
        instructions = tool._get_skill_instructions(skill_name)
        expected_vars = set(_CREWAI_VAR_PATTERN.findall(instructions))

        missing = expected_vars - set(inputs.keys())
        assert not missing, (
            f"{skill_name}：akickoff inputs 缺少这些变量（CrewAI 会报 'Template variable not found'）：{missing}"
        )

