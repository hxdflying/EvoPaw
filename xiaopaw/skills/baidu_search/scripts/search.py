"""百度千帆搜索脚本 — baidu_search Skill 的沙盒执行入口。

凭证读取自沙盒路径 /workspace/.config/baidu.json，不暴露给 LLM。

用法：
    python search.py --query "搜索词" [--top_k 20] [--recency week] [--sites "a.com,b.com"]

输出：JSON 到 stdout，errcode=0 成功，errcode=1 失败。
"""

import argparse
import json
import sys

import requests


# ───────────────────── 凭证读取 ─────────────────────────────

def _get_api_key() -> str:
    try:
        with open("/workspace/.config/baidu.json") as f:
            creds = json.load(f)
    except FileNotFoundError:
        _exit_error("凭证文件不存在：/workspace/.config/baidu.json，请联系管理员检查服务启动配置。")
    api_key = creds.get("api_key", "")
    if not api_key:
        _exit_error("baidu.json 中 api_key 为空，请联系管理员检查 BAIDU_API_KEY 环境变量。")
    return api_key


# ───────────────────── 输出规范 ─────────────────────────────

def _exit_ok(data: dict) -> None:
    print(json.dumps({"errcode": 0, "errmsg": "success", **data}, ensure_ascii=False))
    sys.exit(0)


def _exit_error(errmsg: str, hint: str = "") -> None:
    msg = errmsg + (f"\n建议：{hint}" if hint else "")
    print(json.dumps({"errcode": 1, "errmsg": msg}, ensure_ascii=False))
    sys.exit(0)


# ───────────────────── 主逻辑 ────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="百度千帆搜索")
    parser.add_argument("--query", required=True, help="搜索关键词或问题")
    parser.add_argument("--top_k", type=int, default=20, help="返回结果数，默认20，最大50")
    parser.add_argument(
        "--recency",
        choices=["week", "month", "semiyear", "year"],
        default=None,
        help="时间过滤：week/month/semiyear/year",
    )
    parser.add_argument(
        "--sites",
        default=None,
        help="指定站点，逗号分隔，最多20个，如 zhihu.com,csdn.net",
    )
    args = parser.parse_args()

    query = args.query.strip()
    if not query:
        _exit_error("query 不能为空。", "请提供有效的搜索关键词。")

    top_k = max(1, min(50, args.top_k))

    sites: list[str] | None = None
    if args.sites:
        sites = [s.strip() for s in args.sites.split(",") if s.strip()][:20]

    api_key = _get_api_key()

    payload: dict = {
        "messages": [{"content": query, "role": "user"}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": top_k}],
    }
    if args.recency:
        payload["search_recency_filter"] = args.recency
    if sites:
        payload["search_filter"] = {"match": {"site": sites}}

    url = "https://qianfan.baidubce.com/v2/ai_search/web_search"
    headers = {
        "X-Appbuilder-Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()
    except requests.exceptions.Timeout:
        _exit_error("请求超时（30s）。", "稍后重试，或减少 top_k 加快响应。")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "未知"
        _exit_error(f"HTTP 错误 {status}：{e}", "检查 API Key 是否有效，或稍后重试。")
    except requests.exceptions.RequestException as e:
        _exit_error(f"网络异常：{e}", "检查沙盒网络连通性后重试。")
    except json.JSONDecodeError as e:
        _exit_error(f"响应解析失败：{e}", "稍后重试。")

    error_code = result.get("code")
    if error_code is not None and error_code not in (0, ""):
        error_msg = result.get("message", "未知错误")
        _exit_error(
            f"API 错误 code={error_code}：{error_msg}",
            "检查 query 参数或稍后重试。",
        )

    references = result.get("references", [])
    if not references:
        _exit_error(
            f"未找到与「{query}」相关的搜索结果。",
            "尝试更通用的关键词，或去掉 recency/sites 限制。",
        )

    results = []
    for ref in references:
        results.append({
            "id": ref.get("id"),
            "title": ref.get("title", ""),
            "url": ref.get("url", ""),
            "summary": ref.get("content", ""),
        })

    _exit_ok({
        "query": query,
        "total": len(results),
        "results": results,
    })


if __name__ == "__main__":
    main()
