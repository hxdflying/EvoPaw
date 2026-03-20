"""集成测试：文件处理全链路 Pipeline

测试场景：
    用户上传文件（CSV）→ TestAPI 复制到 session uploads/
    → Agent 感知文件路径 → 调用 xlsx Skill 处理
    → 输出文件保存到 outputs/ → Agent 回复（含输出路径信息）

子场景：
    P1  附件复制机制（无需 LLM，基础文件 I/O）
    P2  Agent 识别文件路径并意图正确（需要 LLM，不需要 sandbox）
    P3  全链路：上传 CSV → Skill 处理 → 生成输出（需要 LLM + sandbox）
    P4  全链路：上传 CSV → Skill 处理 → feishu_ops 发回（需要 LLM + sandbox + 飞书凭证）

运行：
    # P1 only（最快，无需任何外部服务）
    pytest tests/integration/test_file_pipeline.py -m "not llm" -v

    # P1 + P2（需要 LLM）
    pytest tests/integration/test_file_pipeline.py -m "llm and not sandbox" -v -s

    # P1-P3（需要 LLM + sandbox）
    pytest tests/integration/test_file_pipeline.py -v -s --timeout=180
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from xiaopaw.api.capture_sender import CaptureSender
from xiaopaw.api.test_server import _copy_attachment, create_test_app
from xiaopaw.runner import Runner
from xiaopaw.session.manager import SessionManager
from xiaopaw.session.models import MessageEntry

from .conftest import SANDBOX_URL, send_message


# ─────────────────────────────────────────────────────────────────────────────
# 测试数据
# ─────────────────────────────────────────────────────────────────────────────

# 真实的 CSV 测试数据（月度销售数据）
SALES_CSV = """\
月份,销售额,成本,利润
1月,120000,80000,40000
2月,95000,65000,30000
3月,150000,95000,55000
4月,130000,88000,42000
5月,110000,72000,38000
6月,145000,92000,53000
"""

# 测试用消息模板
_FILE_PROCESS_PROMPT = (
    "请帮我分析这个 CSV 文件，计算每个月的利润率（利润/销售额），"
    "并生成一个汇总报告保存到 outputs/ 目录，文件名为 sales_summary.txt。"
)

_FILE_SEND_PROMPT = (
    "分析完成后，把生成的汇总报告文件通过飞书发给我（routing_key: p2p:ou_test_user）。"
)


# ─────────────────────────────────────────────────────────────────────────────
# P1: 附件复制机制测试（无需 LLM）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestAttachmentCopy:
    """验证 TestAPI 的附件复制机制（_copy_attachment）。"""

    async def test_copies_file_to_uploads_dir(self, tmp_path):
        """文件应被复制到 workspace/sessions/{sid}/uploads/。"""
        src_file = tmp_path / "source.csv"
        src_file.write_text(SALES_CSV, encoding="utf-8")

        workspace_dir = tmp_path / "workspace"
        session_id = "s-test-001"

        content = await _copy_attachment(
            attachment_path=str(src_file),
            file_name=None,
            session_id=session_id,
            workspace_dir=workspace_dir,
            original_text="帮我分析",
        )

        # 验证文件已复制
        dest = workspace_dir / "sessions" / session_id / "uploads" / "source.csv"
        assert dest.exists(), f"期望文件存在：{dest}"
        assert dest.read_text(encoding="utf-8") == SALES_CSV

        # 验证 content 包含沙盒路径提示
        assert "/workspace/sessions/s-test-001/uploads/source.csv" in content
        assert "帮我分析" in content  # original_text 保留

    async def test_uses_custom_file_name(self, tmp_path):
        """attachment.file_name 优先于源文件名。"""
        src_file = tmp_path / "raw_data.tmp"
        src_file.write_text("data", encoding="utf-8")

        workspace_dir = tmp_path / "workspace"
        content = await _copy_attachment(
            attachment_path=str(src_file),
            file_name="sales_data.csv",
            session_id="s-custom",
            workspace_dir=workspace_dir,
            original_text="",
        )

        dest = workspace_dir / "sessions" / "s-custom" / "uploads" / "sales_data.csv"
        assert dest.exists()
        assert "sales_data.csv" in content

    async def test_missing_file_returns_error_hint(self, tmp_path):
        """源文件不存在时，content 返回错误提示而非抛出异常。"""
        workspace_dir = tmp_path / "workspace"
        content = await _copy_attachment(
            attachment_path="/nonexistent/path/file.csv",
            file_name=None,
            session_id="s-err",
            workspace_dir=workspace_dir,
            original_text="",
        )
        assert "不存在" in content

    async def test_no_original_text(self, tmp_path):
        """original_text 为空时，不附加用户备注。"""
        src_file = tmp_path / "file.txt"
        src_file.write_text("hello")
        workspace_dir = tmp_path / "workspace"

        content = await _copy_attachment(
            attachment_path=str(src_file),
            file_name=None,
            session_id="s-notext",
            workspace_dir=workspace_dir,
            original_text="",
        )
        assert "用户备注" not in content

    async def test_via_test_api_attachment_field(
        self, session_mgr: SessionManager, tmp_path: Path
    ):
        """通过 TestAPI 的 attachment 字段发送文件，验证文件被复制且 content 被改写。"""
        src_file = tmp_path / "upload.csv"
        src_file.write_text(SALES_CSV, encoding="utf-8")

        workspace_dir = tmp_path / "workspace"
        captured_content: list[str] = []

        async def capture_agent_fn(
            user_message: str,
            history: list[MessageEntry],
            session_id: str,
            routing_key: str = "",
            root_id: str = "",
            verbose: bool = False,
        ) -> str:
            captured_content.append(user_message)
            return f"收到文件，session={session_id}"

        sender = CaptureSender()
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=capture_agent_fn,
            idle_timeout=5.0,
        )
        app = create_test_app(
            runner=runner,
            sender=sender,
            session_mgr=session_mgr,
            workspace_dir=workspace_dir,
        )

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/api/test/message",
                json={
                    "routing_key": "p2p:ou_test_attach",
                    "content": "帮我处理这个文件",
                    "attachment": {"file_path": str(src_file)},
                },
            )
            assert resp.status == 200
            data = await resp.json()

        await runner.shutdown()

        # content 应被改写为文件路径提示
        assert captured_content, "agent_fn 未被调用"
        assert "/workspace/sessions/" in captured_content[0]
        assert "upload.csv" in captured_content[0]
        assert "帮我处理这个文件" in captured_content[0]

        # 文件应存在于 workspace
        session_id = data["session_id"]
        dest = workspace_dir / "sessions" / session_id / "uploads" / "upload.csv"
        assert dest.exists(), f"期望文件被复制到 {dest}"


# ─────────────────────────────────────────────────────────────────────────────
# P2: Agent 文件意图识别（需要 LLM，不需要 sandbox）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
class TestAgentFileIntentRecognition:
    """Agent 在收到文件路径提示时，应正确识别文件处理意图。"""

    ROUTING_KEY = "p2p:ou_file_intent_test"

    async def test_agent_recognizes_csv_file(self, llm_client: TestClient):
        """告知 Agent CSV 文件路径，Agent 应回复有效的处理意图（不要求实际执行）。"""
        data = await send_message(
            llm_client,
            (
                "用户发来了文件，已自动保存至沙盒路径：\n"
                "`/workspace/sessions/s-test/uploads/sales.csv`\n"
                "请根据文件内容和用户意图完成相应处理。\n"
                "（用户备注：帮我查看这个 CSV 文件的内容）"
            ),
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        # Agent 应该响应——不应该返回空或报错
        assert len(reply) > 20, f"回复过短：{reply!r}"

    async def test_agent_recognizes_process_and_send_intent(self, llm_client: TestClient):
        """Agent 应理解'处理文件并发回给用户'的组合意图。"""
        data = await send_message(
            llm_client,
            (
                "用户发来了文件，已自动保存至沙盒路径：\n"
                "`/workspace/sessions/s-test/uploads/report.pdf`\n"
                "请根据文件内容和用户意图完成相应处理。\n"
                "（用户备注：帮我把 PDF 里的内容提取出来，整理成 Word 文档，然后发回给我）"
            ),
            self.ROUTING_KEY,
        )
        reply = data["reply"]
        assert len(reply) > 20, f"回复过短：{reply!r}"


# ─────────────────────────────────────────────────────────────────────────────
# P3: 全链路文件处理（需要 LLM + sandbox）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.sandbox
class TestFullFilePipeline:
    """
    全链路测试：
    1. 用户上传 CSV 文件（TestAPI attachment 机制）
    2. Agent 收到文件路径提示
    3. Agent 调用 xlsx/csv Skill 处理文件
    4. Skill 生成分析结果，保存到 outputs/

    注意：feishu_ops 发送步骤需要真实飞书凭证，此处只验证到文件处理完成。
    """

    ROUTING_KEY = "p2p:ou_pipeline_test"

    @pytest.fixture
    def workspace_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "workspace"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @pytest.fixture
    async def pipeline_client(
        self,
        session_mgr: SessionManager,
        qwen_api_key: str,
        sandbox_available: bool,
        workspace_dir: Path,
    ) -> TestClient:
        """含 workspace_dir 注入的完整 E2E 客户端。"""
        if not sandbox_available:
            pytest.skip("AIO-Sandbox 不可达，跳过全链路测试")

        from xiaopaw.agents.main_crew import build_agent_fn

        sender = CaptureSender()
        agent_fn = build_agent_fn(
            sender=sender,
            max_history_turns=20,
            sandbox_url=SANDBOX_URL,
        )
        runner = Runner(
            session_mgr=session_mgr,
            sender=sender,
            agent_fn=agent_fn,
            idle_timeout=60.0,
        )
        app = create_test_app(
            runner=runner,
            sender=sender,
            session_mgr=session_mgr,
            workspace_dir=workspace_dir,
        )
        async with TestClient(TestServer(app)) as cli:
            yield cli
        await runner.shutdown()

    async def test_upload_csv_and_process(
        self,
        pipeline_client: TestClient,
        tmp_path: Path,
        workspace_dir: Path,
    ):
        """
        完整流程：
        1. 创建 CSV 文件并通过 TestAPI 上传（attachment 机制）
        2. Agent 收到包含沙盒路径的消息
        3. Agent 调用 Skill 分析 CSV
        4. 验证 Agent 回复包含处理结果说明
        """
        # 1. 创建测试 CSV 文件
        csv_file = tmp_path / "sales_data.csv"
        csv_file.write_text(SALES_CSV, encoding="utf-8")

        # 2. 通过 TestAPI 上传文件（模拟用户发送文件）
        resp = await pipeline_client.post(
            "/api/test/message",
            json={
                "routing_key": self.ROUTING_KEY,
                "content": _FILE_PROCESS_PROMPT,
                "attachment": {"file_path": str(csv_file)},
            },
        )
        assert resp.status == 200, f"TestAPI 请求失败，状态码：{resp.status}"
        data = await resp.json()

        session_id = data["session_id"]
        reply = data["reply"]

        # 3. 验证 Agent 回复表明已处理
        assert len(reply) > 20, f"回复过短：{reply!r}"
        assert any(
            kw in reply
            for kw in ["完成", "分析", "汇总", "利润", "成功", "报告", "保存"]
        ), f"期望回复包含处理结果关键词，实际：{reply!r}"

        # 4. 验证文件被复制到 uploads/（附件复制机制验证）
        uploads_dir = workspace_dir / "sessions" / session_id / "uploads"
        assert (uploads_dir / "sales_data.csv").exists(), (
            f"CSV 文件应被复制到 uploads/ 目录：{uploads_dir}"
        )

    async def test_multi_turn_file_processing(
        self,
        pipeline_client: TestClient,
        tmp_path: Path,
        workspace_dir: Path,
    ):
        """
        多轮对话：
        第1轮：上传文件，要求分析
        第2轮：追问"帮我把结果发给我" — 测试 Agent 是否尝试调用 feishu_ops
        """
        csv_file = tmp_path / "monthly_report.csv"
        csv_file.write_text(SALES_CSV, encoding="utf-8")

        # 第1轮：上传文件
        resp1 = await pipeline_client.post(
            "/api/test/message",
            json={
                "routing_key": self.ROUTING_KEY,
                "content": "帮我分析这份销售数据，告诉我哪个月利润最高。",
                "attachment": {"file_path": str(csv_file)},
            },
        )
        assert resp1.status == 200
        data1 = await resp1.json()
        reply1 = data1["reply"]
        assert len(reply1) > 10, f"第1轮回复过短：{reply1!r}"

        # 第2轮：要求发送结果（会触发 feishu_ops，可能因无凭证失败，但 Agent 应尝试）
        data2 = await send_message(
            pipeline_client,
            "好的，现在把分析结果通过飞书发给 p2p:ou_boss_user。",
            self.ROUTING_KEY,
        )
        reply2 = data2["reply"]
        # Agent 应该有实质性回复（可能是成功，也可能是凭证不足的错误说明）
        assert len(reply2) > 10, f"第2轮回复过短：{reply2!r}"
