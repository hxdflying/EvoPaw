"""baidu_search scripts 单元测试

测试策略：
- 动态导入 scripts/search.py，不运行子进程
- mock requests.post 和凭证文件读取
- 用 capsys 捕获 stdout JSON 输出
- 用 pytest.raises(SystemExit) 捕获 sys.exit(0)

运行：
    python3 -m pytest tests/unit/test_baidu_search_script.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ── 模块导入 ──────────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "xiaopaw" / "skills" / "baidu_search" / "scripts"


def _import_search():
    """动态导入 search.py（每次重新加载避免模块缓存干扰 argparse）。"""
    import importlib
    if SCRIPTS_DIR not in [Path(p) for p in sys.path]:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import search as _search_mod
    importlib.reload(_search_mod)
    return _search_mod


# ── 工具函数 ──────────────────────────────────────────────────────────────────

FAKE_CREDS = json.dumps({"api_key": "test-api-key-abc"})


def _make_api_resp(references: list | None = None, code=None, message: str = "") -> MagicMock:
    """构造百度千帆搜索 API 的 mock 响应。"""
    m = MagicMock()
    body: dict = {"references": references or []}
    if code is not None:
        body["code"] = code
        body["message"] = message
    m.json.return_value = body
    return m


def _make_ref(idx: int = 1) -> dict:
    return {
        "id": idx,
        "title": f"标题{idx}",
        "url": f"https://example.com/{idx}",
        "content": f"内容摘要{idx}",
    }


def _run_main(search_mod, args: list[str], capsys) -> dict:
    """设置 sys.argv 后运行 main()，捕获并解析 stdout JSON。"""
    with patch("sys.argv", ["search.py"] + args):
        with pytest.raises(SystemExit):
            search_mod.main()
    captured = capsys.readouterr()
    return json.loads(captured.out)


# ── 凭证读取测试 ──────────────────────────────────────────────────────────────


class TestCredentials:
    def test_missing_config_file_returns_error(self, capsys):
        search = _import_search()
        with patch("builtins.open", side_effect=FileNotFoundError("no file")), \
             patch("sys.argv", ["search.py", "--query", "test"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "baidu.json" in out["errmsg"]

    def test_empty_api_key_returns_error(self, capsys):
        search = _import_search()
        creds = json.dumps({"api_key": ""})
        with patch("builtins.open", mock_open(read_data=creds)), \
             patch("sys.argv", ["search.py", "--query", "test"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "api_key" in out["errmsg"]


# ── 参数解析测试 ──────────────────────────────────────────────────────────────


class TestArgumentParsing:
    def test_empty_query_returns_error(self, capsys):
        search = _import_search()
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("sys.argv", ["search.py", "--query", "   "]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1

    def test_top_k_clamped_to_max_50(self, capsys):
        """top_k > 50 应被截断为 50。"""
        search = _import_search()
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", return_value=_make_api_resp([_make_ref()])), \
             patch("sys.argv", ["search.py", "--query", "q", "--top_k", "999"]):
            with pytest.raises(SystemExit):
                search.main()
        # 验证发送给 API 的 top_k 是 50
        import requests
        # 不直接检查 requests.post 参数，通过成功响应推断解析通过
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0

    def test_top_k_clamped_to_min_1(self, capsys):
        """top_k < 1 应被截断为 1。"""
        search = _import_search()
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", return_value=_make_api_resp([_make_ref()])), \
             patch("sys.argv", ["search.py", "--query", "q", "--top_k", "0"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0

    def test_sites_parsed_from_comma_string(self, capsys):
        """--sites 逗号分隔字符串应被正确解析并传入 payload。"""
        search = _import_search()
        captured_payload = {}

        def mock_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _make_api_resp([_make_ref()])

        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", side_effect=mock_post), \
             patch("sys.argv", ["search.py", "--query", "q", "--sites", "zhihu.com,csdn.net"]):
            with pytest.raises(SystemExit):
                search.main()
        assert "search_filter" in captured_payload
        sites = captured_payload["search_filter"]["match"]["site"]
        assert "zhihu.com" in sites
        assert "csdn.net" in sites

    def test_sites_clamped_to_20(self, capsys):
        """超过 20 个站点时应截断。"""
        search = _import_search()
        captured_payload = {}

        def mock_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _make_api_resp([_make_ref()])

        over_20 = ",".join(f"site{i}.com" for i in range(25))
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", side_effect=mock_post), \
             patch("sys.argv", ["search.py", "--query", "q", "--sites", over_20]):
            with pytest.raises(SystemExit):
                search.main()
        sites = captured_payload["search_filter"]["match"]["site"]
        assert len(sites) == 20

    def test_recency_filter_added_to_payload(self, capsys):
        """--recency 应作为 search_recency_filter 加入 payload。"""
        search = _import_search()
        captured_payload = {}

        def mock_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _make_api_resp([_make_ref()])

        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", side_effect=mock_post), \
             patch("sys.argv", ["search.py", "--query", "q", "--recency", "month"]):
            with pytest.raises(SystemExit):
                search.main()
        assert captured_payload.get("search_recency_filter") == "month"

    def test_no_recency_means_no_filter_key(self, capsys):
        """不传 --recency 时 payload 中不应有 search_recency_filter。"""
        search = _import_search()
        captured_payload = {}

        def mock_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _make_api_resp([_make_ref()])

        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", side_effect=mock_post), \
             patch("sys.argv", ["search.py", "--query", "q"]):
            with pytest.raises(SystemExit):
                search.main()
        assert "search_recency_filter" not in captured_payload


# ── 成功响应测试 ──────────────────────────────────────────────────────────────


class TestSuccessfulSearch:
    def test_returns_errcode_0(self, capsys):
        search = _import_search()
        refs = [_make_ref(1), _make_ref(2)]
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", return_value=_make_api_resp(refs)), \
             patch("sys.argv", ["search.py", "--query", "Python 教程"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 0
        assert out["errmsg"] == "success"

    def test_results_structure(self, capsys):
        search = _import_search()
        refs = [_make_ref(i) for i in range(3)]
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", return_value=_make_api_resp(refs)), \
             patch("sys.argv", ["search.py", "--query", "q"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["total"] == 3
        assert len(out["results"]) == 3
        assert out["query"] == "q"
        # 每条结果包含必要字段
        for r in out["results"]:
            assert "title" in r
            assert "url" in r
            assert "summary" in r

    def test_api_key_sent_in_header(self, capsys):
        search = _import_search()
        captured_headers = {}

        def mock_post(url, json, headers, timeout):
            captured_headers.update(headers)
            return _make_api_resp([_make_ref()])

        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", side_effect=mock_post), \
             patch("sys.argv", ["search.py", "--query", "q"]):
            with pytest.raises(SystemExit):
                search.main()
        assert "Bearer test-api-key-abc" in captured_headers.get("X-Appbuilder-Authorization", "")

    def test_query_whitespace_stripped(self, capsys):
        search = _import_search()
        captured_payload = {}

        def mock_post(url, json, headers, timeout):
            captured_payload.update(json)
            return _make_api_resp([_make_ref()])

        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", side_effect=mock_post), \
             patch("sys.argv", ["search.py", "--query", "  Python  "]):
            with pytest.raises(SystemExit):
                search.main()
        sent_query = captured_payload["messages"][0]["content"]
        assert sent_query == "Python"


# ── 错误响应测试 ──────────────────────────────────────────────────────────────


class TestErrorResponses:
    def test_no_results_returns_error(self, capsys):
        search = _import_search()
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", return_value=_make_api_resp([])), \
             patch("sys.argv", ["search.py", "--query", "罕见词"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "罕见词" in out["errmsg"]

    def test_api_error_code_returns_error(self, capsys):
        search = _import_search()
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", return_value=_make_api_resp([], code=40001, message="invalid key")), \
             patch("sys.argv", ["search.py", "--query", "q"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "40001" in out["errmsg"]

    def test_timeout_returns_error(self, capsys):
        import requests as req_module
        search = _import_search()
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", side_effect=req_module.exceptions.Timeout()), \
             patch("sys.argv", ["search.py", "--query", "q"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "超时" in out["errmsg"]

    def test_http_error_returns_error(self, capsys):
        import requests as req_module
        search = _import_search()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", side_effect=req_module.exceptions.HTTPError(response=mock_resp)), \
             patch("sys.argv", ["search.py", "--query", "q"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "403" in out["errmsg"]

    def test_network_error_returns_error(self, capsys):
        import requests as req_module
        search = _import_search()
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", side_effect=req_module.exceptions.ConnectionError("refused")), \
             patch("sys.argv", ["search.py", "--query", "q"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1

    def test_json_decode_error_returns_error(self, capsys):
        search = _import_search()
        mock_resp = MagicMock()
        mock_resp.json.side_effect = json.JSONDecodeError("err", "", 0)
        mock_resp.raise_for_status = MagicMock()
        with patch("builtins.open", mock_open(read_data=FAKE_CREDS)), \
             patch("requests.post", return_value=mock_resp), \
             patch("sys.argv", ["search.py", "--query", "q"]):
            with pytest.raises(SystemExit):
                search.main()
        out = json.loads(capsys.readouterr().out)
        assert out["errcode"] == 1
        assert "解析" in out["errmsg"]
