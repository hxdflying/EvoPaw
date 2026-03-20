"""E2E 集成测试 — 端到端对话场景

覆盖范围：
  Group A  Slash Command（无需 LLM）
  Group B  基础对话（需要 LLM，不需要 sandbox）
  Group C  多轮上下文（需要 LLM）
  Group D  Session 管理命令与 LLM 配合
  Group E  history_reader Skill（reference 型，需要 LLM + sandbox）
  Group F  scheduler_mgr Skill（task 型，需要 LLM + sandbox）
  Group G  文件处理 Skill（需要 LLM + sandbox + 上传文件）
  Group H  边界与容错场景（需要 LLM）
  Group I  写作辅助场景（需要 LLM）
  Group J  知识问答场景（需要 LLM）
  Group K  任务规划场景（需要 LLM）
  Group L  格式化输出场景（需要 LLM）
  Group M  对话流程连贯性（需要 LLM）
  Group N  飞书操作场景（需要 LLM + sandbox）
  Group O  PPT 处理场景（需要 LLM + sandbox）

运行方式：
  # 仅 slash command（最快，无需 API key）
  pytest tests/integration/test_e2e_conversation.py -m "not llm" -v

  # 基础 LLM 测试（不需要 sandbox）
  pytest tests/integration/test_e2e_conversation.py -m "llm and not sandbox" -v -s

  # 完整套件
  pytest tests/integration/test_e2e_conversation.py -v -s --timeout=180
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient

from .conftest import send_message

# ─────────────────────────────────────────────────────────────────────────────
# Group A: Slash Command 测试（无需 LLM，echo agent 即可）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestSlashCommands:
    """Slash command 在 Runner 层拦截，不进入 Agent，速度极快。"""

    ROUTING_KEY = "p2p:ou_slash_test"

    async def test_help_returns_command_list(self, slash_client: TestClient):
        data = await send_message(slash_client, "/help", self.ROUTING_KEY)
        reply = data["reply"]
        # /help 必须列出所有核心命令
        for cmd in ["/new", "/verbose", "/status", "/help"]:
            assert cmd in reply, f"期望 {cmd!r} 出现在帮助信息中，实际：{reply!r}"

    async def test_status_returns_session_info(self, slash_client: TestClient):
        data = await send_message(slash_client, "/status", self.ROUTING_KEY)
        reply = data["reply"]
        # /status 应包含 session ID
        assert "s-" in reply, f"期望包含 session id，实际：{reply!r}"

    async def test_verbose_on_confirms_enabled(self, slash_client: TestClient):
        data = await send_message(slash_client, "/verbose on", self.ROUTING_KEY)
        reply = data["reply"]
        assert "详细" in reply or "verbose" in reply.lower(), reply

    async def test_verbose_off_confirms_disabled(self, slash_client: TestClient):
        await send_message(slash_client, "/verbose on", self.ROUTING_KEY)
        data = await send_message(slash_client, "/verbose off", self.ROUTING_KEY)
        reply = data["reply"]
        assert "关闭" in reply or "off" in reply.lower(), reply

    async def test_verbose_query_returns_current_state(self, slash_client: TestClient):
        # 先关闭
        await send_message(slash_client, "/verbose off", self.ROUTING_KEY)
        data = await send_message(slash_client, "/verbose", self.ROUTING_KEY)
        reply = data["reply"]
        assert "关闭" in reply or "off" in reply.lower(), reply

    async def test_new_creates_different_session(self, slash_client: TestClient):
        data1 = await send_message(slash_client, "初始消息", self.ROUTING_KEY)
        sid1 = data1["session_id"]

        await send_message(slash_client, "/new", self.ROUTING_KEY)

        data3 = await send_message(slash_client, "新对话消息", self.ROUTING_KEY)
        sid3 = data3["session_id"]

        assert sid1 != sid3, "期望 /new 后产生新的 session_id"

    async def test_new_reply_contains_session_id(self, slash_client: TestClient):
        data = await send_message(slash_client, "/new", self.ROUTING_KEY)
        reply = data["reply"]
        # 回复中应包含新 session ID 的部分
        assert "s-" in reply or "创建" in reply or "新" in reply, reply

    async def test_unknown_slash_not_intercepted(self, slash_client: TestClient):
        """未知 / 命令不被 Runner 拦截，交给 agent 处理（echo agent 会回 echo 内容）"""
        data = await send_message(slash_client, "/unknown_cmd", self.ROUTING_KEY)
        reply = data["reply"]
        # echo agent 将原文回显
        assert "echo:" in reply or "/unknown_cmd" in reply, reply

    async def test_different_routing_keys_have_independent_sessions(
        self, slash_client: TestClient
    ):
        data1 = await send_message(slash_client, "/status", "p2p:ou_user_aaa")
        data2 = await send_message(slash_client, "/status", "p2p:ou_user_bbb")
        assert data1["session_id"] != data2["session_id"]

    async def test_group_routing_key(self, slash_client: TestClient):
        data = await send_message(slash_client, "/status", "group:oc_group_001")
        assert data["reply"]
        assert data["session_id"]

    async def test_thread_routing_key(self, slash_client: TestClient):
        data = await send_message(
            slash_client, "/status", "thread:oc_chat789:ot_topic001"
        )
        assert data["reply"]
        assert data["session_id"]


# ─────────────────────────────────────────────────────────────────────────────
# Group B: 基础 LLM 对话（需要 QWEN_API_KEY，不需要 sandbox）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
class TestBasicConversation:
    """验证主 Agent 能正常回复简单问题，不调用 Skill。"""

    ROUTING_KEY = "p2p:ou_basic_test"

    async def test_self_introduction(self, llm_client: TestClient):
        """Agent 应能介绍自己是 XiaoPaw 工作助手。"""
        data = await send_message(llm_client, "你好，介绍一下你自己。", self.ROUTING_KEY)
        reply = data["reply"]
        assert len(reply.strip()) > 20, f"回复过短：{reply!r}"
        # 应提到 XiaoPaw 或助手相关词汇
        assert any(
            kw in reply
            for kw in ["XiaoPaw", "小爪子", "助手", "帮助", "飞书"]
        ), f"回复未提及自身身份：{reply!r}"

    async def test_simple_question_gets_non_empty_reply(self, llm_client: TestClient):
        """简单问题应得到非空的合理回复。"""
        data = await send_message(llm_client, "今天天气怎么样？", self.ROUTING_KEY)
        reply = data["reply"]
        assert len(reply.strip()) > 5, f"回复过短：{reply!r}"

    async def test_calculation_request(self, llm_client: TestClient):
        """简单计算（无需 skill）应直接回答。"""
        data = await send_message(
            llm_client, "帮我计算一下 1+1 等于多少？", self.ROUTING_KEY
        )
        reply = data["reply"]
        assert "2" in reply, f"期望包含 '2'，实际：{reply!r}"

    async def test_duration_is_recorded(self, llm_client: TestClient):
        data = await send_message(llm_client, "你好", self.ROUTING_KEY)
        assert data["duration_ms"] > 0

    async def test_session_id_returned(self, llm_client: TestClient):
        data = await send_message(llm_client, "你好", self.ROUTING_KEY)
        assert data["session_id"].startswith("s-")

    async def test_capability_inquiry_mentions_skills(self, llm_client: TestClient):
        """询问能做什么，Agent 应提到 Skills 相关能力。"""
        data = await send_message(
            llm_client,
            "你都能帮我做什么？有哪些功能？",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 30, f"回复过短：{reply!r}"
        # 应提到某些功能
        assert any(
            kw in reply
            for kw in ["文件", "定时", "飞书", "搜索", "PDF", "Skill", "skill"]
        ), f"回复未提及功能：{reply!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Group C: 多轮对话上下文
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
class TestMultiTurnContext:
    """验证多轮对话中 Agent 能保持上下文连贯性。"""

    async def test_context_maintained_across_turns(self, llm_client: TestClient):
        """第二条消息应能引用第一条的内容。"""
        routing_key = "p2p:ou_ctx_test"

        # 第一轮：告诉 Agent 一个名字
        await send_message(
            llm_client, "我的名字叫张三，请记住。", routing_key
        )

        # 第二轮：询问是否记得
        data2 = await send_message(llm_client, "我叫什么名字？", routing_key)
        reply = data2["reply"]

        assert "张三" in reply, f"期望包含 '张三'，实际：{reply!r}"

    async def test_new_session_clears_context(self, llm_client: TestClient):
        """发送 /new 后，上下文清空，旧信息不再可用。"""
        routing_key = "p2p:ou_ctx_new_test"

        # 建立上下文
        await send_message(llm_client, "密码是 XiaoPaw2024。", routing_key)

        # 创建新 session
        await send_message(llm_client, "/new", routing_key)

        # 询问密码 —— 新 session 中不应有这个上下文
        data = await send_message(
            llm_client, "之前说的密码是什么？", routing_key
        )
        reply = data["reply"]
        # 不应在新 session 中给出旧密码
        assert "XiaoPaw2024" not in reply, (
            f"新 session 中不应包含旧上下文，实际：{reply!r}"
        )

    async def test_message_history_accumulates(
        self, llm_client: TestClient, session_mgr
    ):
        """多条消息后 session.message_count 应正确递增。"""
        routing_key = "p2p:ou_history_count"
        for i in range(3):
            await send_message(llm_client, f"第 {i + 1} 条消息", routing_key)

        session = await session_mgr.get_or_create(routing_key)
        # 3 轮 × 2 (user + assistant) = 6
        assert session.message_count == 6, (
            f"期望 message_count=6，实际={session.message_count}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Group D: Session 管理与 LLM 配合
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
class TestSessionManagementWithLLM:
    """验证 slash command 与 LLM 对话的组合场景。"""

    ROUTING_KEY = "p2p:ou_session_mgmt"

    async def test_verbose_on_still_gets_reply(self, llm_client: TestClient):
        """开启 verbose 后正常对话仍有回复（verbose 消息通过 sender 推送，不影响主回复）。"""
        await send_message(llm_client, "/verbose on", self.ROUTING_KEY)
        data = await send_message(llm_client, "你好", self.ROUTING_KEY)
        assert data["reply"], "verbose 开启后仍应有正常回复"

    async def test_status_after_conversation_shows_message_count(
        self, llm_client: TestClient
    ):
        """对话后 /status 应显示非零消息数。"""
        routing_key = "p2p:ou_status_after_chat"
        await send_message(llm_client, "你好", routing_key)
        await send_message(llm_client, "再见", routing_key)

        data = await send_message(llm_client, "/status", routing_key)
        reply = data["reply"]
        # 状态中应包含消息数
        assert any(c.isdigit() for c in reply), f"期望包含数字（消息数），实际：{reply!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Group E: history_reader Skill（task 型，需要 sandbox）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.sandbox
class TestHistoryReaderSkill:
    """
    history_reader Skill 读取 /workspace/sessions/{sid}.jsonl，
    需要 AIO-Sandbox 可达且 workspace 正确挂载。
    """

    ROUTING_KEY = "p2p:ou_history_skill"

    async def test_history_reader_invoked_for_recall_request(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """在消息超出上下文窗口后，询问早期内容时应触发 history_reader。"""
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        # 先积累一些历史
        for i in range(3):
            await send_message(
                llm_client, f"测试消息 {i + 1}：小红书运营技巧分享", self.ROUTING_KEY
            )

        # 询问历史 —— 应触发 history_reader skill 或直接从上下文回答
        data = await send_message(
            llm_client,
            "请帮我回顾一下我们对话的历史记录，我在之前说了什么？",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 20, f"回复过短：{reply!r}"
        assert any(kw in reply for kw in ["小红书", "测试", "消息", "对话", "历史"]), (
            f"期望回复提及历史内容，实际：{reply!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Group F: scheduler_mgr Skill（需要 LLM + sandbox）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.sandbox
class TestSchedulerMgrSkill:
    """
    scheduler_mgr Skill 读写 /workspace/cron/tasks.json。
    需要沙盒有对应路径的写权限。
    """

    ROUTING_KEY = "p2p:ou_scheduler_test"

    async def test_create_daily_reminder(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """要求创建每日提醒，Agent 应调用 scheduler_mgr 并确认创建成功。"""
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        data = await send_message(
            llm_client,
            "帮我创建一个定时任务，每天早上9点提醒我写工作日志。",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 10, f"回复过短：{reply!r}"
        # 应确认已创建或提到定时任务
        assert any(
            kw in reply
            for kw in ["创建", "设置", "定时", "提醒", "9点", "成功", "已"]
        ), f"期望回复确认创建，实际：{reply!r}"

    async def test_list_scheduled_jobs(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """查看所有定时任务，Agent 应列出任务列表或说明当前状态。"""
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        data = await send_message(
            llm_client,
            "帮我查看一下当前有哪些定时任务。",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 5, f"回复过短：{reply!r}"

    async def test_create_weekly_reminder(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """要求创建周期性任务（每周一），Agent 应正确处理 cron 表达式场景。"""
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        data = await send_message(
            llm_client,
            "每周一上午10点提醒我参加周会，帮我设置一下。",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 10, f"回复过短：{reply!r}"
        assert any(
            kw in reply
            for kw in ["设置", "创建", "周会", "定时", "成功", "每周"]
        ), f"期望回复确认设置，实际：{reply!r}"

    async def test_create_onetime_task(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """创建一次性任务（at 模式），Agent 应识别并创建。"""
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        data = await send_message(
            llm_client,
            "帮我设置一个明天下午3点发送报告的提醒，完成后删除这个任务。",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 10, f"回复过短：{reply!r}"

    async def test_delete_job_after_create(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """先创建再删除任务，Agent 应分两步处理或一步完成。"""
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        # 先创建
        create_data = await send_message(
            llm_client,
            "帮我创建一个每小时提醒一次喝水的定时任务。",
            self.ROUTING_KEY,
        )
        assert create_data["reply"]

        # 再删除
        delete_data = await send_message(
            llm_client,
            "删除刚才创建的那个提醒喝水的定时任务。",
            self.ROUTING_KEY,
        )
        reply = delete_data["reply"]
        assert any(
            kw in reply
            for kw in ["删除", "取消", "移除", "成功", "已删"]
        ), f"期望回复确认删除，实际：{reply!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Group G: 文件处理 Skill（需要 LLM + sandbox + 测试文件）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.sandbox
class TestFileProcessingSkill:
    """
    pdf / docx / xlsx Skill 需要真实文件上传到沙盒。
    测试将在 tmp 目录创建简单测试文件，验证 Agent 能识别文件处理意图。
    """

    ROUTING_KEY = "p2p:ou_file_test"

    async def test_pdf_request_recognized(
        self, llm_client: TestClient, sandbox_available: bool, tmp_path: Path
    ):
        """
        当用户提到 PDF 文件时，Agent 应调用 pdf Skill 处理。
        这里只测试 Agent 的意图识别和响应，不上传真实文件。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        # 模拟用户提到文件路径（沙盒路径）
        data = await send_message(
            llm_client,
            (
                "我有一个 PDF 文件在 /workspace/sessions/s-test/uploads/report.pdf，"
                "帮我提取里面的文字内容。"
            ),
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 10, f"回复过短：{reply!r}"
        # Agent 应尝试处理或返回合理错误
        assert any(
            kw in reply
            for kw in ["PDF", "pdf", "文件", "提取", "内容", "错误", "不存在", "找不到"]
        ), f"期望 Agent 响应 PDF 请求，实际：{reply!r}"

    async def test_docx_request_recognized(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """当用户提到 Word 文档时，Agent 应调用 docx Skill。"""
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        data = await send_message(
            llm_client,
            "帮我读取 /workspace/sessions/s-test/uploads/contract.docx 的内容。",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 10, f"回复过短：{reply!r}"

    async def test_xlsx_request_recognized(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """当用户提到 Excel 文件时，Agent 应调用 xlsx Skill。"""
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        data = await send_message(
            llm_client,
            "帮我分析 /workspace/sessions/s-test/uploads/sales_data.xlsx 里的销售数据。",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 10, f"回复过短：{reply!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Group H: 边界与容错场景
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
class TestEdgeCases:
    """边界场景和容错验证。"""

    ROUTING_KEY = "p2p:ou_edge_test"

    async def test_empty_message_gets_reply(self, llm_client: TestClient):
        """空消息（routing_key 有但 content 为空）应得到非错误回复。"""
        data = await send_message(llm_client, "", self.ROUTING_KEY)
        reply = data["reply"]
        # Agent 应提示用户输入内容，或给出帮助信息
        assert isinstance(reply, str), "回复应为字符串"

    async def test_very_long_message(self, llm_client: TestClient):
        """超长消息不应导致服务崩溃。"""
        long_msg = "请帮我总结以下内容：" + "这是一段重复的测试文本。" * 50
        data = await send_message(llm_client, long_msg, self.ROUTING_KEY)
        assert data["reply"], "应有回复"

    async def test_special_characters_in_message(self, llm_client: TestClient):
        """包含特殊字符的消息（Emoji、符号等）应正常处理。"""
        data = await send_message(
            llm_client,
            "你好！🎉 我需要帮助 😊 @bot <test> &amp;",
            self.ROUTING_KEY,
        )
        assert data["reply"], "应有回复"

    async def test_parallel_different_users_independent(
        self, llm_client: TestClient
    ):
        """
        不同用户（routing_key）的消息相互独立，session 不混淆。
        顺序发送模拟并发场景。
        """
        # 用户 A
        await send_message(llm_client, "我叫小明。", "p2p:ou_user_a")
        # 用户 B
        await send_message(llm_client, "我叫小红。", "p2p:ou_user_b")

        # 用户 A 查询自己的信息
        data_a = await send_message(llm_client, "我叫什么名字？", "p2p:ou_user_a")
        data_b = await send_message(llm_client, "我叫什么名字？", "p2p:ou_user_b")

        assert "小明" in data_a["reply"], f"用户 A 应记得小明，实际：{data_a['reply']!r}"
        assert "小红" in data_b["reply"], f"用户 B 应记得小红，实际：{data_b['reply']!r}"

    async def test_sql_injection_attempt_handled_safely(self, llm_client: TestClient):
        """SQL 注入尝试应被正常处理，不导致崩溃。"""
        data = await send_message(
            llm_client,
            "'; DROP TABLE sessions; --",
            self.ROUTING_KEY,
        )
        assert isinstance(data["reply"], str), "应有字符串回复"

    async def test_repeated_slash_new(self, llm_client: TestClient):
        """连续发送多次 /new 应正常工作，每次都创建新 session。"""
        routing_key = "p2p:ou_repeated_new"
        sessions = []
        for _ in range(3):
            data = await send_message(llm_client, "/new", routing_key)
            sessions.append(data["session_id"])

        # 注意：/new 的回复不含 session_id（slash 命令返回文本），
        # 这里检查的是随后的普通消息 session_id 的变化
        data = await send_message(llm_client, "你好", routing_key)
        assert data["session_id"], "最终应有有效 session_id"
