"""Tavily 搜索脚本 — tavily_search Skill 的执行入口。

凭证读取自 /workspace/.config/tavily.json。

用法：
    python search.py --query "搜索词" [--max_results 10] [--search_depth basic] [--include_answer true]

输出：JSON 到 stdout，errcode=0 成功，errcode=1 失败。
"""

import argparse
import json
import sys


# ───────────────────── 凭证读取 ─────────────────────────────

def _get_api_key() -> str:
    try:
        with open("/workspace/.config/tavily.json") as f:
            creds = json.load(f)
    except FileNotFoundError:
        _exit_error("凭证文件不存在：/workspace/.config/tavily.json，请联系管理员检查服务启动配置。")
    api_key = creds.get("api_key", "")
    if not api_key:
        _exit_error("tavily.json 中 api_key 为空，请联系管理员检查 TAVILY_API_KEY 环境变量。")
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
    parser = argparse.ArgumentParser(description="Tavily 网络搜索")
    parser.add_argument("--query", required=True, help="搜索关键词或问题")
    parser.add_argument("--max_results", type=int, default=10, help="返回结果数，默认10，最大20")
    parser.add_argument(
        "--search_depth",
        choices=["basic", "advanced"],
        default="basic",
        help="搜索深度：basic（快速）/ advanced（深度）",
    )
    parser.add_argument(
        "--include_answer",
        default="true",
        help="是否返回AI摘要：true/false",
    )
    args = parser.parse_args()

    query = args.query.strip()
    if not query:
        _exit_error("query 不能为空。", "请提供有效的搜索关键词。")

    max_results = max(1, min(20, args.max_results))
    include_answer = args.include_answer.lower() in ("true", "1", "yes")

    api_key = _get_api_key()

    try:
        from tavily import TavilyClient  # noqa: PLC0415
    except ImportError:
        _exit_error("tavily-python 未安装。", "请执行 pip install tavily-python 后重试。")

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth=args.search_depth,
            include_answer=include_answer,
        )
    except Exception as e:  # noqa: BLE001
        _exit_error(f"Tavily API 调用失败：{e}", "检查 API Key 是否有效，或稍后重试。")

    results = []
    for item in response.get("results", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "content": item.get("content", ""),
        })

    output = {
        "query": query,
        "total": len(results),
        "results": results,
    }

    answer = response.get("answer")
    if answer:
        output["answer"] = answer

    _exit_ok(output)


if __name__ == "__main__":
    main()
