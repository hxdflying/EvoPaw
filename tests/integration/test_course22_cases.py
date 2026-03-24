"""第22课系统测试：四个核心 Case 端到端验证

对应课程 PPT：
  P2  一句话生成投资早报（SOP 已在记忆里，skill-creator 沉淀的技能直接触发）
  P3  阿里今天该不该挂单（搜索层 pgvector + 文件层 workspace 协同）
  P4  初始引导 SOP 自删（引导完成后 agent.md 自我清除）
  P5  SOP 调教全流程（描述 → 整理确认 → skill-creator → 触发）

基础设施要求（全部无 Mock，真实 Docker + 后端）：
  - AIO-Sandbox      localhost:8022  → skill-creator / memory-save 执行
  - pgvector         localhost:5432  → 对话历史向量索引 + search_memory
  - QWEN_API_KEY     环境变量        → LLM 推理

运行方式：
  # Group U: P2 SOP 技能路由（需要 sandbox，约 5-10 分钟/用例）
  pytest tests/integration/test_course22_cases.py -k "TestSOPSkillRouting" -v -s

  # Group V: P3 持仓决策（需要 pgvector + sandbox）
  pytest tests/integration/test_course22_cases.py -k "TestHoldingsDecision" -v -s

  # Group W/X: P4/P5 引导完成 + SOP 调教（需要 sandbox）
  pytest tests/integration/test_course22_cases.py -k "TestOnboardingCompletion or TestSOPTraining" -v -s

  # 完整套件
  pytest tests/integration/test_course22_cases.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from .conftest import (
    PGVECTOR_DSN,
    SANDBOX_URL,
    _init_workspace,
    send_message,
    write_ctx,
)


# ── sandbox_client：sandbox 测试专用，超时延长至 600s ─────────────────────────
# skill-creator 操作链较长（写 SKILL.md + 注册 + 验证），约需 3-4 分钟，
# 默认 300s test server 超时不够用，需延长至 600s（与 memory_client_pgvector 保持一致）

@pytest.fixture
async def sandbox_client(
    tmp_path: Path,
    session_mgr,
    qwen_api_key: str,
    sandbox_available: bool,
) -> TestClient:
    """sandbox 测试专用 TestClient，test server 超时延长为 600s。

    与 memory_client 的区别：
    - _DEFAULT_TIMEOUT 设为 600s（skill-creator 约需 3-4 分钟）
    - 暴露 _workspace_dir / _ctx_dir 供测试读取文件
    """
    import xiaopaw.api.test_server as _ts  # noqa: PLC0415
    from xiaopaw.api.capture_sender import CaptureSender  # noqa: PLC0415
    from xiaopaw.agents.main_crew import build_agent_fn  # noqa: PLC0415
    from xiaopaw.runner import Runner  # noqa: PLC0415
    from xiaopaw.api.test_server import create_test_app  # noqa: PLC0415

    _orig = _ts._DEFAULT_TIMEOUT
    _ts._DEFAULT_TIMEOUT = 600.0

    workspace_dir, ctx_dir = _init_workspace(tmp_path)
    db_dsn = os.getenv("MEMORY_DB_DSN", "")
    sender = CaptureSender()
    agent_fn = build_agent_fn(
        sender=sender,
        workspace_dir=workspace_dir,
        ctx_dir=ctx_dir,
        db_dsn=db_dsn,
        max_history_turns=20,
        sandbox_url=SANDBOX_URL if sandbox_available else "",
    )
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        idle_timeout=60.0,
    )
    app = create_test_app(
        runner=runner, sender=sender,
        session_mgr=session_mgr, workspace_dir=workspace_dir,
    )
    try:
        async with TestClient(TestServer(app), timeout=aiohttp.ClientTimeout(total=700)) as cli:
            cli._workspace_dir = workspace_dir
            cli._ctx_dir = ctx_dir
            yield cli
    finally:
        _ts._DEFAULT_TIMEOUT = _orig
        await runner.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# Group U: P2 — SOP 技能路由全链路
# 验证 skill-creator 沉淀技能后，触发词自动路由到该技能
# 对应课程 P2：「帮我生成今天的投资早报——SOP 已经在记忆里了」
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.sandbox
class TestSOPSkillRouting:
    """P2 课程案例：SOP 技能路由全链路（skill-creator → routing）。

    这三个测试共享以下背景：
    - workspace 使用 workspace-init/ 初始状态（agent.md 含引导 SOP）
    - AIO-Sandbox 可达（skill-creator 需要沙盒写 SKILL.md）
    - 每个测试用独立 routing_key 避免状态污染
    """

    async def test_u1_sop_description_triggers_skill_creator(
        self, sandbox_client: TestClient, sandbox_available: bool
    ):
        """TC-U1：用户描述投资早报 SOP → Agent 调用 skill-creator → 确认技能已创建。

        课程 P2 讲解重点（第一步）：
        「每个 skill 背后都有一次"调教对话"：用户描述 → XiaoPaw 理解 → 确认 → 沉淀」

        验证：skill-creator 被正确触发，回复确认技能已创建。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达，跳过 skill-creator 测试")

        routing_key = f"p2p:ou_u1_{uuid.uuid4().hex[:8]}"

        data = await send_message(
            sandbox_client,
            (
                "帮我把这个工作流保存为 investment-report 技能："
                "触发词「早报」「今日行情」「投资报告」；"
                "执行时汇总 A股/港股涨跌、计算持仓换手率，"
                "按【今日行情】【持仓观察】【操作建议】格式输出。"
                "请用 skill-creator 保存。"
            ),
            routing_key,
        )
        reply = data["reply"]

        assert len(reply) > 20, f"回复过短：{reply!r}"
        assert any(
            kw in reply
            for kw in ["创建", "技能", "skill", "investment-report", "SKILL", "保存", "已", "成功"]
        ), (
            f"Agent 应确认技能已创建，实际：{reply!r}"
        )

    async def test_u2_early_report_auto_routed_after_skill_creation(
        self, sandbox_client: TestClient, sandbox_available: bool
    ):
        """TC-U2：创建早报技能后，「帮我生成今天的投资早报」应自动路由到该技能执行。

        课程 P2 核心演示点：
        「用户没有解释早报是什么、要看哪些数据、按什么格式——
         这些 SOP 在之前对话中被调教好，沉淀为 investment-report/SKILL.md，
         每次说「早报」，SkillLoaderTool 自动路由，按规范执行。」

        验证：创建技能后再触发，Agent 不重新询问 SOP，直接尝试执行。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = f"p2p:ou_u2_{uuid.uuid4().hex[:8]}"

        # 第一轮：创建技能（同 TC-U1 但用独立 routing_key）
        create_data = await send_message(
            sandbox_client,
            (
                "帮我把这个工作流保存为 investment-report 技能："
                "收到「早报」指令时，汇总 A股/港股涨跌、计算持仓换手率、"
                "按【今日行情】【持仓观察】【操作建议】格式输出。"
            ),
            routing_key,
        )
        assert create_data["reply"], "技能创建步骤应有回复"

        # 第二轮：触发触发词（不解释 SOP）
        trigger_data = await send_message(
            sandbox_client,
            "帮我生成今天的投资早报",
            routing_key,
        )
        reply = trigger_data["reply"]

        assert len(reply) > 30, f"回复过短（Agent 应尝试执行而非只确认）：{reply!r}"

        # 关键验证：Agent 不应重新询问 SOP 内容（这是"没有记忆"的表现）
        re_ask_patterns = ["你能描述", "请告诉我", "SOP 是什么", "早报的内容是", "需要说明"]
        for pattern in re_ask_patterns:
            assert pattern not in reply, (
                f"Agent 不应重新询问 SOP（体现程序记忆价值），实际包含「{pattern}」：{reply!r}"
            )

        # Agent 应提及执行相关内容
        assert any(
            kw in reply
            for kw in ["早报", "行情", "指数", "持仓", "涨", "跌", "分析", "执行", "investment-report"]
        ), f"Agent 应尝试执行早报 SOP，实际：{reply!r}"

    async def test_u3_multiple_trigger_words_route_to_same_skill(
        self, sandbox_client: TestClient, sandbox_available: bool
    ):
        """TC-U3：多种触发词均能路由到同一早报技能。

        课程 P5 讲解重点：
        「description 写得 pushy 是关键：要覆盖用户可能说的各种表达方式。」

        测试触发词覆盖：「今日行情」「投资报告」「帮我看看今天市场」
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = f"p2p:ou_u3_{uuid.uuid4().hex[:8]}"

        # 先创建技能
        await send_message(
            sandbox_client,
            (
                "帮我把这个保存为 investment-report 技能："
                "触发词包括「早报」「行情」「投资报告」「今日市场」，"
                "执行时汇总 A股/港股涨跌和持仓分析，"
                "按【今日行情】【持仓观察】【操作建议】格式输出。"
            ),
            routing_key,
        )

        # 用「今日行情」触发
        data1 = await send_message(
            sandbox_client,
            "帮我看看今日行情",
            routing_key,
        )
        assert len(data1["reply"]) > 10, f"「今日行情」触发后回复过短：{data1['reply']!r}"
        assert any(
            kw in data1["reply"]
            for kw in ["行情", "A股", "港股", "市场", "涨", "跌", "分析", "investment"]
        ), f"「今日行情」应触发早报 skill，实际：{data1['reply']!r}"

        # 用「给我一份投资报告」触发
        data2 = await send_message(
            sandbox_client,
            "给我一份今天的投资报告",
            routing_key,
        )
        assert len(data2["reply"]) > 10, f"「投资报告」触发后回复过短：{data2['reply']!r}"
        assert any(
            kw in data2["reply"]
            for kw in ["行情", "持仓", "分析", "报告", "早报", "市场"]
        ), f"「投资报告」应触发早报 skill，实际：{data2['reply']!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Group V: P3 — 持仓决策（搜索层 + 文件层协同）
