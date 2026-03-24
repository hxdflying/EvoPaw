"""bootstrap 单元测试

💡【第19/20课·Bootstrap】build_bootstrap_prompt() 从 workspace 目录读取
soul.md / user.md / agent.md / memory.md（前200行），
拼装成 <soul> <user_profile> <agent_rules> <memory_index> 四段式 backstory。
"""

from __future__ import annotations

import pytest

from xiaopaw.memory.bootstrap import build_bootstrap_prompt


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path):
    """标准 4 文件 workspace"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "soul.md").write_text("# XiaoPaw 身份设定\n你是 XiaoPaw。", encoding="utf-8")
    (ws / "user.md").write_text("# 用户画像\n用户：晓寒", encoding="utf-8")
    (ws / "agent.md").write_text("# Agent 规范\n禁止 FileWriterTool。", encoding="utf-8")
    (ws / "memory.md").write_text("# 记忆索引\n## 工作项目\n", encoding="utf-8")
    return ws


# ── 四个 section 存在 ───────────────────────────────────────────


class TestFourSections:
    def test_all_four_tags_present(self, workspace):
        """返回字符串包含全部四个 XML 标签"""
        result = build_bootstrap_prompt(workspace)
        for tag in ["<soul>", "</soul>", "<user_profile>", "</user_profile>",
                    "<agent_rules>", "</agent_rules>", "<memory_index>", "</memory_index>"]:
            assert tag in result, f"缺少标签 {tag}"

    def test_file_content_injected_into_correct_section(self, workspace):
        """各文件内容出现在对应 section 中"""
        result = build_bootstrap_prompt(workspace)
        # soul.md 内容
        soul_start = result.index("<soul>")
        soul_end = result.index("</soul>")
        assert "XiaoPaw 身份设定" in result[soul_start:soul_end]

        # user.md 内容
        user_start = result.index("<user_profile>")
        user_end = result.index("</user_profile>")
        assert "用户：晓寒" in result[user_start:user_end]

        # agent.md 内容
        agent_start = result.index("<agent_rules>")
        agent_end = result.index("</agent_rules>")
        assert "禁止 FileWriterTool" in result[agent_start:agent_end]

        # memory.md 内容
        mem_start = result.index("<memory_index>")
        mem_end = result.index("</memory_index>")
        assert "记忆索引" in result[mem_start:mem_end]


# ── 缺失文件容错 ─────────────────────────────────────────────────


class TestMissingFiles:
    def test_missing_soul_no_exception(self, workspace):
        """soul.md 缺失时不抛异常，<soul> section 不出现"""
        (workspace / "soul.md").unlink()
        result = build_bootstrap_prompt(workspace)  # should not raise
        assert "<soul>" not in result
        # 其他 section 仍存在
        assert "<user_profile>" in result
        assert "<agent_rules>" in result
        assert "<memory_index>" in result

    def test_missing_user_md_no_exception(self, workspace):
        """user.md 缺失时不抛异常，其余 section 正常"""
        (workspace / "user.md").unlink()
        result = build_bootstrap_prompt(workspace)
        assert "<user_profile>" not in result
        assert "禁止 FileWriterTool" in result  # agent.md 仍在

    def test_missing_agent_md_no_exception(self, workspace):
        """agent.md 缺失时不抛异常"""
        (workspace / "agent.md").unlink()
        result = build_bootstrap_prompt(workspace)
        assert "<agent_rules>" not in result

    def test_missing_memory_md_no_exception(self, workspace):
        """memory.md 缺失时不抛异常，<memory_index> 不出现"""
        (workspace / "memory.md").unlink()
        result = build_bootstrap_prompt(workspace)
        assert "<memory_index>" not in result

    def test_empty_workspace_dir_returns_empty_string(self, tmp_path):
        """空目录返回空字符串（四个文件均缺失）"""
        empty = tmp_path / "empty"
        empty.mkdir()
        result = build_bootstrap_prompt(empty)
        assert result == ""

    def test_nonexistent_workspace_returns_empty_string(self, tmp_path):
        """workspace 目录不存在时返回空字符串"""
        result = build_bootstrap_prompt(tmp_path / "nonexistent")
        assert result == ""


# ── memory.md 200 行截断 ─────────────────────────────────────────


class TestMemoryTruncation:
    def test_memory_md_over_200_lines_truncated(self, workspace):
        """memory.md 超 200 行时，只注入前 200 行"""
        long_content = "\n".join([f"line {i}" for i in range(300)])
        (workspace / "memory.md").write_text(long_content, encoding="utf-8")
        result = build_bootstrap_prompt(workspace)
        assert "line 199" in result
        assert "line 200" not in result

    def test_memory_md_exactly_200_lines_complete(self, workspace):
        """恰好 200 行时，全量注入（边界值）"""
        content = "\n".join([f"line {i}" for i in range(200)])
        (workspace / "memory.md").write_text(content, encoding="utf-8")
        result = build_bootstrap_prompt(workspace)
        assert "line 199" in result  # 第200行（0-indexed）

    def test_memory_md_under_200_lines_complete(self, workspace):
        """不足 200 行时，全量注入"""
        (workspace / "memory.md").write_text("line1\nline2\nline3", encoding="utf-8")
        result = build_bootstrap_prompt(workspace)
        assert "line1" in result
        assert "line3" in result

    def test_memory_md_empty_file(self, workspace):
        """memory.md 为空文件时，<memory_index> section 存在但内容为空"""
        (workspace / "memory.md").write_text("", encoding="utf-8")
        result = build_bootstrap_prompt(workspace)
        assert "<memory_index>" in result


# ── section 顺序 ─────────────────────────────────────────────────


class TestSectionOrder:
    def test_sections_in_order(self, workspace):
        """section 顺序：soul → user_profile → agent_rules → memory_index"""
        result = build_bootstrap_prompt(workspace)
        soul_pos = result.index("<soul>")
        user_pos = result.index("<user_profile>")
        agent_pos = result.index("<agent_rules>")
        mem_pos = result.index("<memory_index>")
        assert soul_pos < user_pos < agent_pos < mem_pos
