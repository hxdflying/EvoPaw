"""系统级测试：evopaw-with-memory 三层记忆

场景覆盖（无 Mock，全流程）：
  场景1  初次见面 ——空白助手
  场景2  建立记忆 ——让助手认识我
  场景3  固化工作流——把重复动作变成技能
  场景4  日常使用 ——跨天对话不失忆
  场景5  记忆维护 ——防止助手脑子乱
  场景6  系统健壮性——异常和边界

运行方式：
  # 纯函数（无外部依赖，秒级）
  pytest tests/integration/test_memory_e2e.py -m "not llm" -v

  # LLM 测试（不需要 sandbox，约5分钟）
  pytest tests/integration/test_memory_e2e.py -m "llm and not sandbox" -v -s --timeout=120

  # 完整套件
  pytest tests/integration/test_memory_e2e.py -v -s --timeout=600
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient

from .conftest import send_message, write_ctx


# ─────────────────────────────────────────────────────────────────────────────
# 场景5（纯函数）：TC-5.2 Bootstrap memory.md 200行截断
# ─────────────────────────────────────────────────────────────────────────────

class TestBootstrapTruncation:
    """TC-5.2：memory.md 超 200 行时只注入前 200 行。

    纯函数，无需 LLM。
    """

    def test_early_marker_injected_late_marker_excluded(self, tmp_path: Path):
        """第50行的内容应出现在 prompt，第250行的不应出现。"""
        from evopaw.memory.bootstrap import build_bootstrap_prompt

        workspace = tmp_path / "ws"
        workspace.mkdir()

        # 构造 300 行 memory.md
        lines = [f"- 记忆条目 {i:03d}" for i in range(300)]
        lines[49]  = "- EARLY_MARKER: 早期重要信息"   # 第50行，应注入
        lines[249] = "- LATE_MARKER: 超出截断线的信息"  # 第250行，不应注入
        (workspace / "memory.md").write_text("\n".join(lines), encoding="utf-8")

        prompt = build_bootstrap_prompt(workspace)

        assert "EARLY_MARKER" in prompt, "第50行应被注入到 bootstrap prompt"
        assert "LATE_MARKER" not in prompt, "第250行超出200行截断，不应被注入"

    def test_missing_files_do_not_raise(self, tmp_path: Path):
        """TC-1.1 的纯函数前置：workspace 为空时 build_bootstrap_prompt 不抛异常。"""
        from evopaw.memory.bootstrap import build_bootstrap_prompt

        empty_ws = tmp_path / "empty"
        empty_ws.mkdir()

        # 不应抛出任何异常
        prompt = build_bootstrap_prompt(empty_ws)
        # 返回值可以为空字符串，但必须是字符串
        assert isinstance(prompt, str)

    def test_soul_content_included(self, tmp_path: Path):
        """soul.md 的内容应出现在 prompt 中。"""
        from evopaw.memory.bootstrap import build_bootstrap_prompt

        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "soul.md").write_text("你是 SOUL_MARKER 助手", encoding="utf-8")

        prompt = build_bootstrap_prompt(workspace)
        assert "SOUL_MARKER" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数：TC-C1 prune_tool_results + TC-C3 反雪崩
# ─────────────────────────────────────────────────────────────────────────────

class TestPruneAndCompress:
    """TC-C1/C3：prune_tool_results 和 maybe_compress 的行为验证。"""

    def _make_messages(self, n_turns: int, include_tool: bool = False) -> list[dict]:
        """构造 n_turns 轮 user/assistant 消息，可选在第0轮插入 tool result。"""
        msgs: list[dict] = []
        if include_tool:
            msgs.append({
                "role": "tool",
                "content": "SECRET_TOOL_CONTENT_EARLY",
                "tool_call_id": "tc_early",
            })
        for i in range(n_turns):
            msgs.append({"role": "user",      "content": f"用户消息 {i}"})
            msgs.append({"role": "assistant", "content": f"助手回复 {i}"})
        return msgs

    def test_old_tool_result_pruned(self):
        """TC-C1：超过 keep_turns 的 tool result 应被替换为 [已剪枝]。"""
        from evopaw.memory.context_mgmt import prune_tool_results

        msgs = self._make_messages(n_turns=12, include_tool=True)
        original_tool_content = msgs[0]["content"]
        assert original_tool_content == "SECRET_TOOL_CONTENT_EARLY"

        prune_tool_results(msgs, keep_turns=10)

        assert msgs[0]["content"] == "[已剪枝]", (
            f"早期 tool result 应被剪枝，实际：{msgs[0]['content']!r}"
        )

    def test_recent_tool_result_not_pruned(self):
        """TC-C1 补充：keep_turns 内的 tool result 不应被剪枝。"""
        from evopaw.memory.context_mgmt import prune_tool_results

        msgs = self._make_messages(n_turns=3, include_tool=False)
        # 在最后追加一条新 tool result（刚发生）
        msgs.append({
            "role": "tool",
            "content": "RECENT_TOOL_CONTENT",
            "tool_call_id": "tc_recent",
        })

        prune_tool_results(msgs, keep_turns=10)

        last = msgs[-1]
        assert last["content"] == "RECENT_TOOL_CONTENT", (
            "最近的 tool result 不应被剪枝"
        )

    def test_anti_snowball_context_summary_not_counted(self):
        """TC-C3：<context_summary> system 消息不计入压缩阈值，不触发雪崩。"""
        from evopaw.memory.context_mgmt import maybe_compress

        # 构造包含大量 context_summary 的 messages
        # summary 的 token 量很大，但非 system 消息很少
        big_summary = "摘要内容" * 500  # 约 500*3=1500 字
        messages = [
            {"role": "system", "content": "你是EvoPaw"},
            {"role": "system", "content": f"<context_summary>{big_summary}</context_summary>"},
            {"role": "system", "content": f"<context_summary>{big_summary}</context_summary>"},
            # 非 system 消息 token 很少，不应触发压缩
            {"role": "user",      "content": "最近一条消息"},
            {"role": "assistant", "content": "最近一条回复"},
        ]
        original_len = len(messages)
        original_snapshot = [dict(m) for m in messages]

        # maybe_compress 需要 LLMCallHookContext，但阈值未达到不应调用 LLM
        # 传入 None 作为 context：若触发了压缩（错误情况），会抛出 AttributeError
        maybe_compress(messages, context=None)  # type: ignore[arg-type]

        assert len(messages) == original_len, (
            "非 system 消息未超阈值，不应触发压缩（反雪崩验证）"
        )
        for orig, curr in zip(original_snapshot, messages):
            assert orig == curr, "消息内容不应被修改"


# ─────────────────────────────────────────────────────────────────────────────
# 场景1：TC-1.1/1.2/1.3 初次见面
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.integration
class TestFirstMeeting:
    """场景1：用户拿到全新 EvoPaw，workspace 为空，第一次对话。"""

    ROUTING_KEY = "p2p:ou_first_meeting"

    async def test_tc1_1_blank_assistant_responds_normally(
        self, memory_client: TestClient
    ):
        """TC-1.1：空白 workspace，助手能正常回应不崩溃，且对用户没有先验知识。

        注意：用户名不在消息里，询问"你知道我叫什么"来验证助手无先验记忆。
        """
        # 清空 workspace（移除 workspace-init 复制过来的文件）
        ws = memory_client._workspace_dir
        for f in ws.glob("*.md"):
            f.unlink()

        # 先用一条不含名字的消息建立 session
        await send_message(memory_client, "你好", self.ROUTING_KEY)
        # 再问助手是否知道用户的名字
        data = await send_message(memory_client, "你知道我叫什么吗？", self.ROUTING_KEY)
        reply = data["reply"]

        assert reply, "空白 workspace 下助手仍应有回复"
        # 助手应表示不知道（没有任何先验信息可以让它知道用户的名字）
        unknown_keywords = ["不知道", "没有", "不清楚", "没告诉", "介绍", "叫什么", "不了解"]
        assert any(kw in reply for kw in unknown_keywords), (
            f"空白 workspace 下助手应表示不知道用户名字，实际：{reply!r}"
        )

    async def test_tc1_2_blank_assistant_can_describe_capabilities(
        self, memory_client: TestClient
    ):
        """TC-1.2：空白 workspace，助手能描述自己的基本能力。"""
        ws = memory_client._workspace_dir
        for f in ws.glob("*.md"):
            f.unlink()

        data = await send_message(memory_client, "你能帮我做什么？", self.ROUTING_KEY)
        reply = data["reply"]

        assert reply, "回复不应为空"
        # 助手应至少描述一种能力（文件处理 / 搜索 / 飞书 / 任务等关键词之一）
        capability_keywords = ["文件", "搜索", "飞书", "任务", "skill", "Skill", "帮助", "工具"]
        assert any(kw in reply for kw in capability_keywords), (
            f"回复应包含能力描述，实际：{reply!r}"
        )

    async def test_tc1_3_ctx_json_written_after_first_turn(
        self, memory_client: TestClient
    ):
        """TC-1.3：首轮对话结束后，ctx.json 和 raw.jsonl 自动写入磁盘。"""
        data = await send_message(memory_client, "今天天气真好", self.ROUTING_KEY)
        assert data["reply"], "需要有回复才能验证持久化"

        session_id = data["session_id"]
        ctx_dir: Path = memory_client._ctx_dir

        # 等待异步写入（run_and_index 是 await，但 index 是 create_task）
        await asyncio.sleep(1)

        ctx_file  = ctx_dir / f"{session_id}_ctx.json"
        raw_file  = ctx_dir / f"{session_id}_raw.jsonl"

        assert ctx_file.exists(), f"ctx.json 应已写入：{ctx_file}"
        assert raw_file.exists(), f"raw.jsonl 应已写入：{raw_file}"

        ctx_data = json.loads(ctx_file.read_text(encoding="utf-8"))
        assert isinstance(ctx_data, list), "ctx.json 应为 JSON 列表"
        roles = {m.get("role") for m in ctx_data}
        assert "user" in roles,      "ctx.json 应含 user 消息"
        assert "assistant" in roles, "ctx.json 应含 assistant 消息"

        raw_lines = raw_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(raw_lines) >= 1, "raw.jsonl 至少有一行记录"
        json.loads(raw_lines[0])   # 应为合法 JSON


# ─────────────────────────────────────────────────────────────────────────────
# 场景4/6（无 sandbox）：ctx 恢复、session 隔离、健壮性
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.integration
class TestCtxRestoreAndIsolation:
    """TC-4.1/6.2/6.3：跨 session ctx 恢复、多用户隔离、重启后恢复。"""

    async def test_tc4_1_ctx_restored_across_sessions(
        self, memory_client: TestClient
    ):
        """TC-4.1：昨天的 ctx.json 里说的事，今天新 session 还能记得。"""
        ctx_dir: Path = memory_client._ctx_dir
        session_id = "session-yesterday"

        # 构造"昨天"的 ctx.json（直接写入，不经过 LLM）
        write_ctx(ctx_dir, session_id, [
            {"role": "system",    "content": "你是EvoPaw（旧backstory，应被过滤）"},
            {"role": "user",      "content": "第22课准备周五发布"},
            {"role": "assistant", "content": "好的，我记住了，第22课计划周五发布。"},
        ])

        # 用相同 session_id 发消息（simulate Runner 找到同一 session）
        # 通过 routing_key 让 SessionManager 映射到同一 session
        # 实际上 routing_key→session 是 SessionManager 管理的，
        # 测试中 routing_key 决定 session，所以先查 session_id
        # 简化：直接用 /api/test/message 的 routing_key，由 session_mgr 分配
        # 注意：session_id 是 session_mgr 内部生成的，我们无法直接控制
        # 改为：先发一条消息让 session_mgr 创建 session，
        # 然后把 ctx.json 写到正确路径，再发第二条消息验证
        data_first = await send_message(
            memory_client, "（建立session用）", "p2p:ou_yesterday_test"
        )
        actual_session_id = data_first["session_id"]

        # 覆盖写入 ctx.json（模拟昨天的对话）
        write_ctx(ctx_dir, actual_session_id, [
            {"role": "system",    "content": "你是EvoPaw（旧backstory，应被过滤）"},
            {"role": "user",      "content": "第22课准备周五发布"},
            {"role": "assistant", "content": "好的，我记住了，第22课计划周五发布。"},
        ])

        # 同一 routing_key 发第二条消息，应该读到 ctx.json
        data = await send_message(
            memory_client, "我上次说第22课什么时候发？", "p2p:ou_yesterday_test"
        )
        reply = data["reply"]

        assert "周五" in reply, (
            f"应从 ctx.json 恢复历史，知道'周五'，实际回复：{reply!r}"
        )

    async def test_tc6_3_restart_restores_ctx(
        self, memory_client: TestClient
    ):
        """TC-6.3：模拟 bot 重启——新 MemoryAwareCrew 实例读取已有 ctx.json。"""
        ctx_dir: Path = memory_client._ctx_dir

        # Step1：发消息建立 session
        data_init = await send_message(
            memory_client, "（建立session）", "p2p:ou_restart_test"
        )
        sid = data_init["session_id"]

        # Step2：写入"重启前的 ctx.json"
        write_ctx(ctx_dir, sid, [
            {"role": "system",    "content": "你是旧版EvoPaw"},
            {"role": "user",      "content": "PR_MARKER_42 的代码审查完成了"},
            {"role": "assistant", "content": "好的，PR_MARKER_42 已审查，主要问题：缺少错误处理。"},
        ])

        # Step3：同 routing_key 再发消息（新 MemoryAwareCrew 实例会读 ctx.json）
        data = await send_message(
            memory_client, "刚才那个 PR 的结论是什么？", "p2p:ou_restart_test"
        )
        reply = data["reply"]

        assert "PR_MARKER_42" in reply or "错误处理" in reply, (
            f"应从 ctx.json 恢复，知道 PR_MARKER_42 的结论，实际：{reply!r}"
        )

    async def test_tc6_2_sessions_isolated(
        self, memory_client: TestClient
    ):
        """TC-6.2：两个不同 routing_key 的 session 不互相污染。"""
        ctx_dir: Path = memory_client._ctx_dir

        # 建立 user_a 的 session，写入含特殊内容的 ctx
        data_a = await send_message(
            memory_client, "（建立 user_a session）", "p2p:ou_user_a"
        )
        write_ctx(ctx_dir, data_a["session_id"], [
            {"role": "system",    "content": "你是张三的专属助手"},
            {"role": "user",      "content": "我叫张三ISOLATION_MARKER"},
            {"role": "assistant", "content": "你好张三ISOLATION_MARKER"},
        ])

        # user_b 是全新 session（不同 routing_key）
        data_b = await send_message(
            memory_client, "你好，我是李四", "p2p:ou_user_b"
        )
        reply_b = data_b["reply"]

        assert "ISOLATION_MARKER" not in reply_b, (
            f"user_b 的回复不应含 user_a 的内容，实际：{reply_b!r}"
        )
        assert "张三" not in reply_b, (
            f"user_b 的回复不应出现'张三'，实际：{reply_b!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 场景6：TC-6.1 pgvector 不可达时对话不受影响
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.integration
class TestRobustness:
    """TC-4.3：上下文鲁棒性。

    注：原 TC-6.1（pgvector 不可达时降级）已归档，因其依赖已移除的
    evopaw.agents.main_crew；重写见 tests/archive/legacy_crewai/README.md。
    """

    async def test_tc4_3_multi_turn_context_coherent(
        self, memory_client: TestClient
    ):
        """TC-4.3：同 session 多轮追问，上下文不丢失。"""
        rk = "p2p:ou_multi_turn"

        await send_message(
            memory_client,
            "我在设计一个多 Agent 系统，用于自动生成周报，核心 Agent 叫 CORE_AGENT_MARKER",
            rk,
        )
        await send_message(memory_client, "这个系统有哪些 Agent？", rk)
        await send_message(memory_client, "负责数据收集的 Agent 用什么工具？", rk)

        # 最后一轮：考验上下文记忆
        data = await send_message(memory_client, "你刚才提到的核心 Agent 叫什么名字？", rk)
        reply = data["reply"]

        assert "CORE_AGENT_MARKER" in reply, (
            f"多轮追问应能记住第一轮提到的 CORE_AGENT_MARKER，实际：{reply!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 场景2（无 sandbox）：TC-2.2 Bootstrap 跨 Session 读取已有偏好
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.integration
class TestMemoryBootstrapCrossSession:
    """TC-2.2：user.md 里写了偏好，新 session 的 Bootstrap 能感知。"""

    async def test_tc2_2_user_preference_injected_via_bootstrap(
        self, memory_client: TestClient
    ):
        """TC-2.2：直接写入 user.md（模拟 memory-save 结果），
        新 session 中 Bootstrap 读取并体现在回复风格。"""
        ws: Path = memory_client._workspace_dir

        # 直接写入已保存的偏好（模拟 Session 1 的 memory-save 输出）
        user_md = ws / "user.md"
        existing = user_md.read_text(encoding="utf-8") if user_md.exists() else ""
        user_md.write_text(
            existing + "\n## 已记录偏好\n- 编程语言偏好：PREF_LANG_PYTHON\n",
            encoding="utf-8",
        )

        # 新 session（不同 routing_key → 新 session_id）
        data = await send_message(
            memory_client, "我喜欢哪种编程语言？", "p2p:ou_pref_test"
        )
        reply = data["reply"]

        assert "PREF_LANG_PYTHON" in reply or "Python" in reply, (
            f"Bootstrap 应读到 user.md 中的 PREF_LANG_PYTHON，实际：{reply!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 场景2（需 pgvector）：TC-2.3/G1 pgvector 索引写入验证
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.integration
class TestPgvectorIndexing:
    """TC-2.3/G1：对话结束后向量被写入 memories 表。"""

    async def test_tc_g1_turn_indexed_to_pgvector(
        self, memory_client: TestClient, pgvector_dsn: str
    ):
        """TC-G1：对话完成后，memories 表中有对应记录。"""
        import asyncpg

        rk = "p2p:ou_index_test"
        # 发送包含可搜索关键词的消息
        data = await send_message(
            memory_client,
            "我在做一个叫 PROJ_INDEXTEST 的项目，目标是自动生成日报",
            rk,
        )
        sid = data["session_id"]

        # 等待后台 async_index_turn 完成（最多 15s）
        deadline = time.monotonic() + 15
        conn = await asyncpg.connect(pgvector_dsn)
        try:
            count = 0
            while time.monotonic() < deadline:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM memories WHERE session_id = $1", sid
                )
                count = row["cnt"]
                if count >= 1:
                    break
                await asyncio.sleep(1)
        finally:
            await conn.close()

        assert count >= 1, f"memories 表应有 session_id={sid!r} 的记录"

    async def test_tc_g1_indexed_record_has_vector(
        self, memory_client: TestClient, pgvector_dsn: str
    ):
        """TC-G1 补充：写入的记录应含有非 NULL 的 summary_vec。"""
        import asyncpg

        rk = "p2p:ou_vec_test"
        data = await send_message(
            memory_client, "我的项目叫 VEC_TEST_PROJECT", rk
        )
        sid = data["session_id"]

        deadline = time.monotonic() + 15
        conn = await asyncpg.connect(pgvector_dsn)
        try:
            row = None
            while time.monotonic() < deadline:
                row = await conn.fetchrow(
                    "SELECT summary_vec IS NOT NULL AS has_vec FROM memories "
                    "WHERE session_id = $1 LIMIT 1",
                    sid,
                )
                if row:
                    break
                await asyncio.sleep(1)
        finally:
            await conn.close()

        assert row is not None, "应有记录写入"
        assert row["has_vec"], "summary_vec 不应为 NULL"


# ─────────────────────────────────────────────────────────────────────────────
# 场景2（需 sandbox）：TC-2.1 memory-save Skill 写入 user.md
# ─────────────────────────────────────────────────────────────────────────────

_SANDBOX_WORKSPACE = Path(__file__).parents[2] / "data" / "workspace"


@pytest.mark.llm
@pytest.mark.sandbox
@pytest.mark.integration
class TestMemorySaveSkill:
    """TC-2.1：用户让助手"记住"信息，memory-save Skill 写入 workspace 文件。

    注：AIO-Sandbox 的 /workspace 固定挂载到 xiaopow/data/workspace（docker-compose
    决定，与测试 tmp_path 无关）。因此文件断言检查沙盒宿主路径，而非 memory_client
    的 workspace_dir。
    """

    async def test_tc2_1_memory_save_writes_to_workspace(
        self, memory_client: TestClient
    ):
        """TC-2.1：告诉助手一个偏好，要求记住，检查沙盒 workspace 文件是否更新。

        由于 LLM 会对内容进行语义重写，我们无法断言精确字符串，
        而是检查：① 回复确认已记忆；② 某个 workspace 文件被修改。

        注：测试前清除 user.md，防止 memory-save 的准入控制因"内容重复"跳过写入。
        """
        # 清除 user.md 确保准入控制不因重复内容跳过写入
        user_md = _SANDBOX_WORKSPACE / "user.md"
        if user_md.exists():
            user_md.unlink()

        # 以所有候选 md 文件为快照基准
        candidate_files = ["user.md", "memory.md", "agent.md"]
        snapshots_before = {
            f: (_SANDBOX_WORKSPACE / f).read_text(encoding="utf-8")
            if (_SANDBOX_WORKSPACE / f).exists() else ""
            for f in candidate_files
        }

        data = await send_message(
            memory_client,
            "帮我记住：我坚持测试驱动开发原则，每次写代码都先写测试再写实现",
            "p2p:ou_memory_save_test",
        )
        reply = data["reply"]

        # 等待文件写入（Sandbox 内写文件有延迟）
        await asyncio.sleep(5)

        snapshots_after = {
            f: (_SANDBOX_WORKSPACE / f).read_text(encoding="utf-8")
            if (_SANDBOX_WORKSPACE / f).exists() else ""
            for f in candidate_files
        }

        assert any(
            kw in reply for kw in ("记住", "保存", "已记录", "好的", "了解")
        ), f"回复应确认已记忆，实际：{reply!r}"

        changed = [f for f in candidate_files if snapshots_after[f] != snapshots_before[f]]
        assert changed, (
            f"memory-save 应写入至少一个 workspace 文件，但 {candidate_files} 均未改变。\n"
            f"reply: {reply!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 场景4（需 sandbox）：TC-4.2 搜索历史对话
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.sandbox
@pytest.mark.integration
class TestSearchMemorySkill:
    """TC-4.2：pgvector 中有历史数据，search_memory Skill 能语义搜索命中。"""

    async def test_tc4_2_semantic_search_finds_history(
        self, memory_client: TestClient, pgvector_dsn: str
    ):
        """TC-4.2：直接插入历史记忆，通过 search_memory Skill 搜索命中。"""
        import asyncpg
        from evopaw.memory.indexer import embed_texts

        rk_history  = "p2p:ou_search_history"
        rk_searcher = "p2p:ou_search_test"

        # 插入一条"两周前的"历史记忆
        texts = ["用户询问了 REDIS_HASH_MARKER 缓存方案，结论是用 Hash 类型存用户会话"]
        vecs = embed_texts(texts)
        vec_str = "[" + ",".join(str(v) for v in vecs[0]) + "]"

        import hashlib

        row_id = hashlib.sha256(f"search-test-{int(time.time())}".encode()).hexdigest()[:64]
        conn = await asyncpg.connect(pgvector_dsn)
        try:
            await conn.execute(f"""
                INSERT INTO memories
                    (id, session_id, routing_key, turn_ts,
                     user_message, assistant_reply, summary, tags,
                     summary_vec, message_vec, search_text)
                VALUES (
                    '{row_id}',
                    'history-session-search-test',
                    '{rk_history}',
                    {int(time.time() * 1000)},
                    '怎么用 Redis 存用户会话？',
                    '建议用 Hash 类型，因为可以只更新单个字段',
                    $1,
                    ARRAY['redis','cache'],
                    '{vec_str}'::vector,
                    '{vec_str}'::vector,
                    'Redis 用户会话 Hash 缓存'
                )
                ON CONFLICT (id) DO NOTHING
            """, texts[0])
        finally:
            await conn.close()

        # 在新 session 中搜索
        data = await send_message(
            memory_client,
            "帮我搜索一下，我之前有没有问过 Redis 缓存相关的问题？",
            rk_searcher,
        )
        reply = data["reply"]

        assert "REDIS_HASH_MARKER" in reply or "Hash" in reply or "redis" in reply.lower(), (
            f"search_memory 应搜索到历史记录，实际：{reply!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 场景3（需 sandbox）：TC-3.1 skill-creator 创建新 Skill
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.sandbox
@pytest.mark.integration
class TestSkillCreator:
    """TC-3.1：用户描述 SOP，助手通过 skill-creator 固化为 SKILL.md。"""

    # AIO-Sandbox 固定挂载 ./evopaw/skills 为 /mnt/skills（sandbox-docker-compose.yaml）
    _SANDBOX_SKILLS = Path(__file__).parents[2] / "evopaw" / "skills"

    async def test_tc3_1_skill_creator_generates_skill_file(
        self, memory_client: TestClient
    ):
        """TC-3.1：发送 SOP 描述，验证 sandbox skills 目录下生成了新的 SKILL.md。

        注：skill-creator Skill 在 sandbox 内运行，写入 /mnt/skills/，对应宿主机
        /root/course/code/xiaopow/evopaw/skills/（docker-compose 固定挂载）。
        """
        import shutil

        skill_name = "e2e-test-report"
        skill_dir  = self._SANDBOX_SKILLS / skill_name

        # 清理可能的残留
        if skill_dir.exists():
            shutil.rmtree(skill_dir)

        data = await send_message(
            memory_client,
            f"""帮我创建一个新 Skill，名字叫 {skill_name}
