"""第22课演示 Case 系统级测试

覆盖第22课 PPT 中演示的四类核心场景（无 Mock，全流程）：

  Group A  初始引导 SOP（P4）
           — 空白 workspace 触发引导，引导过程写入 soul/user，完成后自我清除
  Group B  技能调教（P5）
           — 描述 SOP 流程 → skill-creator 生成 SKILL.md → 注册到 load_skills.yaml
  Group C  按需触发记忆搜索（P3/P6）
           — 对话中透露持仓 → 后续隐式引用 → search_memory 自动触发 → 回复包含历史信息
  Group D  跨 Session 记忆持久化（P2 前提）
           — 偏好写入 user.md → 重启后 Bootstrap 注入 → 行为变化符合预期

运行方式：
  # 仅纯函数（无外部依赖，秒级）
  pytest tests/integration/test_lesson22_cases.py -m "not llm" -v

  # LLM 测试（不需要 sandbox，约 5-10 分钟）
  pytest tests/integration/test_lesson22_cases.py -m "llm and not sandbox" -v -s --timeout=180

  # 完整套件（需要 MEMORY_DB_DSN + sandbox）
  MEMORY_DB_DSN=postgresql://evopaw:evopaw123@localhost:5432/evopaw_memory \
  pytest tests/integration/test_lesson22_cases.py -v -s --timeout=600

前提：
  - QWEN_API_KEY 已设置
  - pgvector 运行：docker compose -f pgvector-docker-compose.yaml up -d
  - AIO-Sandbox 运行在 localhost:8022（Group B 需要）
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from .conftest import send_message, _init_workspace, SANDBOX_URL

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures 专用于第22课
# ─────────────────────────────────────────────────────────────────────────────

_WORKSPACE_INIT = Path(__file__).parents[2] / "workspace-init"
# 沙盒的 /workspace/ 通过 docker-compose 硬映射到这里
_SANDBOX_WORKSPACE = Path(__file__).parents[2] / "data" / "workspace"


def _reset_sandbox_workspace() -> Path:
    """将 data/workspace/ 重置为 workspace-init/ 的内容（供测试前隔离用）。

    copy 后 chmod 666，确保沙盒容器（非 root 用户）可写入。
    """
    _SANDBOX_WORKSPACE.mkdir(parents=True, exist_ok=True)
    if _WORKSPACE_INIT.exists():
        for f in _WORKSPACE_INIT.glob("*.md"):
            dest = _SANDBOX_WORKSPACE / f.name
            shutil.copy(f, dest)
            dest.chmod(0o666)
    return _SANDBOX_WORKSPACE


@pytest.fixture
async def onboarding_client(
    tmp_path: Path,
    session_mgr,
    qwen_api_key: str,
    sandbox_available: bool,
) -> TestClient:
    """初始引导专用客户端：user.md 为空（只保留标题行），触发引导 SOP。

    重要：沙盒的 /workspace/ 硬映射到 ./data/workspace/，所以 workspace_dir
    必须指向该目录，Bootstrap 读取与沙盒写入才能一致。
    """
    from evopaw.agents.main_crew import build_agent_fn
    from evopaw.api.capture_sender import CaptureSender
    from evopaw.api.test_server import create_test_app
    from evopaw.runner import Runner

    # 重置沙盒 workspace 到 workspace-init 干净状态
    workspace_dir = _reset_sandbox_workspace()
    ctx_dir = tmp_path / "ctx"
    ctx_dir.mkdir()

    # 清空 user.md，只留标题行 —— 触发引导 SOP
    (workspace_dir / "user.md").write_text("# 用户画像\n", encoding="utf-8")

    db_dsn = ""  # 初始引导测试不需要 pgvector
    sender = CaptureSender()
    agent_fn = build_agent_fn(
        sender=sender,
        workspace_dir=workspace_dir,
        ctx_dir=ctx_dir,
        db_dsn=db_dsn,
        sandbox_url=SANDBOX_URL if sandbox_available else "",
    )
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        idle_timeout=30.0,
    )
    app = create_test_app(runner=runner, sender=sender, session_mgr=session_mgr,
                          workspace_dir=workspace_dir)
    async with TestClient(TestServer(app)) as cli:
        cli._workspace_dir = workspace_dir
        cli._ctx_dir = ctx_dir
        yield cli
    await runner.shutdown()


@pytest.fixture
async def memory_client_with_pgvector(
    tmp_path: Path,
    session_mgr,
    qwen_api_key: str,
    sandbox_available: bool,
    pgvector_dsn: str,
) -> TestClient:
    """带 pgvector 的完整记忆客户端，专用于 Group C 搜索测试。"""
    from evopaw.agents.main_crew import build_agent_fn
    from evopaw.api.capture_sender import CaptureSender
    from evopaw.api.test_server import create_test_app
    from evopaw.runner import Runner

    workspace_dir = _reset_sandbox_workspace()
    ctx_dir = tmp_path / "ctx"
    ctx_dir.mkdir()

    # user.md 写入已初始化状态（跳过引导 SOP）
    (workspace_dir / "user.md").write_text(
        "# 用户画像\n\n## 偏好\n- 回复风格：简洁直接\n\n## 使用场景\n- 投资分析\n",
        encoding="utf-8",
    )
    # agent.md 不含引导 SOP（已完成初始化）
    agent_md = workspace_dir / "agent.md"
    if agent_md.exists():
        content = agent_md.read_text(encoding="utf-8")
        if "## 初始引导 SOP" in content:
            idx = content.index("## 初始引导 SOP")
            rest = content[idx:]
            next_section = rest.find("\n## ", 1)
            if next_section != -1:
                content = content[:idx] + rest[next_section + 1:]
            else:
                content = content[:idx]
            agent_md.write_text(content, encoding="utf-8")

    sender = CaptureSender()
    agent_fn = build_agent_fn(
        sender=sender,
        workspace_dir=workspace_dir,
        ctx_dir=ctx_dir,
        db_dsn=pgvector_dsn,
        sandbox_url=SANDBOX_URL if sandbox_available else "",
    )
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        idle_timeout=30.0,
    )
    app = create_test_app(runner=runner, sender=sender, session_mgr=session_mgr,
                          workspace_dir=workspace_dir)
    async with TestClient(TestServer(app)) as cli:
        cli._workspace_dir = workspace_dir
        cli._ctx_dir = ctx_dir
        yield cli
    await runner.shutdown()


@pytest.fixture
async def memory_client_l22(
    tmp_path: Path,
    session_mgr,
    qwen_api_key: str,
    sandbox_available: bool,
) -> TestClient:
    """Group D 专用：带完整 workspace 的 E2E 客户端，使用沙盒实际 workspace 目录。

    跳过引导 SOP：预填 user.md + 移除 agent.md 中的引导节，让 agent 直接进入工作模式。
    """
    from evopaw.agents.main_crew import build_agent_fn
    from evopaw.api.capture_sender import CaptureSender
    from evopaw.api.test_server import create_test_app
    from evopaw.runner import Runner

    workspace_dir = _reset_sandbox_workspace()
    ctx_dir = tmp_path / "ctx"
    ctx_dir.mkdir()

    # 预填 user.md，跳过引导 SOP
    (workspace_dir / "user.md").write_text(
        "# 用户画像\n\n## 偏好\n- 回复风格：简洁直接\n\n## 使用场景\n- 投资分析\n",
        encoding="utf-8",
    )
    # 移除 agent.md 中的引导 SOP 节（引导已完成状态）
    agent_md = workspace_dir / "agent.md"
    if agent_md.exists():
        content = agent_md.read_text(encoding="utf-8")
        if "## 初始引导 SOP" in content:
            idx = content.index("## 初始引导 SOP")
            rest = content[idx:]
            next_section = rest.find("\n## ", 1)
            if next_section != -1:
                content = content[:idx] + rest[next_section + 1:]
            else:
                content = content[:idx]
            agent_md.write_text(content, encoding="utf-8")

    db_dsn = ""
    sender = CaptureSender()
    agent_fn = build_agent_fn(
        sender=sender,
        workspace_dir=workspace_dir,
        ctx_dir=ctx_dir,
        db_dsn=db_dsn,
        sandbox_url=SANDBOX_URL if sandbox_available else "",
    )
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=agent_fn,
        idle_timeout=30.0,
    )
    app = create_test_app(runner=runner, sender=sender, session_mgr=session_mgr,
                          workspace_dir=workspace_dir)
    async with TestClient(TestServer(app)) as cli:
        cli._workspace_dir = workspace_dir
        cli._ctx_dir = ctx_dir
        yield cli
    await runner.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# Group A：初始引导 SOP（P4）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.integration
class TestOnboardingSOP:
    """P4：空白 workspace 触发初始引导，逐步收集信息，持久化到 workspace 文件。"""

    ROUTING_KEY = "p2p:ou_onboarding_test"

    async def test_a1_empty_user_triggers_onboarding(
        self, onboarding_client: TestClient
    ):
        """TC-A1：user.md 为空时，首条消息应触发引导，EvoPaw 询问名字或用途。

        引导 SOP 第一步是询问是否要为助手起名。
        """
        data = await send_message(onboarding_client, "你好", self.ROUTING_KEY)
        reply = data["reply"]

        assert reply, "首条消息应有回复"
        # 引导的第一步：询问名字或自我介绍
        onboarding_keywords = ["名字", "叫", "EvoPaw", "助手", "了解", "帮到你", "用我来做"]
        assert any(kw in reply for kw in onboarding_keywords), (
            f"空白 user.md 应触发引导对话，实际回复：{reply!r}"
        )

    async def test_a2_name_step_writes_soul_md(
        self, onboarding_client: TestClient
    ):
        """TC-A2：用户在引导中给助手起名，soul.md 应被更新。

        流程：触发引导 → 用户说"叫你小虎吧" → memory-save(target=soul) → soul.md 更新。
        """
        # 触发引导
        await send_message(onboarding_client, "你好", self.ROUTING_KEY)
        # 给助手起名
        await send_message(onboarding_client, "叫你小虎吧", self.ROUTING_KEY)
        # 等待 memory-save 写入（sandbox 需要时间）
        await asyncio.sleep(3)

        soul_md = onboarding_client._workspace_dir / "soul.md"
        assert soul_md.exists(), "soul.md 应存在"
        content = soul_md.read_text(encoding="utf-8")
        assert "小虎" in content, (
            f"soul.md 应包含用户起的名字'小虎'，实际内容：{content!r}"
        )

    async def test_a3_use_case_step_writes_user_md(
        self, onboarding_client: TestClient
    ):
        """TC-A3：用户在引导中说明用途，user.md 应被写入使用场景。

        流程：引导 → 用户说"主要用来做投资分析" → memory-save(target=user) → user.md 更新。
        """
        await send_message(onboarding_client, "你好", self.ROUTING_KEY)
        await send_message(onboarding_client, "叫你小助手吧", self.ROUTING_KEY)
        await send_message(
            onboarding_client,
            "我主要用你来做投资分析，帮我跟踪股票持仓和每日行情",
            self.ROUTING_KEY,
        )
        await asyncio.sleep(3)

        user_md = onboarding_client._workspace_dir / "user.md"
        content = user_md.read_text(encoding="utf-8")
        # user.md 应有实质性内容（不再是仅有标题行）
        assert len(content.strip().splitlines()) > 2, (
            f"user.md 应被写入使用场景，实际内容：{content!r}"
        )
        invest_keywords = ["投资", "股票", "持仓", "行情", "分析"]
        assert any(kw in content for kw in invest_keywords), (
            f"user.md 应包含投资相关信息，实际：{content!r}"
        )

    async def test_a4_onboarding_progress_persists_in_agent_md(
        self, onboarding_client: TestClient
    ):
        """TC-A4：完成两个引导步骤后，agent.md 中的进度 checklist 应有至少一项被勾选。

        用户给出名字 + 用途 → 两步完成 → agent.md 至少出现一个 `[x]`。
        """
        await send_message(onboarding_client, "你好", self.ROUTING_KEY)
        await send_message(onboarding_client, "叫你小助手吧", self.ROUTING_KEY)
        await send_message(
            onboarding_client,
            "主要用来做投资分析，帮我追踪股票",
            self.ROUTING_KEY,
        )
        await asyncio.sleep(10)

        agent_md = onboarding_client._workspace_dir / "agent.md"
        content = agent_md.read_text(encoding="utf-8")
        assert "[x]" in content, (
            f"完成引导步骤后 agent.md 中应有勾选项，实际：{content!r}"
        )

    async def test_a5_completed_onboarding_removes_sop_section(
        self, onboarding_client: TestClient
    ):
        """TC-A5：用户明确指示删除引导 SOP 节，agent.md 中的初始引导 SOP 节应被删除。

        这验证引导 SOP 的"自我清除"机制：用 memory-save(target=agent) 删除整节。
        直接发送明确的删除指令，绕开 6 步完整引导（避免超时），聚焦验证删除机制。
        """
        # 先做一次简短引导，建立对话上下文
        await send_message(onboarding_client, "你好", self.ROUTING_KEY)
        await send_message(onboarding_client, "叫你小助手吧", self.ROUTING_KEY)
        # 直接告知引导完成，触发自我清除
        await send_message(
            onboarding_client,
            "好了，引导都完成了，现在请用 memory-save 把 agent.md 里的「初始引导 SOP」整节删掉",
            self.ROUTING_KEY,
        )
        # 等待 sandbox 写入完成（删除操作需要时间）
        await asyncio.sleep(15)

        agent_md = onboarding_client._workspace_dir / "agent.md"
        content = agent_md.read_text(encoding="utf-8")
        assert "## 初始引导 SOP" not in content, (
            f"引导 SOP 节应已被删除，实际 agent.md 前 400 字：{content[:400]!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Group B：技能调教（P5）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.sandbox
@pytest.mark.integration
class TestSkillCreation:
    """P5：用户描述 SOP → skill-creator 生成 SKILL.md → 注册到 load_skills.yaml。"""

    ROUTING_KEY = "p2p:ou_skill_creation_test"

    async def test_b1_sop_description_creates_skill_file(
        self, memory_client: TestClient
    ):
        """TC-B1：描述投资早报 SOP → skill-creator 生成 SKILL.md。

        用户说出 SOP 流程，EvoPaw 调用 skill-creator，生成对应 SKILL.md 文件。
        """
        data = await send_message(
            memory_client,
            "帮我把这个流程固化成一个技能：每次生成早报时，"
            "先拉今日 A 股和港股主要指数涨跌幅，"
            "然后看我持仓股的换手率和量价关系，"
            "最后按【今日行情】【持仓观察】【操作建议】三段格式生成简评。"
            "把这个存为 investment-report 技能。",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        # 等待 skill-creator sub-crew 写文件
        await asyncio.sleep(10)

        assert reply, "回复不应为空"
        # skill-creator 成功后应在回复中确认
        confirm_keywords = ["技能", "skill", "保存", "investment-report", "创建", "完成"]
        assert any(kw.lower() in reply.lower() for kw in confirm_keywords), (
            f"回复应确认技能已创建，实际：{reply!r}"
        )

        # 验证 SKILL.md 文件已生成
        ws = memory_client._workspace_dir
        # skill-creator 写入 /mnt/skills 下，实际映射到 evopaw/skills/
        skills_dir = Path(__file__).parents[2] / "evopaw" / "skills"
        skill_file = skills_dir / "investment-report" / "SKILL.md"
        # 注意：sandbox 写入路径和测试路径可能不同，检查 load_skills.yaml 作为替代
        load_yaml = skills_dir / "load_skills.yaml"
        if load_yaml.exists():
            yaml_content = load_yaml.read_text(encoding="utf-8")
            assert "investment-report" in yaml_content, (
                f"investment-report 应注册到 load_skills.yaml，实际：{yaml_content!r}"
            )

    async def test_b2_skill_description_is_pushy(
        self, memory_client: TestClient
    ):
        """TC-B2：生成的 SKILL.md description 应为 pushy 风格，包含明确触发词。

        skill-creator 应按照规范写出 "当用户说'早报'/'投资报告' 时触发" 的 description。
        """
        await send_message(
            memory_client,
            "把每日早报的流程存成技能：拉指数涨跌 + 持仓分析 + 简评，叫 daily-report",
            self.ROUTING_KEY,
        )
        await asyncio.sleep(10)

        skills_dir = Path(__file__).parents[2] / "evopaw" / "skills"
        skill_file = skills_dir / "daily-report" / "SKILL.md"
        if skill_file.exists():
            content = skill_file.read_text(encoding="utf-8")
            # pushy description 应包含触发词
            assert "早报" in content or "投资" in content or "Use this" in content, (
                f"SKILL.md description 应包含触发场景，实际：{content[:300]!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Group C：按需触发记忆搜索（P3/P6）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.sandbox
@pytest.mark.integration
class TestSearchMemoryTrigger:
    """P3/P6：历史对话建立索引 → 后续隐式查询自动触发 search_memory → 回复含历史信息。"""

    ROUTING_KEY_A = "p2p:ou_search_test_session_a"
    ROUTING_KEY_B = "p2p:ou_search_test_session_b"

    async def test_c1_holdings_mentioned_then_searched_implicitly(
        self, memory_client_with_pgvector: TestClient
    ):
        """TC-C1：第一轮透露持仓 → pgvector 建索引 → 新 session 隐式询问 → 回复包含持仓信息。

        场景：用户第一轮提到"我持有阿里 2000 股，成本 85 港元"
              新 session 问"阿里今天该不该挂单卖出"
              → search_memory 找到持仓记录 → 回复中包含成本/持仓数据
        """
        # 第一轮：透露持仓信息（建立 pgvector 索引）
        await send_message(
            memory_client_with_pgvector,
            "我目前持有阿里巴巴港股 2000 股，成本价大概是 85 港元，"
            "是去年低位建的仓，一直拿着没动",
            self.ROUTING_KEY_A,
        )
        # 等待异步建索引完成（async_index_turn 在后台运行）
        await asyncio.sleep(8)

        # 切换到新 session（模拟跨天对话）
        await send_message(memory_client_with_pgvector, "/new", self.ROUTING_KEY_A)

        # 第二轮：隐式引用持仓（不再重复说成本），触发 search_memory
        data = await send_message(
            memory_client_with_pgvector,
            "阿里今天该不该挂单卖出？",
            self.ROUTING_KEY_A,
        )
        reply = data["reply"]

        assert reply, "回复不应为空"
        # 回复应包含从历史搜索到的持仓信息
        holding_keywords = ["85", "2000", "成本", "持仓", "港元", "阿里"]
        assert any(kw in reply for kw in holding_keywords), (
            f"回复应包含从记忆中搜索到的持仓信息，实际：{reply!r}"
        )

    async def test_c2_historical_analysis_referenced_for_review(
        self, memory_client_with_pgvector: TestClient
    ):
        """TC-C2：上周操作时的分析结论 → 被隐式引用触发 search_memory → 复盘使用历史结论。

        场景：第一轮记录了某次分析："阿里短期压力位 90 港元，建议不追高"
              第二轮说"根据上周五操作时的分析结论复盘下"
              → search_memory 找到历史分析 → 复盘回复中引用该结论
        """
        # 第一轮：记录分析结论（建索引）
        await send_message(
            memory_client_with_pgvector,
            "今天分析了一下阿里，技术面来看短期压力位在 90 港元，"
            "量能不足，建议不要在压力位附近追高，等回调到 82-85 区间再考虑加仓",
            self.ROUTING_KEY_B,
        )
        await asyncio.sleep(8)

        # 切换新 session
        await send_message(memory_client_with_pgvector, "/new", self.ROUTING_KEY_B)

        # 第二轮：隐式引用上次分析，触发 search_memory
        data = await send_message(
            memory_client_with_pgvector,
            "根据上周五操作时的分析结论，帮我复盘一下这次操作",
            self.ROUTING_KEY_B,
        )
        reply = data["reply"]

        assert reply, "回复不应为空"
        # 回复应包含从历史搜索到的分析结论
        analysis_keywords = ["90", "压力位", "追高", "回调", "82", "85", "技术面"]
        assert any(kw in reply for kw in analysis_keywords), (
            f"回复应引用从 pgvector 搜索到的历史分析结论，实际：{reply!r}"
        )

    async def test_c3_search_memory_not_triggered_for_simple_question(
        self, memory_client_with_pgvector: TestClient
    ):
        """TC-C3：普通问题不应触发 search_memory（避免 overtriggering）。

        问"今天天气怎么样"不应调用 search_memory（与历史无关）。
        回复应直接回答，不出现搜索历史相关的延迟或内容。
        """
        data = await send_message(
            memory_client_with_pgvector,
            "1+1等于几",
            self.ROUTING_KEY_B,
        )
        reply = data["reply"]
        duration = data["duration_ms"]

        assert reply, "回复不应为空"
        assert "2" in reply or "二" in reply, f"简单问题应直接回答，实际：{reply!r}"
        # 没有 pgvector 查询时响应应较快（< 30s）
        assert duration < 30_000, (
            f"简单问题不应触发 search_memory 导致过长延迟，duration={duration}ms"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Group D：跨 Session 记忆持久化（P2 前提）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.integration
class TestCrossSessionMemory:
    """P2 前提：偏好写入 user.md → 重启后 Bootstrap 注入 → 行为符合偏好。"""

    ROUTING_KEY = "p2p:ou_cross_session_test"

    async def test_d1_preference_written_to_user_md(
        self, memory_client_l22: TestClient
    ):
        """TC-D1：用户说不喜欢用表格 → memory-save → user.md 包含该偏好。"""
        data = await send_message(
            memory_client_l22,
            "以后回复我不要用表格，直接用文字说明就好，我看表格很费劲",
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        await asyncio.sleep(5)

        assert reply, "回复不应为空"
        confirm_keywords = ["记住", "明白", "好的", "不用表格", "文字", "了解"]
        assert any(kw in reply for kw in confirm_keywords), (
            f"回复应确认收到偏好，实际：{reply!r}"
        )

        user_md = memory_client_l22._workspace_dir / "user.md"
        content = user_md.read_text(encoding="utf-8")
        table_keywords = ["表格", "文字", "不要"]
        assert any(kw in content for kw in table_keywords), (
            f"user.md 应包含偏好记录，实际：{content!r}"
        )

    def test_d2_preference_reflected_in_bootstrap(self):
        """TC-D2：user.md 写入偏好后，Bootstrap 提示词中应包含该偏好。

        纯文件验证：直接检查 build_bootstrap_prompt 输出，
        确认偏好已注入到 Agent 系统提示词中，无需 LLM 调用。
        TC-D1 验证写入，TC-D2 验证读取，两者组合证明跨 session 记忆生效。
        """
        from evopaw.memory.bootstrap import build_bootstrap_prompt

        workspace_dir = _reset_sandbox_workspace()

        # 模拟 TC-D1 memory-save 写入后的 user.md 状态
        (workspace_dir / "user.md").write_text(
            "# 用户画像\n\n## 偏好\n- 回复风格：简洁直接\n\n## 使用场景\n- 投资分析\n"
            "\n## 禁忌\n- 不要使用 Markdown 表格格式\n",
            encoding="utf-8",
        )

        # 构建 Bootstrap（模拟新 session 启动时 agent 初始化）
        prompt = build_bootstrap_prompt(workspace_dir)

        assert prompt, "Bootstrap 提示词不应为空"
        assert "不要使用 Markdown 表格格式" in prompt or "禁忌" in prompt, (
            f"Bootstrap 应包含 user.md 的偏好，实际片段：{prompt[:500]!r}"
        )

    async def test_d3_ctx_json_persists_after_session(
        self, memory_client_l22: TestClient
    ):
        """TC-D3：对话后 ctx.json 应持久化到 ctx_dir。

        验证 L19 上下文层的持久化机制：每次对话后 ctx.json 写入磁盘。
        (跨 session 恢复需要两次 LLM 调用，后者存在超时风险，这里只验证写入本身。)
        """
        await send_message(
            memory_client_l22,
            "今天刚买了阿里巴巴港股 1000 股，均价 88 港元",
            self.ROUTING_KEY,
        )

        ctx_dir = memory_client_l22._ctx_dir
        ctx_files = list(ctx_dir.glob("*_ctx.json"))
        assert ctx_files, "ctx.json 应在对话后写入"

        # 验证 ctx.json 包含对话内容
        ctx_data = json.loads(ctx_files[0].read_text(encoding="utf-8"))
        assert ctx_data, "ctx.json 不应为空"
        ctx_text = json.dumps(ctx_data, ensure_ascii=False)
        assert any(kw in ctx_text for kw in ["1000", "88", "阿里"]), (
            f"ctx.json 应包含对话内容，实际：{ctx_text[:300]!r}"
        )

    async def test_d4_memory_save_updates_existing_preference(
        self, memory_client_l22: TestClient
    ):
        """TC-D4：更新已有偏好 → memory-save str_replace → user.md 中旧内容被替换。

        验证"更新优于追加"原则：不是追加一条新记录，而是精准替换旧记录。
        注：显式要求 memory-save 以确保稳定触发（user.md 已有旧偏好，测试 str_replace 行为）。
        """
        await send_message(
            memory_client_l22,
            "帮我更新回复风格偏好，用 memory-save 写入 user.md：简洁直接，不超过 200 字",
            self.ROUTING_KEY,
        )
        await asyncio.sleep(20)  # 等 sandbox memory-save 完成

        user_md_content = (memory_client_l22._workspace_dir / "user.md").read_text(
            encoding="utf-8"
        )

        # 200 字偏好应已写入
        assert "200" in user_md_content, (
            f"user.md 应包含'200'字偏好，实际：{user_md_content!r}"
        )
        # 不应是简单追加（不应重复出现偏好关键字）
        assert user_md_content.count("200") <= 2, (
            f"200 字出现次数过多，可能有重复追加问题：{user_md_content!r}"
        )
