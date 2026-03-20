"""BaiduSearchTool 单元测试"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from xiaopaw.tools.baidu_search_tool import BaiduSearchInput, BaiduSearchTool


def _mock_search_resp(
    references: list | None = None,
    error_code: int | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    body: dict = {"request_id": "req_test_001"}
    if error_code is not None:
        body["code"] = error_code
        body["message"] = "api error"
    else:
        body["references"] = references if references is not None else []
    resp.json.return_value = body
    return resp


# ── Input validation ───────────────────────────────────────────


class TestBaiduSearchInput:
    def test_valid_query(self):
        inp = BaiduSearchInput(query="hello world")
        assert inp.query == "hello world"

    def test_query_stripped(self):
        inp = BaiduSearchInput(query="  hello  ")
        assert inp.query == "hello"

    def test_empty_query_raises(self):
        with pytest.raises(Exception):
            BaiduSearchInput(query="")

    def test_whitespace_only_query_raises(self):
        with pytest.raises(Exception):
            BaiduSearchInput(query="   ")

    def test_top_k_negative_raises(self):
        with pytest.raises(Exception):
            BaiduSearchInput(query="test", top_k=-1)

    def test_top_k_over_50_raises(self):
        with pytest.raises(Exception):
            BaiduSearchInput(query="test", top_k=51)

    def test_top_k_zero_ok(self):
        inp = BaiduSearchInput(query="test", top_k=0)
        assert inp.top_k == 0

    def test_sites_over_20_raises(self):
        with pytest.raises(Exception):
            BaiduSearchInput(query="test", sites=["s.com"] * 21)

    def test_sites_exactly_20_ok(self):
        inp = BaiduSearchInput(query="test", sites=["s.com"] * 20)
        assert len(inp.sites) == 20

    def test_default_top_k(self):
        inp = BaiduSearchInput(query="test")
        assert inp.top_k == 20


# ── Tool._run() ────────────────────────────────────────────────


class TestBaiduSearchTool:
    def test_missing_api_key_returns_error(self, monkeypatch):
        monkeypatch.delenv("BAIDU_API_KEY", raising=False)
        tool = BaiduSearchTool()
        result = tool._run(query="test")
        assert "BAIDU_API_KEY" in result or "认证" in result

    def test_successful_search_returns_references(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        refs = [
            {"id": 1, "title": "Python Guide", "url": "http://example.com", "content": "A summary"},
            {"id": 2, "title": "Another", "url": "http://other.com", "content": "More text"},
        ]
        with patch("requests.post", return_value=_mock_search_resp(refs)):
            result = BaiduSearchTool()._run(query="Python tutorial")
        assert "Python Guide" in result
        assert "2 条搜索结果" in result

    def test_empty_references_returns_not_found(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        with patch("requests.post", return_value=_mock_search_resp([])):
            result = BaiduSearchTool()._run(query="very obscure query 12345")
        assert "未找到" in result

    def test_api_error_code_returns_error(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        with patch("requests.post", return_value=_mock_search_resp(error_code=40300)):
            result = BaiduSearchTool()._run(query="test")
        assert "错误" in result

    def test_timeout_returns_error(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        with patch("requests.post", side_effect=requests.exceptions.Timeout):
            result = BaiduSearchTool()._run(query="test")
        assert "超时" in result

    def test_http_error_returns_error(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        err_resp = MagicMock()
        err_resp.status_code = 403
        with patch(
            "requests.post",
            side_effect=requests.exceptions.HTTPError(response=err_resp),
        ):
            result = BaiduSearchTool()._run(query="test")
        assert "HTTP" in result

    def test_request_exception_returns_error(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        with patch(
            "requests.post",
            side_effect=requests.exceptions.RequestException("network failure"),
        ):
            result = BaiduSearchTool()._run(query="test")
        assert "网络" in result

    def test_json_decode_error_returns_error(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = json.JSONDecodeError("msg", "", 0)
        with patch("requests.post", return_value=resp):
            result = BaiduSearchTool()._run(query="test")
        assert "解析" in result

    def test_recency_filter_included_in_payload(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        refs = [{"id": 1, "title": "News", "url": "http://news.com", "content": "text"}]
        with patch("requests.post", return_value=_mock_search_resp(refs)) as mock_post:
            BaiduSearchTool()._run(query="latest news", recency_filter="week")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["search_recency_filter"] == "week"

    def test_sites_filter_included_in_payload(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        refs = [{"id": 1, "title": "Site", "url": "http://site.com", "content": "text"}]
        with patch("requests.post", return_value=_mock_search_resp(refs)) as mock_post:
            BaiduSearchTool()._run(query="test", sites=["example.com"])
        payload = mock_post.call_args.kwargs["json"]
        assert "search_filter" in payload

    def test_no_recency_filter_payload_absent(self, monkeypatch):
        monkeypatch.setenv("BAIDU_API_KEY", "test-key")
        refs = [{"id": 1, "title": "T", "url": "http://t.com", "content": "c"}]
        with patch("requests.post", return_value=_mock_search_resp(refs)) as mock_post:
            BaiduSearchTool()._run(query="test")
        payload = mock_post.call_args.kwargs["json"]
        assert "search_recency_filter" not in payload
