"""feishu_ops scripts 单元测试

测试策略：
- 通过 sys.path 注入导入 scripts 目录下的模块（不运行子进程）
- 用 unittest.mock 模拟 requests 调用和凭证文件
- 用 capsys 捕获 stdout JSON 输出
- 用 pytest.raises(SystemExit) 捕获 sys.exit(0)

运行：
    python3 -m pytest tests/unit/test_feishu_ops_scripts.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ── scripts 目录注入 ──────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "xiaopaw" / "skills" / "feishu_ops" / "scripts"


def _import_script(name: str):
    """动态导入 feishu_ops scripts 目录下的模块。"""
    if SCRIPTS_DIR not in [Path(p) for p in sys.path]:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import importlib
    return importlib.import_module(name)


# ── 测试工具 ──────────────────────────────────────────────────────────────────

def _make_token_resp(token: str = "t-test-token") -> MagicMock:
    """构造飞书 tenant_access_token 接口的 mock 响应。"""
    m = MagicMock()
    m.json.return_value = {"code": 0, "tenant_access_token": token}
    return m


def _make_api_resp(code: int = 0, data: dict | None = None) -> MagicMock:
    """构造飞书 REST API 的 mock 响应。"""
    m = MagicMock()
    m.json.return_value = {"code": code, "data": data or {}}
    return m


FAKE_CREDS = json.dumps({"app_id": "cli_test", "app_secret": "secret_test"})


# ─────────────────────────────────────────────────────────────────────────────
# _feishu_auth 模块测试
# ─────────────────────────────────────────────────────────────────────────────


class TestFeishuAuth:
    """测试 _feishu_auth.py 的工具函数。"""

    def setup_method(self):
        self.auth = _import_script("_feishu_auth")

    def test_parse_routing_key_p2p(self):
        rtype, rid = self.auth.parse_routing_key("p2p:ou_abc123")
        assert rtype == "open_id"
        assert rid == "ou_abc123"

    def test_parse_routing_key_group(self):
        rtype, rid = self.auth.parse_routing_key("group:oc_xyz456")
        assert rtype == "chat_id"
        assert rid == "oc_xyz456"

    def test_parse_routing_key_bare_ou(self):
        rtype, rid = self.auth.parse_routing_key("ou_direct")
        assert rtype == "open_id"
        assert rid == "ou_direct"

    def test_parse_routing_key_bare_oc(self):
        rtype, rid = self.auth.parse_routing_key("oc_direct")
        assert rtype == "chat_id"
        assert rid == "oc_direct"

    def test_parse_doc_token_from_url(self):
        url = "https://company.feishu.cn/docx/doccnABCDE12345"
        assert self.auth.parse_doc_token(url) == "doccnABCDE12345"

    def test_parse_doc_token_from_legacy_url(self):
        url = "https://company.feishu.cn/docs/doccnABCDE12345"
        assert self.auth.parse_doc_token(url) == "doccnABCDE12345"

    def test_parse_doc_token_bare(self):
        assert self.auth.parse_doc_token("doccnABCDE12345") == "doccnABCDE12345"

    def test_parse_sheet_token_from_url(self):
        url = "https://company.feishu.cn/sheets/shtcnXXXXXX"
        assert self.auth.parse_sheet_token(url) == "shtcnXXXXXX"

    def test_parse_sheet_token_bare(self):
        assert self.auth.parse_sheet_token("shtcnXXXXXX") == "shtcnXXXXXX"

    def test_output_ok_prints_json(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self.auth.output_ok({"message_id": "om_xxx"})
        assert exc.value.code == 0
        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["errcode"] == 0
        assert result["data"]["message_id"] == "om_xxx"

    def test_output_error_prints_json(self, capsys):
        with pytest.raises(SystemExit) as exc:
            self.auth.output_error("API 调用失败", hint="检查 routing_key")
        assert exc.value.code == 0
        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["errcode"] == 1
        assert "API 调用失败" in result["errmsg"]
        assert "检查 routing_key" in result["errmsg"]

    def test_get_headers_calls_token_api(self):
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
            with patch("requests.post", return_value=_make_token_resp("t-abc")) as mock_post:
                headers = self.auth.get_headers()
        mock_post.assert_called_once()
        assert headers["Authorization"] == "Bearer t-abc"
        assert headers["Content-Type"] == "application/json"

    def test_get_headers_exits_on_auth_failure(self, capsys):
        fail_resp = MagicMock()
        fail_resp.json.return_value = {"code": 99991663, "msg": "token expired"}
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
            with patch("requests.post", return_value=fail_resp):
                with pytest.raises(SystemExit):
                    self.auth.get_headers()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "99991663" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# send_text.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestSendText:
    def setup_method(self):
        self.mod = _import_script("send_text")

    def _run(self, argv: list[str], api_resp: MagicMock) -> dict:
        """运行 main() 并返回 stdout JSON。"""
        with patch("sys.argv", ["send_text.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]) as _mock:
                    with pytest.raises(SystemExit):
                        self.mod.main()
        import io, contextlib
        return _mock

    def test_sends_to_p2p(self, capsys):
        api_resp = _make_api_resp(data={"message_id": "om_p2p_001"})
        with patch("sys.argv", ["send_text.py", "--routing_key", "p2p:ou_user1", "--text", "Hello"]):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["message_id"] == "om_p2p_001"

    def test_sends_to_group(self, capsys):
        api_resp = _make_api_resp(data={"message_id": "om_grp_001"})
        with patch("sys.argv", ["send_text.py", "--routing_key", "group:oc_chat1", "--text", "群组消息"]):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0

    def test_api_error_returns_errcode_1(self, capsys):
        api_resp = _make_api_resp(code=230002)
        api_resp.json.return_value = {"code": 230002, "msg": "invalid receive_id"}
        with patch("sys.argv", ["send_text.py", "--routing_key", "p2p:ou_bad", "--text", "Hi"]):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "230002" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# send_post.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestSendPost:
    def setup_method(self):
        self.mod = _import_script("send_post")

    def test_sends_post_message(self, capsys):
        api_resp = _make_api_resp(data={"message_id": "om_post_001"})
        argv = [
            "--routing_key", "p2p:ou_user1",
            "--title", "测试标题",
            "--paragraphs", '["第一段", "第二段"]',
        ]
        with patch("sys.argv", ["send_post.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["message_id"] == "om_post_001"

    def test_invalid_paragraphs_json(self, capsys):
        argv = [
            "--routing_key", "p2p:ou_user1",
            "--paragraphs", "not-json",
        ]
        with patch("sys.argv", ["send_post.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "JSON" in out["errmsg"]

    def test_parse_paragraph_with_link(self):
        mod = _import_script("send_post")
        elements = mod._parse_paragraph("查看[飞书文档](https://example.com)了解详情")
        tags = [e["tag"] for e in elements]
        assert "a" in tags
        assert "text" in tags
        # 链接元素包含 href
        link_elem = next(e for e in elements if e["tag"] == "a")
        assert link_elem["href"] == "https://example.com"


# ─────────────────────────────────────────────────────────────────────────────
# send_image.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestSendImage:
    def setup_method(self):
        self.mod = _import_script("send_image")

    def test_uploads_and_sends_image(self, capsys, tmp_path):
        # 创建假图片文件
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        upload_resp = _make_api_resp(data={"image_key": "img_test_001"})
        send_resp = _make_api_resp(data={"message_id": "om_img_001"})

        argv = ["--routing_key", "p2p:ou_user1", "--image_path", str(img_file)]
        with patch("sys.argv", ["send_image.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                # 模拟：1次 token 请求，1次上传，1次发送
                with patch("requests.post", side_effect=[_make_token_resp(), upload_resp, send_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["image_key"] == "img_test_001"
        assert out["data"]["message_id"] == "om_img_001"

    def test_missing_image_file(self, capsys):
        argv = ["--routing_key", "p2p:ou_user1", "--image_path", "/nonexistent/path/img.png"]
        with patch("sys.argv", ["send_image.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "不存在" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# send_file.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestSendFile:
    def setup_method(self):
        self.mod = _import_script("send_file")

    def test_file_type_detection(self):
        mod = _import_script("send_file")
        assert mod._feishu_file_type(Path("report.pdf")) == "pdf"
        assert mod._feishu_file_type(Path("data.xlsx")) == "xls"
        assert mod._feishu_file_type(Path("slides.pptx")) == "ppt"
        assert mod._feishu_file_type(Path("video.mp4")) == "mp4"
        assert mod._feishu_file_type(Path("unknown.zip")) == "stream"

    def test_uploads_and_sends_file(self, capsys, tmp_path):
        pdf_file = tmp_path / "report.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake content")

        upload_resp = _make_api_resp(data={"file_key": "file_test_001"})
        send_resp = _make_api_resp(data={"message_id": "om_file_001"})

        argv = ["--routing_key", "p2p:ou_user1", "--file_path", str(pdf_file)]
        with patch("sys.argv", ["send_file.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), upload_resp, send_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["file_key"] == "file_test_001"
        assert out["data"]["file_name"] == "report.pdf"
        assert out["data"]["file_type"] == "pdf"

    def test_missing_file(self, capsys):
        argv = ["--routing_key", "p2p:ou_user1", "--file_path", "/nonexistent/file.pdf"]
        with patch("sys.argv", ["send_file.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "不存在" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# read_doc.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestReadDoc:
    def setup_method(self):
        self.mod = _import_script("read_doc")

    def test_reads_doc_by_token(self, capsys):
        api_resp = _make_api_resp(data={"content": "文档正文内容"})
        argv = ["--doc", "doccnABCDE12345"]
        with patch("sys.argv", ["read_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", return_value=api_resp):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["content"] == "文档正文内容"
        assert out["data"]["doc_token"] == "doccnABCDE12345"

    def test_reads_doc_by_url(self, capsys):
        api_resp = _make_api_resp(data={"content": "URL 文档内容"})
        url = "https://company.feishu.cn/docx/doccnURLTOKEN"
        argv = ["--doc", url]
        with patch("sys.argv", ["read_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", return_value=api_resp) as mock_get:
                        with pytest.raises(SystemExit):
                            self.mod.main()
        # 确认 URL 中的 token 被正确提取
        call_url = mock_get.call_args[0][0]
        assert "doccnURLTOKEN" in call_url
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# read_sheet.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestReadSheet:
    def setup_method(self):
        self.mod = _import_script("read_sheet")

    def test_reads_sheet_with_range(self, capsys):
        data_rows = [["月份", "销售额"], ["1月", 10000], ["2月", 15000]]
        api_resp = _make_api_resp(data={"valueRange": {"values": data_rows}})
        argv = ["--sheet", "shtcnXXXXXX", "--sheet_id", "abc123", "--range", "A1:B3"]
        with patch("sys.argv", ["read_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", return_value=api_resp):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["row_count"] == 3
        assert out["data"]["values"][0] == ["月份", "销售额"]

    def test_auto_detects_first_sheet(self, capsys):
        sheets_resp = _make_api_resp(data={"sheets": [{"sheet_id": "auto_sheet"}]})
        data_resp = _make_api_resp(data={"valueRange": {"values": [["A", "B"]]}})
        argv = ["--sheet", "shtcnXXXXXX"]
        with patch("sys.argv", ["read_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", side_effect=[sheets_resp, data_resp]):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["sheet_id"] == "auto_sheet"


# ─────────────────────────────────────────────────────────────────────────────
# get_chat_members.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestGetChatMembers:
    def setup_method(self):
        self.mod = _import_script("get_chat_members")

    def test_returns_member_list(self, capsys):
        members = [{"open_id": "ou_aaa", "name": "张三"}, {"open_id": "ou_bbb", "name": "李四"}]
        api_resp = _make_api_resp(data={"items": members, "has_more": False})
        argv = ["--chat_id", "oc_test_chat"]
        with patch("sys.argv", ["get_chat_members.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", return_value=api_resp):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["member_count"] == 2
        assert out["data"]["members"][0]["open_id"] == "ou_aaa"

    def test_handles_pagination(self, capsys):
        page1 = _make_api_resp(data={
            "items": [{"open_id": "ou_aaa"}],
            "has_more": True,
            "page_token": "pt_001",
        })
        page2 = _make_api_resp(data={
            "items": [{"open_id": "ou_bbb"}],
            "has_more": False,
        })
        argv = ["--chat_id", "oc_test_chat"]
        with patch("sys.argv", ["get_chat_members.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", side_effect=[page1, page2]):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["data"]["member_count"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# parse_bitable_token 测试（_feishu_auth 模块扩展）
# ─────────────────────────────────────────────────────────────────────────────


class TestParseBitableToken:
    """测试 _feishu_auth.parse_bitable_token。"""

    def setup_method(self):
        self.auth = _import_script("_feishu_auth")

    def test_parse_from_url(self):
        url = "https://company.feishu.cn/base/BitableABC123"
        assert self.auth.parse_bitable_token(url) == "BitableABC123"

    def test_parse_from_url_with_query_params(self):
        url = "https://company.feishu.cn/base/BitableABC123?table=tblXXXX&view=vewYYYY"
        assert self.auth.parse_bitable_token(url) == "BitableABC123"

    def test_parse_bare_token(self):
        assert self.auth.parse_bitable_token("BitableABC123") == "BitableABC123"


# ─────────────────────────────────────────────────────────────────────────────
# create_doc.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateDoc:
    def setup_method(self):
        self.mod = _import_script("create_doc")

    def test_creates_doc_with_title(self, capsys):
        api_resp = _make_api_resp(data={"document": {"document_id": "doc_test_001"}})
        argv = ["--title", "季度报告"]
        with patch("sys.argv", ["create_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), api_resp,
                    _make_token_resp(), _make_token_resp(), _make_api_resp(),
                ]):
                    with patch("requests.patch"):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["document_id"] == "doc_test_001"
        assert out["data"]["title"] == "季度报告"
        assert "doc_test_001" in out["data"]["url"]
        assert out["data"]["blocks_written"] == 0

    def test_creates_doc_with_folder_token(self, capsys):
        api_resp = _make_api_resp(data={"document": {"document_id": "doc_folder_001"}})
        argv = ["--title", "项目文档", "--folder_token", "fldcnXXXXXX"]
        with patch("sys.argv", ["create_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), api_resp,
                    _make_token_resp(), _make_token_resp(), _make_api_resp(),
                ]) as mock_post:
                    with patch("requests.patch"):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        # 确认 folder_token 包含在创建请求体中
        create_call = mock_post.call_args_list[1]
        assert create_call.kwargs["json"]["folder_token"] == "fldcnXXXXXX"
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0

    def test_api_error_returns_errcode_1(self, capsys):
        api_resp = MagicMock()
        api_resp.json.return_value = {"code": 99991400, "msg": "permission denied"}
        argv = ["--title", "失败文档"]
        with patch("sys.argv", ["create_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "99991400" in out["errmsg"]

    def test_creates_doc_with_markdown_content(self, capsys):
        """带 --content 时额外调用 blocks 写入接口（再多 1 次 POST）。"""
        create_resp = _make_api_resp(data={"document": {"document_id": "doc_md_001"}})
        blocks_resp = _make_api_resp(data={"children": []})
        md = "# 标题\n\n正文段落\n\n- 列表项A\n- 列表项B"
        argv = ["--title", "MD文档", "--content", md]
        with patch("sys.argv", ["create_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                # POST 顺序：token(create) + create_doc + token(perm) + token(url) + meta + token(write) + write_blocks
                with patch("requests.post", side_effect=[
                    _make_token_resp(), create_resp,
                    _make_token_resp(), _make_token_resp(), _make_api_resp(),
                    _make_token_resp(), blocks_resp,
                ]):
                    with patch("requests.patch"):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["document_id"] == "doc_md_001"
        assert out["data"]["blocks_written"] > 0

    def test_md_to_blocks_all_types(self):
        """_md_to_blocks 应正确解析标题/无序/有序/代码块/段落。"""
        md = (
            "# 一级标题\n"
            "## 二级标题\n"
            "### 三级标题\n"
            "- 无序项目\n"
            "* 无序项目2\n"
            "1. 有序项目\n"
            "2. 有序项目2\n"
            "```\ncode line\n```\n"
            "普通段落"
        )
        blocks = self.mod._md_to_blocks(md)
        btypes = [b["block_type"] for b in blocks]
        # 3=h1, 4=h2, 5=h3, 12=bullet×2, 13=ordered×2, 14=code, 2=text
        assert 3 in btypes    # heading1
        assert 4 in btypes    # heading2
        assert 5 in btypes    # heading3
        assert 12 in btypes   # bullet
        assert 13 in btypes   # ordered
        assert 14 in btypes   # code
        assert 2 in btypes    # text paragraph

    def test_md_to_blocks_skips_empty_lines(self):
        """空行不应产生 block。"""
        md = "\n\n\n# 仅标题\n\n\n"
        blocks = self.mod._md_to_blocks(md)
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 3

    def test_creates_doc_with_content_file(self, capsys, tmp_path):
        """--content_file 应从文件读取 Markdown 内容。"""
        md_file = tmp_path / "report.md"
        md_file.write_text("# 报告标题\n\n正文内容", encoding="utf-8")
        create_resp = _make_api_resp(data={"document": {"document_id": "doc_file_001"}})
        blocks_resp = _make_api_resp(data={"children": []})
        argv = ["--title", "文件文档", "--content_file", str(md_file)]
        with patch("sys.argv", ["create_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), create_resp,
                    _make_token_resp(), _make_token_resp(), _make_api_resp(),
                    _make_token_resp(), blocks_resp,
                ]):
                    with patch("requests.patch"):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["blocks_written"] > 0

    def test_content_file_not_found_returns_error(self, capsys):
        """--content_file 不存在应返回 errcode=1。"""
        create_resp = _make_api_resp(data={"document": {"document_id": "doc_err_001"}})
        argv = ["--title", "文件不存在", "--content_file", "/nonexistent/report.md"]
        with patch("sys.argv", ["create_doc.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), create_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "读取内容文件失败" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# create_sheet.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateSheet:
    def setup_method(self):
        self.mod = _import_script("create_sheet")

    def test_creates_sheet_with_title(self, capsys):
        api_resp = _make_api_resp(data={
            "spreadsheet": {
                "spreadsheet_token": "sht_test_001",
                "url": "https://xxx.feishu.cn/sheets/sht_test_001",
            }
        })
        argv = ["--title", "销售数据"]
        with patch("sys.argv", ["create_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), api_resp,
                    _make_token_resp(), _make_token_resp(), _make_api_resp(),
                ]):
                    with patch("requests.patch"):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["spreadsheet_token"] == "sht_test_001"
        assert out["data"]["title"] == "销售数据"
        assert "sht_test_001" in out["data"]["url"]

    def test_creates_sheet_with_folder_token(self, capsys):
        api_resp = _make_api_resp(data={
            "spreadsheet": {"spreadsheet_token": "sht_folder_001", "url": "https://xxx.feishu.cn"}
        })
        argv = ["--title", "周报", "--folder_token", "fldcnYYYYYY"]
        with patch("sys.argv", ["create_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), api_resp,
                    _make_token_resp(), _make_token_resp(), _make_api_resp(),
                ]) as mock_post:
                    with patch("requests.patch"):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        create_call = mock_post.call_args_list[1]
        assert create_call.kwargs["json"]["folder_token"] == "fldcnYYYYYY"
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0

    def test_api_error_returns_errcode_1(self, capsys):
        api_resp = MagicMock()
        api_resp.json.return_value = {"code": 99991400, "msg": "no permission"}
        argv = ["--title", "失败表格"]
        with patch("sys.argv", ["create_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# write_sheet.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestWriteSheet:
    def setup_method(self):
        self.mod = _import_script("write_sheet")

    def test_col_letter_basic(self):
        assert self.mod._col_letter(1) == "A"
        assert self.mod._col_letter(26) == "Z"
        assert self.mod._col_letter(27) == "AA"
        assert self.mod._col_letter(28) == "AB"

    def test_end_cell_calculation(self):
        # A1 + 3行4列 → D3
        assert self.mod._end_cell("A1", 3, 4) == "D3"
        # B2 + 2行2列 → C3
        assert self.mod._end_cell("B2", 2, 2) == "C3"

    def test_writes_with_explicit_sheet_id(self, capsys):
        values = [["姓名", "年龄"], ["Alice", 30], ["Bob", 25]]
        write_resp = MagicMock()
        write_resp.json.return_value = {"code": 0, "data": {"revision": 1}}
        argv = [
            "--sheet", "sht_test_001",
            "--values", json.dumps(values),
            "--sheet_id", "sh001",
        ]
        with patch("sys.argv", ["write_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.put", return_value=write_resp):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["rows_written"] == 3
        assert out["data"]["cols_written"] == 2
        assert out["data"]["sheet_id"] == "sh001"

    def test_writes_auto_fetch_first_sheet(self, capsys):
        values = [["A", "B"], ["1", "2"]]
        sheets_resp = _make_api_resp(data={"sheets": [{"sheet_id": "auto_sh001"}]})
        write_resp = MagicMock()
        write_resp.json.return_value = {"code": 0, "data": {"revision": 1}}
        argv = ["--sheet", "sht_test_001", "--values", json.dumps(values)]
        with patch("sys.argv", ["write_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with patch("requests.get", return_value=sheets_resp):
                        with patch("requests.put", return_value=write_resp):
                            with pytest.raises(SystemExit):
                                self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["sheet_id"] == "auto_sh001"

    def test_invalid_values_json(self, capsys):
        argv = ["--sheet", "sht_test_001", "--values", "not-json", "--sheet_id", "sh001"]
        with patch("sys.argv", ["write_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "JSON" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# create_bitable.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateBitable:
    def setup_method(self):
        self.mod = _import_script("create_bitable")

    def test_creates_bitable(self, capsys):
        api_resp = _make_api_resp(data={
            "app": {"app_token": "bitable_001", "url": "https://xxx.feishu.cn/base/bitable_001"}
        })
        argv = ["--name", "项目管理"]
        with patch("sys.argv", ["create_bitable.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), api_resp,
                    _make_token_resp(), _make_token_resp(), _make_api_resp(),
                ]):
                    with patch("requests.patch"):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["app_token"] == "bitable_001"
        assert out["data"]["name"] == "项目管理"
        assert "bitable_001" in out["data"]["url"]

    def test_creates_bitable_with_folder_token(self, capsys):
        api_resp = _make_api_resp(data={
            "app": {"app_token": "bitable_002", "url": "https://xxx.feishu.cn/base/bitable_002"}
        })
        argv = ["--name", "销售数据", "--folder_token", "fldcnZZZZZZ"]
        with patch("sys.argv", ["create_bitable.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), api_resp,
                    _make_token_resp(), _make_token_resp(), _make_api_resp(),
                ]) as mock_post:
                    with patch("requests.patch"):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        create_call = mock_post.call_args_list[1]
        assert create_call.kwargs["json"]["folder_token"] == "fldcnZZZZZZ"
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0

    def test_api_error_returns_errcode_1(self, capsys):
        api_resp = MagicMock()
        api_resp.json.return_value = {"code": 99991400, "msg": "no permission"}
        argv = ["--name", "失败多维表格"]
        with patch("sys.argv", ["create_bitable.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), api_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# create_bitable_table.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateBitableTable:
    def setup_method(self):
        self.mod = _import_script("create_bitable_table")

    def test_build_field_body_text(self):
        body = self.mod._build_field_body({"name": "任务名称", "type": "text"})
        assert body["field_name"] == "任务名称"
        assert body["type"] == 1  # text → 1
        assert "property" not in body

    def test_build_field_body_select_with_options(self):
        body = self.mod._build_field_body({
            "name": "优先级",
            "type": "select",
            "options": ["高", "中", "低"],
        })
        assert body["field_name"] == "优先级"
        assert body["type"] == 3  # select → 3
        assert body["property"]["options"] == [{"name": "高"}, {"name": "中"}, {"name": "低"}]

    def test_build_field_body_unsupported_type(self, capsys):
        with pytest.raises(SystemExit):
            self.mod._build_field_body({"name": "未知字段", "type": "unsupported_xyz"})
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "unsupported_xyz" in out["errmsg"]

    def test_creates_table_with_fields(self, capsys):
        # _create_table: 1 token POST + 1 tables POST
        # _create_field(×1): 1 token POST + 1 fields POST  → 4 POSTs total
        table_resp = _make_api_resp(data={"table_id": "tbl_test_001"})
        field_resp = _make_api_resp(data={"field": {"field_id": "fld_text_001"}})
        fields_json = json.dumps([{"name": "任务名称", "type": "text"}])
        argv = ["--app", "bitable_token_001", "--name", "任务清单", "--fields", fields_json]
        with patch("sys.argv", ["create_bitable_table.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), table_resp, _make_token_resp(), field_resp
                ]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["table_id"] == "tbl_test_001"
        assert out["data"]["table_name"] == "任务清单"
        assert len(out["data"]["fields_created"]) == 1
        assert out["data"]["fields_created"][0]["name"] == "任务名称"

    def test_creates_table_without_fields(self, capsys):
        # 不传 --fields，默认空列表，只有 _create_table 的 2 个 POST
        table_resp = _make_api_resp(data={"table_id": "tbl_empty_001"})
        argv = ["--app", "bitable_token_001", "--name", "空数据表"]
        with patch("sys.argv", ["create_bitable_table.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[_make_token_resp(), table_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["table_id"] == "tbl_empty_001"
        assert out["data"]["fields_created"] == []


# ─────────────────────────────────────────────────────────────────────────────
# write_bitable_records.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestWriteBitableRecords:
    def setup_method(self):
        self.mod = _import_script("write_bitable_records")

    def test_writes_records(self, capsys):
        records = [
            {"任务名称": "完成 API 文档", "优先级": "高"},
            {"任务名称": "代码 Review", "优先级": "中"},
        ]
        batch_resp = _make_api_resp(data={
            "records": [{"record_id": "rec_001"}, {"record_id": "rec_002"}]
        })
        argv = [
            "--app", "bitable_token_001",
            "--table_id", "tbl_test_001",
            "--records", json.dumps(records),
        ]
        with patch("sys.argv", ["write_bitable_records.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                # _batch_create: 1 token POST + 1 batch_create POST
                with patch("requests.post", side_effect=[_make_token_resp(), batch_resp]):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["record_count"] == 2
        assert "rec_001" in out["data"]["record_ids"]
        assert "rec_002" in out["data"]["record_ids"]

    def test_batch_splits_large_records(self, capsys):
        """验证超过 _BATCH_SIZE 时自动分批写入。"""
        records = [{"名称": f"任务{i}"} for i in range(3)]
        batch1_resp = _make_api_resp(data={
            "records": [{"record_id": "rec_001"}, {"record_id": "rec_002"}]
        })
        batch2_resp = _make_api_resp(data={"records": [{"record_id": "rec_003"}]})
        argv = [
            "--app", "bitable_token_001",
            "--table_id", "tbl_test_001",
            "--records", json.dumps(records),
        ]
        with patch("sys.argv", ["write_bitable_records.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                # 将 _BATCH_SIZE 缩小为 2 触发分批（3条 → 2批）
                with patch.object(self.mod, "_BATCH_SIZE", 2):
                    # 批1: token + batch_create；批2: token + batch_create → 4 POSTs
                    with patch("requests.post", side_effect=[
                        _make_token_resp(), batch1_resp, _make_token_resp(), batch2_resp
                    ]) as mock_post:
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["record_count"] == 3
        assert mock_post.call_count == 4  # 2 token + 2 batch_create

    def test_invalid_records_json(self, capsys):
        argv = [
            "--app", "bitable_token_001",
            "--table_id", "tbl_test_001",
            "--records", "not-json",
        ]
        with patch("sys.argv", ["write_bitable_records.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "JSON" in out["errmsg"]

    def test_empty_records_returns_error(self, capsys):
        argv = [
            "--app", "bitable_token_001",
            "--table_id", "tbl_test_001",
            "--records", "[]",
        ]
        with patch("sys.argv", ["write_bitable_records.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "空" in out["errmsg"]


# ─────────────────────────────────────────────────────────────────────────────
# upload_sheet.py 测试
# ─────────────────────────────────────────────────────────────────────────────


class TestUploadSheet:
    """测试 upload_sheet.py：本地 xlsx 文件导入为飞书电子表格。"""

    def setup_method(self):
        self.mod = _import_script("upload_sheet")

    def _make_upload_resp(self, file_token: str = "ftoken_001") -> MagicMock:
        """构造 upload_all 接口响应。"""
        m = MagicMock()
        m.json.return_value = {"code": 0, "data": {"file_token": file_token}}
        return m

    def _make_import_task_resp(self, ticket: str = "ticket_001") -> MagicMock:
        """构造 import_tasks 创建响应。"""
        m = MagicMock()
        m.json.return_value = {"code": 0, "data": {"ticket": ticket}}
        return m

    def _make_poll_resp(
        self,
        status: int = 0,
        token: str = "sht_imported_001",
        url: str = "https://xxx.feishu.cn/sheets/sht_imported_001",
    ) -> MagicMock:
        """构造 import_tasks/{ticket} GET 轮询响应。"""
        m = MagicMock()
        m.json.return_value = {
            "code": 0,
            "data": {
                "result": {
                    "job_status": status,
                    "token": token,
                    "url": url,
                }
            },
        }
        return m

    def test_uploads_and_imports_xlsx(self, capsys, tmp_path):
        """全链路：上传 xlsx → 创建导入任务 → 轮询成功。"""
        xlsx = tmp_path / "data.xlsx"
        xlsx.write_bytes(b"PK\x03\x04")  # 最小伪 xlsx 文件头

        argv = ["--file_path", str(xlsx)]
        with patch("sys.argv", ["upload_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                # POST 顺序：token(upload) + upload_all + token(import_task) + import_task
                #           + token(poll) + token(set_perm) + token(get_url) + meta_batch_query
                with patch("requests.post", side_effect=[
                    _make_token_resp(),           # upload_all: get_auth_header → token
                    self._make_upload_resp(),      # upload_all: actual response
                    _make_token_resp(),            # import_tasks: get_headers → token
                    self._make_import_task_resp(), # import_tasks: actual response
                    _make_token_resp(),            # poll: get_headers → token
                    _make_token_resp(),            # _set_tenant_readable: token
                    _make_token_resp(),            # _get_real_url: token
                    _make_api_resp(),              # _get_real_url: meta batch_query response
                ]):
                    with patch("requests.get", return_value=self._make_poll_resp()):
                        with patch("requests.patch"):
                            with pytest.raises(SystemExit):
                                self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["spreadsheet_token"] == "sht_imported_001"
        assert "sht_imported_001" in out["data"]["url"]
        assert out["data"]["file_size"] > 0

    def test_uses_title_as_file_name(self, capsys, tmp_path):
        """--title 指定时，导入任务中文件名应使用该 title。"""
        xlsx = tmp_path / "raw.xlsx"
        xlsx.write_bytes(b"PK\x03\x04")

        argv = ["--file_path", str(xlsx), "--title", "销售报告"]
        with patch("sys.argv", ["upload_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), self._make_upload_resp(),
                    _make_token_resp(), self._make_import_task_resp(),
                    _make_token_resp(),            # poll token
                    _make_token_resp(),            # _set_tenant_readable: token
                    _make_token_resp(),            # _get_real_url: token
                    _make_api_resp(),              # _get_real_url: meta batch_query response
                ]) as mock_post:
                    with patch("requests.get", return_value=self._make_poll_resp()):
                        with patch("requests.patch"):
                            with pytest.raises(SystemExit):
                                self.mod.main()
        # import_tasks 请求（第4次 POST）的 json 应含正确 file_name
        import_call = mock_post.call_args_list[3]
        assert import_call.kwargs["json"]["file_name"] == "销售报告.xlsx"
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["data"]["title"] == "销售报告.xlsx"

    def test_unsupported_file_format_returns_error(self, capsys, tmp_path):
        """非 xlsx/xls 文件应返回 errcode=1。"""
        csv = tmp_path / "data.csv"
        csv.write_text("a,b,c\n1,2,3")

        argv = ["--file_path", str(csv)]
        with patch("sys.argv", ["upload_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert ".csv" in out["errmsg"]

    def test_nonexistent_file_returns_error(self, capsys):
        """不存在的文件路径应返回 errcode=1。"""
        argv = ["--file_path", "/nonexistent/data.xlsx"]
        with patch("sys.argv", ["upload_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", return_value=_make_token_resp()):
                    with pytest.raises(SystemExit):
                        self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "不存在" in out["errmsg"]

    def test_import_task_failure_returns_error(self, capsys, tmp_path):
        """导入任务 job_status=3（失败）时应返回 errcode=1。"""
        xlsx = tmp_path / "bad.xlsx"
        xlsx.write_bytes(b"PK\x03\x04")

        fail_poll = MagicMock()
        fail_poll.json.return_value = {
            "code": 0,
            "data": {
                "result": {
                    "job_status": 3,
                    "job_error_msg": "文件格式损坏",
                    "token": "",
                    "url": "",
                }
            },
        }
        argv = ["--file_path", str(xlsx)]
        with patch("sys.argv", ["upload_sheet.py"] + argv):
            with patch("builtins.open", mock_open(read_data=FAKE_CREDS)):
                with patch("requests.post", side_effect=[
                    _make_token_resp(), self._make_upload_resp(),
                    _make_token_resp(), self._make_import_task_resp(),
                    _make_token_resp(),  # poll token
                ]):
                    with patch("requests.get", return_value=fail_poll):
                        with pytest.raises(SystemExit):
                            self.mod.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "导入任务失败" in out["errmsg"]