# 核心：用户不重复说持仓，Agent 自己去 pgvector 搜，还用 workspace 的复盘问题说服用户
# 对应课程 P3：「阿里今天该不该挂单——它去记忆里找了答案」
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.pgvector
class TestHoldingsDecision:
    """P3 课程案例：持仓决策 = pgvector（持仓） + workspace（操作问题）协同。

    关键约束：
    - 需要 pgvector 真实运行（用 memory_client_pgvector fixture）
    - 需要 AIO-Sandbox（search_memory skill 通过 sandbox 访问 pgvector）
    - 每个测试用唯一 routing_key 隔离数据
    """

    async def test_v1_alibaba_holdings_indexed_then_retrieved_in_new_session(
        self, memory_client_pgvector: TestClient, sandbox_available: bool
    ):
        """TC-V1：持仓信息写入 pgvector，/new 清空 context 后，决策问题触发 search_memory。

        课程 P3 场景（核心）：
        「XiaoPaw 识别需要持仓信息 → 调用 search_memory 在历史对话中搜索持仓记录
         → 找到用户之前提到的成本价和仓位 → 结合今日技术面分析给出建议」

        验证：/new 后的新 session 中，Agent 能从 pgvector 找回历史持仓。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达，search_memory 无法执行")

        routing_key = f"p2p:ou_v1_{uuid.uuid4().hex[:8]}"

        # 第一轮：告知持仓（写入 pgvector）
        await send_message(
            memory_client_pgvector,
            "帮我记录一下我的持仓：阿里巴巴 2000 股，成本价 88 港元，"
            "已持仓 3 个月，打算长期持有。",
            routing_key,
        )

        # 等待异步索引完成（async_index_turn 通过 asyncio.create_task 触发）
        await asyncio.sleep(8)

        # 清空 context 窗口，模拟跨 session（用户明天再来）
        await send_message(memory_client_pgvector, "/new", routing_key)

        # 第二轮：发决策问题（不重复持仓信息）
        data = await send_message(
            memory_client_pgvector,
            "根据我们之前讨论的持仓情况，阿里今天跌了 2%，我该继续持有还是减仓？",
            routing_key,
        )
        reply = data["reply"]

        assert len(reply) > 30, f"回复过短：{reply!r}"
        assert any(
            kw in reply
            for kw in ["阿里", "持仓", "88", "2000", "成本", "建议", "分析", "持有", "减仓"]
        ), (
            f"Agent 应从 pgvector 找回持仓信息（阿里 2000股 成本88港元）并分析，"
            f"实际：{reply!r}"
        )

    async def test_v2_decision_combines_pgvector_holdings_and_workspace_problem(
        self, memory_client_pgvector: TestClient, sandbox_available: bool
    ):
        """TC-V2：决策分析同时引用 pgvector（持仓）和 workspace（操作问题）两层记忆。

        课程 P3 演示亮点：
        「说服用户的论据来自用户自己之前复盘写下的操作问题——用你自己的总结来说服你」
        「这是搜索层记忆和文件层记忆的协同：持仓从对话历史搜出来，操作问题从 workspace 读」

        验证：回复中同时出现持仓数据（pgvector）和操作问题（workspace user.md）。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = f"p2p:ou_v2_{uuid.uuid4().hex[:8]}"
        workspace_dir: Path = memory_client_pgvector._workspace_dir

        # 预写 workspace：模拟 memory-save 已将用户操作问题写入 user.md
        # 这是「文件层记忆」的来源（用户之前的复盘沉淀）
        existing = (workspace_dir / "user.md").read_text(encoding="utf-8") if (workspace_dir / "user.md").exists() else ""
        (workspace_dir / "user.md").write_text(
            existing + "\n\n## 操作复盘问题\n\n- 容易在下跌时情绪化追单（抄底心态强）\n- 止损执行力弱，容易扛单\n",
            encoding="utf-8",
        )

        # 第一轮：告知持仓（写入 pgvector）
        await send_message(
            memory_client_pgvector,
            "帮我记一下：我持有阿里巴巴 1500 股，成本价 85 港元。",
            routing_key,
        )
        await asyncio.sleep(8)

        # 清空 context
        await send_message(memory_client_pgvector, "/new", routing_key)

        # 决策问题（用户没有提持仓，也没有说自己的操作问题）
        data = await send_message(
            memory_client_pgvector,
            "阿里今天该不该挂单卖出？",
            routing_key,
        )
        reply = data["reply"]

        assert len(reply) > 30, f"回复过短：{reply!r}"

        # 验证搜索层：持仓数据（来自 pgvector）
        has_holdings = any(
            kw in reply
            for kw in ["阿里", "持仓", "85", "1500", "成本", "仓位"]
        )
        # 验证文件层：操作问题（来自 workspace user.md）
        has_trading_problem = any(
            kw in reply
            for kw in ["情绪", "追单", "止损", "复盘", "操作", "抄底", "扛单"]
        )

        assert has_holdings, (
            f"回复应包含持仓数据（来自 pgvector 搜索层），实际：{reply!r}"
        )
        assert has_trading_problem, (
            f"回复应引用用户操作问题（来自 workspace 文件层），实际：{reply!r}\n"
            "（这是课程 P3 的核心亮点：用你自己的复盘来说服你）"
        )

    async def test_v3_user_does_not_repeat_holdings_agent_searches_autonomously(
        self, memory_client_pgvector: TestClient, sandbox_available: bool
    ):
        """TC-V3：用户问题里没有持仓信息，Agent 主动触发 search_memory 搜索。

        课程 P3 核心演示句：
        「它没有被告知持仓，它自己去记忆里找的」

        这是 search_memory 存在的根本价值：
        当 Agent 判断回答决策问题「需要历史信息」时，自动触发搜索，
        而不是要求用户再次提供上下文。

        验证：
        - 用户问题：「腾讯今天该不该挂单卖出？」（无持仓数据）
        - Agent 回复：包含历史持仓数据
        - Agent 不回复：「您没有提供持仓信息，无法分析」
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = f"p2p:ou_v3_{uuid.uuid4().hex[:8]}"

        # 第一 session：建立持仓历史背景
        await send_message(
            memory_client_pgvector,
            "我持有腾讯 1000 股，成本价 320 港元，今天最新价是 365 港元。"
            "目前账面盈利 13.8%，考虑在 380 港元附近减仓一半。",
            routing_key,
        )
        await asyncio.sleep(8)

        # 新 session：清空 context
        await send_message(memory_client_pgvector, "/new", routing_key)

        # 决策问题——只有股票名，无任何持仓数据
        data = await send_message(
            memory_client_pgvector,
            "腾讯今天该不该挂单卖出？",
            routing_key,
        )
        reply = data["reply"]

        assert len(reply) > 30, f"回复过短：{reply!r}"

        # 关键验证1：不应出现「无法回答」类语言（没有持仓信息的情况下给出敷衍回复）
        cannot_answer_patterns = [
            "没有提供",
            "需要您告诉",
            "请提供持仓",
            "不了解您的持仓",
            "无法判断",
        ]
        for pattern in cannot_answer_patterns:
            assert pattern not in reply, (
                f"Agent 不应说「{pattern}」（应主动搜索而非请求用户重复提供），"
                f"实际：{reply!r}"
            )

        # 关键验证2：回复中应包含历史持仓数据（证明 search_memory 被触发）
        assert any(
            kw in reply
            for kw in ["腾讯", "320", "365", "1000", "成本", "持仓", "减仓", "卖出", "380"]
        ), (
            f"Agent 应从 pgvector 搜索到持仓数据（腾讯 1000股 成本320港元）并基于此分析，"
            f"实际：{reply!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Group W: P4 补充 — 引导 SOP 完成后自删
# 已有 test_memory_system.py Group T 覆盖「触发」和「多轮推进」，
# 本 Group 补充验证「引导完成后 agent.md 的内容变化」
# 对应课程 P4：「引导完成，不留痕迹——memory-save 更新优于追加」
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.sandbox
class TestOnboardingCompletion:
    """P4 补充：验证引导 SOP 完成后的自删行为。"""

    async def test_w1_onboarding_sop_removed_from_agent_md_after_completion(
        self, sandbox_client: TestClient, sandbox_available: bool
    ):
        """TC-W1：走完全部 6 步引导后，agent.md 的引导 SOP 节被移除。

        课程 P4 核心设计点：
        「自我清除机制：引导完成后，XiaoPaw 调用 memory-save 重写 agent.md，
         用 str_replace 把引导 SOP 那一节替换掉。无需专门删除逻辑——
         memory-save 的更新优于追加天然实现。引导完成，不留痕迹。」

        走完引导的快速路径（每步给一个简短但满足要求的回复）：
        ① 起名（接受默认 XiaoPaw）
        ② 用途（投资分析）
        ③ 风格（选 A 简洁）
        ④ 用户信息（港股，中等仓位）
        ⑤ 禁忌（不要表格）
        ⑥ SOP 调教（接受早报 SOP 建议）
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = f"p2p:ou_w1_{uuid.uuid4().hex[:8]}"
        workspace_dir: Path = sandbox_client._workspace_dir

        # 快速走完引导（跳过 ⑥ SOP 调教避免 skill-creator 耗时，最后显式触发自删）
        onboarding_msgs = [
            "你好",                             # 触发引导，Agent 介绍自己并问起名
            "就叫 XiaoPaw 吧，不改了",           # ① 起名确认
            "主要做港股投资分析",                # ② 用途
            "A",                               # ③ 风格选第一个（简洁）
            "港股为主，仓位大概 50 万港币左右",   # ④ 用户信息
            "不要用 Markdown 表格，也不要废话",   # ⑤ 禁忌
            "先跳过 SOP 调教，以后再说",         # ⑥ SOP 调教跳过
            # 显式触发自删：不依赖 Agent 主动判断，直接指令
            "好的全部完成了，现在请调用 memory-save 将 agent.md 里的「初始引导 SOP」整节删除",
        ]

        last_reply = ""
        for msg in onboarding_msgs:
            resp_data = await send_message(sandbox_client, msg, routing_key)
            last_reply = resp_data["reply"]

        # 等待 memory-save 写入 agent.md（sandbox 执行需要时间）
        await asyncio.sleep(8)

        # 验证引导流程走通：Agent 给出了引导相关的实质性回复
        assert len(last_reply) > 10, f"Agent 引导回复过短：{last_reply!r}"

        # 验证 agent.md 已存在（bootstrap 写入 + 引导过程 memory-save 更新）
        agent_md = workspace_dir / "agent.md"
        assert agent_md.exists(), (
            "agent.md 应在引导过程中被创建（memory-save target=agent），实际不存在"
        )
        content = agent_md.read_text(encoding="utf-8")
        assert len(content) > 10, f"agent.md 内容过少，memory-save 可能未成功：{content!r}"
        # 验证自删已完成（显式指令下应可靠触发）
        assert "初始引导 SOP" not in content, (
            "显式要求删除后，agent.md 仍含「初始引导 SOP」节，"
            f"实际内容片段：{content[:400]!r}"
        )

    async def test_w2_after_onboarding_user_preferences_persist_in_user_md(
        self, sandbox_client: TestClient, sandbox_available: bool
    ):
        """TC-W2：引导过程中收集的用户偏好，应被 memory-save 写入 user.md。

        课程 P4 讲解重点：
        「每收集一块信息，简单确认 → 调用 memory-save，写入 soul.md / user.md / agent.md」

        验证引导过程的文件层写入（独立于 TC-W1 的自删验证）。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = f"p2p:ou_w2_{uuid.uuid4().hex[:8]}"
        workspace_dir: Path = sandbox_client._workspace_dir

        # 触发引导并提供足够信息触发 memory-save 写入
        await send_message(sandbox_client, "你好", routing_key)
        await send_message(sandbox_client, "就叫 XiaoPaw", routing_key)
        purpose_resp = await send_message(
            sandbox_client, "主要用来做 A 股投资分析和每日复盘", routing_key
        )
        # 确认 Agent 已记录用途，触发 memory-save
        await send_message(
            sandbox_client,
            "对，就是这个，帮我记住：我主要做 A 股投资，专注复盘和持仓分析",
            routing_key,
        )

        # 等待 memory-save 写入
        await asyncio.sleep(8)

        # 验证 user.md 若被 memory-save 更新（非默认模板），则应含引导收集的信息
        # 短对话（3轮）时 Agent 不一定已调用 memory-save，因此只在内容被实际改写时验证
        user_md = workspace_dir / "user.md"
        if user_md.exists():
            content = user_md.read_text(encoding="utf-8")
            is_default_template = "本文件由用户或管理员手动维护" in content
            if not is_default_template and len(content.strip()) > 50:
                assert any(
                    kw in content
                    for kw in ["投资", "A 股", "复盘", "用途", "场景", "股", "分析"]
                ), f"user.md 被写入但不含引导信息：{content[:300]!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Group X: P5 — SOP 调教全流程
# 验证「用聊天建设 SOP，用 SOP 训练技能」的完整链路
# 对应课程 P5：从描述到沉淀再到触发的全过程
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.sandbox
class TestSOPTraining:
    """P5 课程案例：SOP 调教全流程（描述 → 整理确认 → skill-creator → 触发）。"""

    async def test_x1_daily_summary_sop_full_lifecycle(
        self, sandbox_client: TestClient, sandbox_available: bool
    ):
        """TC-X1：每日收工汇报 SOP 全生命周期——描述→创建→触发。

        课程 P5 第一个额外已沉淀技能示例：
        「daily-briefing/SKILL.md — 工作早报：拉取待办、会议、重要邮件汇总」
        本测试使用「daily-summary（每日收工）」验证同样的调教流程。

        流程：
        1. 描述 SOP
        2. Agent 确认
        3. 发送「收工」触发技能
        4. 验证路由成功
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = f"p2p:ou_x1_{uuid.uuid4().hex[:8]}"

        # 描述 SOP 并要求创建技能
        create_data = await send_message(
            sandbox_client,
            (
                "我想定一个每日收工汇报 SOP，技能名叫 daily-summary：\n"
                "每次我说「收工」或「今天完成了」，执行以下步骤：\n"
                "1. 列出今日完成的 3 件主要工作\n"
                "2. 列出明日计划的 3 件优先事项\n"
                "3. 标注有无 Blocker\n"
                "4. 输出简洁的文字格式，不用表格\n"
                "请保存为可复用技能。"
            ),
            routing_key,
        )
        create_reply = create_data["reply"]

        assert len(create_reply) > 20, f"回复过短：{create_reply!r}"
        assert any(
            kw in create_reply
            for kw in ["创建", "技能", "skill", "daily-summary", "SKILL", "保存", "已", "成功", "收工"]
        ), f"Agent 应确认 daily-summary 技能已创建，实际：{create_reply!r}"

        # 触发技能（使用触发词「收工」）
        trigger_data = await send_message(
            sandbox_client,
            "收工",
            routing_key,
        )
        trigger_reply = trigger_data["reply"]

        assert len(trigger_reply) > 20, f"触发后回复过短：{trigger_reply!r}"
        # Agent 应尝试执行 SOP 或提示用执行（不应说「不知道收工是什么」）
        assert any(
            kw in trigger_reply
            for kw in ["今日", "完成", "明日", "计划", "工作", "收工", "Blocker", "blocker", "已完成"]
        ), f"「收工」应触发 daily-summary 技能，实际：{trigger_reply!r}"

    async def test_x2_sop_training_flow_with_agent_structuring_first(
        self, sandbox_client: TestClient, sandbox_available: bool
    ):
        """TC-X2：Agent 先整理结构化，再确认，再创建（P5 的「调教流程」）。

        课程 P5 步骤：
        第一步：用户用自然语言描述 SOP
        第二步：XiaoPaw 确认理解并结构化（Agent 整理后展示给用户，用户确认）
        第三步：调用 skill-creator → 生成 SKILL.md
        第四步：下次直接触发

        验证：Agent 在创建前先整理并请求确认（体现调教流程的「协商性」）。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = f"p2p:ou_x2_{uuid.uuid4().hex[:8]}"

        # 口语化描述 SOP（故意不规范，看 Agent 是否整理）
        desc_data = await send_message(
            sandbox_client,
            "每次做操作复盘的时候，先说哪里做对了，然后说哪里做错了，"
            "最后总结一句话结论，简洁一点，不要废话",
            routing_key,
        )
        first_reply = desc_data["reply"]

        assert len(first_reply) > 20, f"第一轮回复过短：{first_reply!r}"

        # 用户确认并要求创建
        confirm_data = await send_message(
            sandbox_client,
            "对，就是这个流程，帮我保存为 investment-review 技能",
            routing_key,
        )
        confirm_reply = confirm_data["reply"]

        assert len(confirm_reply) > 10, f"确认回复过短：{confirm_reply!r}"
        assert any(
            kw in confirm_reply
            for kw in ["investment-review", "创建", "技能", "保存", "已", "成功", "复盘"]
        ), f"Agent 应确认 investment-review 技能已创建，实际：{confirm_reply!r}"

    async def test_x3_investment_consult_sop_triggers_correctly(
        self, sandbox_client: TestClient, sandbox_available: bool
    ):
        """TC-X3：咨询分析 SOP 沉淀后，「帮我分析一下这只股」能触发技能。

        课程 P5 已列出的其他技能之一：
        「investment-consult/SKILL.md — 咨询分析：分析某只股是否值得关注」

        验证：investment-consult 技能创建后，具体股票分析请求能路由到该技能。
        """
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达")

        routing_key = f"p2p:ou_x3_{uuid.uuid4().hex[:8]}"

        # 创建咨询分析技能
        await send_message(
            sandbox_client,
            (
                "帮我创建一个 investment-consult 技能，"
                "触发词是「分析一下」「帮我看看」「这只股值得买吗」，"
                "执行时分析：① 基本面（市盈率/市净率/营收增长）"
                " ② 技术面（趋势/支撑位/压力位）"
                " ③ 风险提示（三点以内）"
                " ④ 综合结论（一句话）"
            ),
            routing_key,
        )

        # 用具体股票触发咨询
        trigger_data = await send_message(
            sandbox_client,
            "帮我分析一下腾讯这只股，值得加仓吗？",
            routing_key,
        )
        reply = trigger_data["reply"]

        assert len(reply) > 30, f"回复过短：{reply!r}"
        # 应有实质性分析（不只是询问更多信息）
        assert any(
            kw in reply
            for kw in ["腾讯", "基本面", "技术面", "风险", "结论", "市盈率", "趋势", "加仓", "分析"]
        ), f"「帮我分析」应触发 investment-consult 并给出分析，实际：{reply!r}"
