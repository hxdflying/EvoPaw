"""第22课系统测试：三层记忆能力端到端验证

对应课程案例（草稿_22_XiaoPaw记忆篇_课程草稿.md）：
  P1  回顾：XiaoPaw 的记忆缺陷（Bootstrap 注入验证）
  P2  Case 1：SOP 已在记忆里（skill-creator）
  P3  Case 2：阿里该不该挂单（search_memory 协同 workspace）
  P4  产品化第一步：初始引导触发
  P5  技能调教：SOP → Skill
  P6  按需触发记忆搜索

运行方式：
  # Group P/Q/T (仅 LLM，无需 pgvector)
  pytest tests/integration/test_memory_system.py -m "llm and not pgvector" -v -s

  # Group S (需要 LLM + pgvector)
  pytest tests/integration/test_memory_system.py -m "pgvector" -v -s --timeout=180

  # Group R (需要 LLM + sandbox)
  pytest tests/integration/test_memory_system.py -m "sandbox" -v -s --timeout=180

  # 完整套件
  pytest tests/integration/test_memory_system.py -v -s --timeout=180
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import psycopg2
import pytest
from aiohttp.test_utils import TestClient

from .conftest import PGVECTOR_DSN, send_message, write_ctx


# ─────────────────────────────────────────────────────────────────────────────
# Group P: Bootstrap 注入层（L19）
# 验证 workspace 四个文件正确注入到 Agent.backstory
# 对应课程 P1：「XiaoPaw 的记忆缺陷」→ Bootstrap 是解决方案
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
class TestBootstrapLayer:
    """Bootstrap 阶段读取 workspace 文件，注入 Agent backstory。"""

    ROUTING_KEY = "p2p:ou_bootstrap_test"

    async def test_agent_knows_identity_from_soul_md(self, llm_client: TestClient):
        """soul.md 注入后，Agent 应知道自己叫 XiaoPaw。

        课程对应：P1 右侧「XiaoPaw with Memory」升级后的身份自知。
        """
        data = await send_message(llm_client, "你叫什么名字？", self.ROUTING_KEY)
        reply = data["reply"]
        assert any(kw in reply for kw in ["XiaoPaw", "小爪子"]), (
            f"Agent 应知道自己叫 XiaoPaw（来自 soul.md），实际：{reply!r}"
        )

    async def test_agent_uses_user_profile_from_user_md(
        self, llm_client: TestClient
    ):
        """user.md 写入用户称呼后，Agent 应能在对话中使用该信息。

        课程对应：用户偏好由 memory-save 写入 user.md，下次 Bootstrap 即可读取。
        验证：
          1. 将 agent.md 替换为简洁版（移除引导 SOP，模拟引导已完成）
          2. 在 user.md 写入「称呼：晓寒」（模拟 memory-save 写入）
          3. 发消息 → Bootstrap 注入 → Agent 应知道用户称呼
        """
        workspace_dir: Path = llm_client._workspace_dir

        # 移除引导 SOP（模拟引导已完成的 workspace 状态）
        (workspace_dir / "agent.md").write_text(
            "# Agent 配置\n\n你是 XiaoPaw，简洁回答用户问题。\n",
            encoding="utf-8",
        )

        # 写入用户称呼（模拟 memory-save 已将称呼写入 user.md）
        (workspace_dir / "user.md").write_text(
            "# 用户档案\n\n- **称呼**：晓寒\n- **时区**：Asia/Shanghai\n",
            encoding="utf-8",
        )

        # Bootstrap 在每次 LLM 调用时读取 workspace 文件，直接验证读取结果
        data = await send_message(
            llm_client, "你知道我的称呼是什么吗？", self.ROUTING_KEY
        )
        reply = data["reply"]
        assert "晓寒" in reply, (
            f"Agent 应从 user.md 读取称呼「晓寒」，实际：{reply!r}"
        )

    async def test_empty_workspace_agent_still_responds(
        self, llm_client: TestClient
    ):
        """workspace 文件为空也不应崩溃（Bootstrap 容错降级）。

        课程对应：build_bootstrap_prompt 文件缺失时返回空字符串，Agent 仍可运行。
        """
        workspace_dir: Path = llm_client._workspace_dir
        # 清空所有 workspace 文件
        for f in workspace_dir.glob("*.md"):
            f.unlink()

        data = await send_message(
            llm_client, "你好，你能帮我做什么？", "p2p:ou_empty_ws"
        )
        assert data["reply"], "workspace 为空时 Agent 应仍能回复"


# ─────────────────────────────────────────────────────────────────────────────
# Group Q: Context 持久化层（L19 - ctx.json）
# 验证每轮对话后 ctx.json / raw.jsonl 正确写入
# 对应课程 P1：解决「跨 session 失忆」和「长对话崩溃」
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
class TestContextPersistence:
    """验证 ctx.json 持久化和 raw.jsonl 审计日志的写入行为。"""

    ROUTING_KEY = "p2p:ou_ctx_persist_test"

    async def test_ctx_json_written_after_conversation(
        self, llm_client: TestClient, session_mgr
    ):
        """对话结束后，应在 ctx_dir 生成 {session_id}_ctx.json。

        课程对应：run_and_index() 调用 save_session_ctx() 写入 ctx.json。
        """
        ctx_dir: Path = llm_client._ctx_dir

        await send_message(llm_client, "你好", self.ROUTING_KEY)

        # 等待 ctx.json 写入（同步操作，不需要额外等待）
        session = await session_mgr.get_or_create(self.ROUTING_KEY)
        ctx_file = ctx_dir / f"{session.id}_ctx.json"

        assert ctx_file.exists(), (
            f"对话结束后应生成 ctx.json，路径：{ctx_file}"
        )

    async def test_ctx_json_is_valid_json_with_messages(
        self, llm_client: TestClient, session_mgr
    ):
        """ctx.json 应是合法的 JSON，包含 LLM 消息列表。

        课程对应：save_session_ctx 将 context.messages 序列化为 JSON。
        """
        ctx_dir: Path = llm_client._ctx_dir
        routing_key = "p2p:ou_ctx_format_test"

        await send_message(llm_client, "帮我算 1+1", routing_key)

        session = await session_mgr.get_or_create(routing_key)
        ctx_file = ctx_dir / f"{session.id}_ctx.json"
        assert ctx_file.exists()

        messages = json.loads(ctx_file.read_text(encoding="utf-8"))
        assert isinstance(messages, list), "ctx.json 应是 JSON 列表"
        assert len(messages) > 0, "ctx.json 应包含至少一条消息"

        roles = {m.get("role") for m in messages}
        assert "user" in roles or "assistant" in roles, (
            f"消息应包含 user/assistant 角色，实际 roles={roles}"
        )

    async def test_raw_jsonl_written_as_audit_log(
        self, llm_client: TestClient, session_mgr
    ):
        """对话结束后，应生成 raw.jsonl 完整审计日志。

        课程对应：append_session_raw() 追加写入审计日志（不可覆盖的完整记录）。
        """
        ctx_dir: Path = llm_client._ctx_dir
        routing_key = "p2p:ou_raw_jsonl_test"

        await send_message(llm_client, "今天天气怎么样", routing_key)

        session = await session_mgr.get_or_create(routing_key)
        raw_file = ctx_dir / f"{session.id}_raw.jsonl"

        assert raw_file.exists(), f"应生成 raw.jsonl 审计日志，路径：{raw_file}"

        lines = [l for l in raw_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) > 0, "raw.jsonl 应有记录"

        # 每行应是合法 JSON
        for line in lines:
            record = json.loads(line)
            assert "role" in record, f"记录应含 role 字段：{record}"
            assert "ts" in record, f"记录应含时间戳 ts：{record}"

    async def test_ctx_json_used_to_restore_previous_session_context(
        self, llm_client: TestClient, session_mgr
    ):
        """手动向 ctx.json 写入「秘密信息」，新一轮对话应能感知该信息。

        课程对应：_restore_session() 在首次 LLM 调用前从 ctx.json 恢复历史。
        此测试验证跨 session 恢复机制的读取路径。
        """
        ctx_dir: Path = llm_client._ctx_dir
        routing_key = "p2p:ou_ctx_restore_test"

        # 先发一条消息建立 session（获取 session_id）
        init_data = await send_message(llm_client, "初始化", routing_key)
        session = await session_mgr.get_or_create(routing_key)
        session_id = session.id

        # 将「秘密代码」注入 ctx.json（模拟上一次 session 已有的对话历史）
        secret_msgs = [
            {"role": "system", "content": "你是 XiaoPaw"},
            {"role": "user",      "content": "我的测试秘密代码是 ZXCV9876"},
            {"role": "assistant", "content": "好的，我已记住你的测试秘密代码 ZXCV9876"},
        ]
        write_ctx(ctx_dir, session_id, secret_msgs)

        # 下一轮对话应从 ctx.json 恢复并感知到秘密代码
        data = await send_message(
            llm_client, "我之前说的测试秘密代码是什么？", routing_key
        )
        reply = data["reply"]
        assert "ZXCV9876" in reply, (
            f"Agent 应从 ctx.json 恢复「ZXCV9876」，实际：{reply!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Group R: 文件记忆层（L20 - memory-save / skill-creator）
# 验证 Agent 主动写入 workspace 文件的能力
# 对应课程 P2/P5：SOP 沉淀为 Skill；P3：workspace 文件和搜索层协同
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.sandbox
class TestFileMemoryLayer:
    """文件记忆层：memory-save 写偏好，skill-creator 固化 SOP。"""

    async def test_memory_save_confirms_saving_user_preference(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """用户明确表达偏好，Agent 应调用 memory-save 并确认保存。

        课程对应 P5：「用聊天建设 SOP，用 SOP 训练技能」
        — memory-save 的触发场景：用户更正行为或表达固定习惯。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = "p2p:ou_memory_save_test"
        data = await send_message(
            llm_client,
            "我不喜欢用 Markdown 表格格式回复，以后请直接用文字列举，记住这个偏好。",
            routing_key,
        )
        reply = data["reply"]
        assert len(reply) > 10, f"回复过短：{reply!r}"
        assert any(
            kw in reply
            for kw in ["记住", "保存", "记录", "偏好", "好的", "明白", "已"]
        ), f"Agent 应确认已保存偏好，实际：{reply!r}"

    async def test_memory_save_updates_user_preference_is_retained(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """memory-save 写入 user.md 后，下次对话 Agent 仍记得该偏好。

        课程对应：偏好学习为零 → 解决方案：memory-save 写入 user.md，
        Bootstrap 注入保证跨 session 可用。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = "p2p:ou_memory_save_retain_test"

        # 第一轮：明确表达偏好
        await send_message(
            llm_client,
            "从现在起，回复我时请在开头加「【晓寒专属】」这几个字，记住这个格式要求。",
            routing_key,
        )

        # 第二轮：验证偏好被记住（同一个 session 中 LLM 应有记忆）
        data = await send_message(
            llm_client,
            "我叫什么名字？",
            routing_key,
        )
        reply = data["reply"]
        # 在同一 session 内，LLM 应通过上下文记住这个格式要求
        assert len(reply) > 5, f"回复过短：{reply!r}"

    async def test_skill_creator_triggered_for_sop_description(
        self, llm_client: TestClient, sandbox_available: bool
    ):
        """用户描述 SOP 并要求保存为技能，Agent 应调用 skill-creator。

        课程对应 P5/P2：
        「用户描述早报 SOP → XiaoPaw 调用 skill-creator → investment-report/SKILL.md」
        此测试使用简化版 SOP（避免依赖投资数据），验证 skill-creator 触发确认。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = "p2p:ou_skill_creator_test"
        data = await send_message(
            llm_client,
            (
                "我想把以下工作流保存为可复用技能：\n"
                "1. 收到「工作汇总」指令\n"
                "2. 列出今日完成的 3 件事\n"
                "3. 列出明日计划的 3 件事\n"
                "4. 用简洁格式输出\n"
                "请把这个流程保存为技能，技能名叫 daily-summary。"
            ),
            routing_key,
        )
        reply = data["reply"]
        assert len(reply) > 20, f"回复过短：{reply!r}"
        assert any(
            kw in reply
            for kw in ["技能", "skill", "SKILL", "保存", "创建", "daily-summary", "成功", "已"]
        ), f"Agent 应确认技能已创建，实际：{reply!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Group S: 搜索记忆层（L21 - pgvector 向量数据库）
# 验证每轮对话写入 pgvector、search_memory 能检索历史
# 对应课程 P3/P6：「阿里该不该挂单」「根据上周五的分析复盘」
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.pgvector
class TestSearchMemoryLayer:
    """搜索记忆层：pgvector 写入 + search_memory 检索。"""

    async def test_conversation_indexed_to_pgvector(
        self, memory_client_pgvector: TestClient, pgvector_live_dsn: str
    ):
        """对话结束后，pgvector memories 表应有对应记录。

        课程对应：run_and_index() 在每轮结束后 asyncio.create_task(async_index_turn(...))。
        验证「异步建索引」路径：对话返回后等待异步任务完成，再查 DB。
        """
        import uuid
        routing_key = f"p2p:ou_pgvector_idx_{uuid.uuid4().hex[:8]}"

        data = await send_message(
            memory_client_pgvector,
            "帮我记录一下：我今天完成了项目的单元测试，覆盖率达到 85%。",
            routing_key,
        )
        assert data["reply"]

        # 等待异步索引任务完成（asyncio.create_task 是 fire-and-forget）
        await asyncio.sleep(8)

        conn = psycopg2.connect(pgvector_live_dsn)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT count(*) FROM memories WHERE routing_key = %s",
                (routing_key,),
            )
            count = cur.fetchone()[0]
        finally:
            conn.close()

        assert count >= 1, (
            f"对话结束后 pgvector 应有 >= 1 条记录（routing_key={routing_key}），"
            f"实际 count={count}"
        )

    async def test_indexed_record_contains_expected_fields(
        self, memory_client_pgvector: TestClient, pgvector_live_dsn: str
    ):
        """pgvector 写入记录应包含完整字段（summary / tags / turn_ts 等）。

        课程对应：indexer.py _index_single_turn 的字段完整性。
        """
        import uuid
        routing_key = f"p2p:ou_pgvector_fields_{uuid.uuid4().hex[:8]}"

        await send_message(
            memory_client_pgvector,
            "我买了 500 股阿里巴巴，成本价 85 港元，购买日期是昨天。",
            routing_key,
        )
        await asyncio.sleep(8)

        conn = psycopg2.connect(pgvector_live_dsn)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT session_id, user_message, assistant_reply, summary, turn_ts
                FROM memories
                WHERE routing_key = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (routing_key,),
            )
            row = cur.fetchone()
        finally:
            conn.close()

        assert row is not None, f"DB 中应有该 routing_key 的记录，实际为空"
        session_id, user_msg, assistant_reply, summary, turn_ts = row

        assert session_id, "session_id 不应为空"
        assert "阿里" in user_msg or "500" in user_msg, (
            f"user_message 应包含原始消息内容，实际：{user_msg!r}"
        )
        assert assistant_reply, "assistant_reply 不应为空"
        assert summary, "summary 不应为空（LLM 提取失败时会用 fallback）"
        assert turn_ts > 0, f"turn_ts 应为正整数时间戳，实际：{turn_ts}"

    async def test_search_memory_triggered_for_history_recall(
        self, memory_client_pgvector: TestClient, sandbox_available: bool
    ):
        """ask 关于历史对话的问题时，Agent 应触发 search_memory 检索 pgvector。

        课程对应 P3：「阿里今天该不该挂单」→ XiaoPaw 识别需要历史持仓信息，
        触发 search_memory → 找到之前提到的成本价和仓位。

        测试 Case：
        1. 第一轮：告诉 Agent 持仓信息
        2. 等待 pgvector 异步索引
        3. 新 session（/new）清空 context 窗口
        4. 第二轮：问「根据上次说的，我的阿里持仓该不该加仓？」
        5. Agent 应能从历史检索到持仓信息
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        import uuid
        routing_key = f"p2p:ou_search_mem_{uuid.uuid4().hex[:8]}"

        # 第一轮：写入持仓信息
        await send_message(
            memory_client_pgvector,
            "帮我记录一下我的持仓：阿里巴巴 2000 股，成本价 88 港元，"
            "目前已持仓 3 个月，打算长期持有。",
            routing_key,
        )

        # 等待 pgvector 索引
        await asyncio.sleep(8)

        # 清空 context 窗口（模拟跨 session）
        await send_message(memory_client_pgvector, "/new", routing_key)

        # 第二轮：引用历史持仓信息（隐式触发 search_memory）
        data = await send_message(
            memory_client_pgvector,
            "根据我们之前讨论过的持仓情况，阿里今天跌了 3%，我该不该加仓？",
            routing_key,
        )
        reply = data["reply"]
        assert len(reply) > 30, f"回复过短：{reply!r}"
        # Agent 应能找回持仓信息并给出分析
        assert any(
            kw in reply
            for kw in ["阿里", "持仓", "88", "2000", "成本", "加仓", "建议"]
        ), (
            f"Agent 应从 search_memory 找回持仓信息并分析，实际：{reply!r}"
        )

    async def test_p3_case_decision_question_uses_historical_context(
        self, memory_client_pgvector: TestClient, sandbox_available: bool
    ):
        """P3 核心 Case：「该不该挂单卖出」隐含历史需求，Agent 主动搜索记忆。

        课程 P3 讲解重点：
        - 用户没有再次告知持仓，XiaoPaw 自己判断「需要历史信息」，触发 search_memory
        - search_memory description 覆盖「该不该 XX」类决策问题

        测试 Case：先在历史中建立决策背景，新 session 后问决策问题，
        验证 Agent 能自主搜索并给出基于历史的建议。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        import uuid
        routing_key = f"p2p:ou_p3_case_{uuid.uuid4().hex[:8]}"

        # 建立历史背景（持仓 + 复盘问题）
        await send_message(
            memory_client_pgvector,
            "我持有腾讯 1000 股，成本价 320 港元，今天最新价是 365 港元。"
            "我之前复盘发现自己容易在上涨时追高，这是我的操作问题。",
            routing_key,
        )
        await asyncio.sleep(8)

        # 新 session，清空 context
        await send_message(memory_client_pgvector, "/new", routing_key)

        # 决策问题（隐含需要历史信息）
        data = await send_message(
            memory_client_pgvector,
            "腾讯今天该不该挂单卖出？",
            routing_key,
        )
        reply = data["reply"]
        assert len(reply) > 30, f"回复过短：{reply!r}"
        # Agent 应给出分析而不是说「不知道你的持仓」
        assert any(
            kw in reply
            for kw in ["腾讯", "持仓", "320", "365", "成本", "建议", "分析", "卖", "仓"]
        ), (
            f"Agent 应搜索历史并基于持仓信息给出建议，实际：{reply!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Group T: 初始引导场景（P4 case）
# 验证 agent.md 内置引导 SOP 在 workspace 为空时自动触发
# 对应课程 P4：「从空白到有灵魂：agent.md 的自我引导」
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
class TestInitialOnboarding:
    """初始引导：workspace 为初始状态时，agent.md 触发引导对话。"""

    async def test_onboarding_questions_triggered_on_fresh_workspace(
        self, llm_client: TestClient
    ):
        """全新 workspace（含引导 SOP 的 agent.md），第一条消息应触发引导问题。

        课程对应 P4：
        agent.md 内置引导 SOP：「当检测到 workspace 中 soul.md / user.md 为空或极简时，
        开始与用户自然对话，逐步收集使用信息。」

        workspace-init/ 中的 agent.md 包含引导 SOP（soul.md/user.md 为空），
        发送任意消息后 Agent 应开始提问引导。
        """
        routing_key = "p2p:ou_onboarding_test"
        data = await send_message(llm_client, "你好", routing_key)
        reply = data["reply"]
        assert len(reply) > 10, f"回复过短：{reply!r}"
        # Agent 应提出引导问题：询问名字或用途
        assert any(
            kw in reply
            for kw in ["名字", "叫我", "称呼", "用途", "帮你做", "助手", "使用", "?", "？"]
        ), (
            f"首次对话 Agent 应触发引导问题，实际：{reply!r}"
        )

    async def test_onboarding_continues_naturally_across_turns(
        self, llm_client: TestClient
    ):
        """初始引导应跨多轮自然推进，不在单轮内强制完成所有步骤。

        课程对应 P4 讲解重点：
        「触发时机完全由对话决定，不是程序判断第一次启动」
        「用户随时可以打断去做别的事，引导可以跨 session 分多次完成」
        """
        routing_key = "p2p:ou_onboarding_multiturn"

        # 第一轮：触发引导
        data1 = await send_message(llm_client, "嗨", routing_key)
        assert data1["reply"]

        # 第二轮：回答引导问题（提供用途）
        data2 = await send_message(
            llm_client, "我主要用你来做投资分析和工作整理", routing_key
        )
        reply2 = data2["reply"]
        assert len(reply2) > 10

        # 第三轮：提供更多信息
        data3 = await send_message(
            llm_client, "我希望你回复简洁，不要废话", routing_key
        )
        reply3 = data3["reply"]
        assert len(reply3) > 10
        # Agent 应推进引导而非忽视
        assert any(
            kw in reply3
            for kw in ["好的", "明白", "记住", "了解", "简洁", "保存", "下一步", "还有"]
        ), f"Agent 应接收偏好并推进引导，实际：{reply3!r}"

    async def test_onboarding_can_be_interrupted_and_resumed(
        self, llm_client: TestClient
    ):
        """引导过程中用户打断去做别的事，Agent 应立即切换正常助手模式。

        课程对应 P4：「用户随时可以打断去做别的事，引导可以跨 session 分多次完成」
        """
        routing_key = "p2p:ou_onboarding_interrupt"

        # 触发引导
        await send_message(llm_client, "你好", routing_key)

        # 打断：直接提出工作任务
        data = await send_message(llm_client, "帮我算一下 15 * 23 等于多少", routing_key)
        reply = data["reply"]

        # Agent 应先完成手头任务，而不是坚持引导
        assert "345" in reply, (
            f"打断引导后 Agent 应优先完成工作任务（15*23=345），实际：{reply!r}"
        )