功能：读取 /workspace/test-results.txt，统计通过/失败数量，
生成测试报告存到 /workspace/reports/test_report.md
输入：测试结果文件路径（可选，默认 /workspace/test-results.txt）
输出：报告文件路径""",
            "p2p:ou_skill_creator_test",
        )
        reply = data["reply"]

        # 等待 sandbox 内文件写入
        await asyncio.sleep(5)

        # skill-creator 可能写到 /mnt/skills/<name>/SKILL.md（首选）或
        # /workspace/sessions/.../tmp/<name>/SKILL.md（临时草稿目录）
        # 搜索两个位置，找到任意一个即通过
        preferred = skill_dir / "SKILL.md"
        tmp_matches = list(_SANDBOX_WORKSPACE.rglob(f"*{skill_name}*/SKILL.md"))
        skill_md = preferred if preferred.exists() else (tmp_matches[0] if tmp_matches else None)

        assert skill_md is not None and skill_md.exists(), (
            f"SKILL.md 应在 {skill_dir} 或 sandbox workspace 中被创建，回复：{reply!r}"
        )
        content = skill_md.read_text(encoding="utf-8")
        assert skill_name in content, f"SKILL.md 应包含 skill name：{skill_name}"


# ─────────────────────────────────────────────────────────────────────────────
# 场景5（需 sandbox）：TC-5.1 memory-governance 死链检测
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.llm
@pytest.mark.sandbox
@pytest.mark.integration
class TestMemoryGovernance:
    """TC-5.1：memory.md 有死链，governance Skill 能发现并报告。"""

    async def test_tc5_1_governance_detects_dead_links(
        self, memory_client: TestClient
    ):
        """TC-5.1：memory.md 引用不存在的文件，governance 报告死链。"""
        ws: Path = memory_client._workspace_dir
        memory_md = ws / "memory.md"

        # 写入含死链的 memory.md
        memory_md.write_text(
            """# EvoPaw 记忆索引

## 用户偏好
→ 详见：[topics/DEADLINK_PREF.md](./topics/DEADLINK_PREF.md)

## 项目状态
→ 详见：[topics/DEADLINK_PROJ.md](./topics/DEADLINK_PROJ.md)
""",
            encoding="utf-8",
        )
        # topics/ 目录不存在 → 死链

        data = await send_message(
            memory_client,
            "帮我检查一下记忆文件的健康状况，看看有没有失效的链接",
            "p2p:ou_governance_test",
        )
        reply = data["reply"]

        # 应报告死链（文件不存在）
        dead_link_keywords = [
            "DEADLINK_PREF", "DEADLINK_PROJ",
            "不存在", "dead", "找不到", "失效",
        ]
        assert any(kw in reply for kw in dead_link_keywords), (
            f"governance 应报告死链，实际：{reply!r}"
        )
