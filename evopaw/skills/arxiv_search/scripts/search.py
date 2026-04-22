"""arXiv 论文搜索脚本 — arxiv_search Skill 的执行入口。

用法：
    python search.py search --query "关键词" [--max_results 10] [--sort_by relevance]
    python search.py get --arxiv_id "2301.07041"
    python search.py pdf --arxiv_id "2301.07041" --output_dir /path/to/outputs

输出：JSON 到 stdout，errcode=0 成功，errcode=1 失败。
"""

import argparse
import json
import sys
import time


# ───────────────────── 输出规范 ─────────────────────────────

def _exit_ok(data: dict) -> None:
    print(json.dumps({"errcode": 0, "errmsg": "success", **data}, ensure_ascii=False))
    sys.exit(0)


def _exit_error(errmsg: str, hint: str = "") -> None:
    msg = errmsg + (f"\n建议：{hint}" if hint else "")
    print(json.dumps({"errcode": 1, "errmsg": msg}, ensure_ascii=False))
    sys.exit(0)


def _ensure_arxiv():
    try:
        import arxiv  # noqa: F811, PLC0415
        return arxiv
    except ImportError:
        _exit_error("arxiv 库未安装。", "请执行 pip install arxiv 后重试。")


def _paper_to_dict(paper) -> dict:
    return {
        "arxiv_id": paper.entry_id.split("/")[-1],
        "title": paper.title,
        "authors": [a.name for a in paper.authors],
        "summary": paper.summary,
        "published": paper.published.strftime("%Y-%m-%d") if paper.published else "",
        "updated": paper.updated.strftime("%Y-%m-%d") if paper.updated else "",
        "pdf_url": paper.pdf_url or "",
        "categories": list(paper.categories),
    }


# ───────────────────── search 命令 ────────────────────────────

def cmd_search(args) -> None:
    arxiv = _ensure_arxiv()

    query = args.query.strip()
    if not query:
        _exit_error("query 不能为空。", "请提供有效的搜索关键词。")

    max_results = max(1, min(50, args.max_results))

    sort_map = {
        "relevance": arxiv.SortCriterion.Relevance,
        "lastUpdatedDate": arxiv.SortCriterion.LastUpdatedDate,
        "submittedDate": arxiv.SortCriterion.SubmittedDate,
    }
    sort_criterion = sort_map.get(args.sort_by, arxiv.SortCriterion.Relevance)

    try:
        client = arxiv.Client(delay_seconds=3.0)
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=sort_criterion,
        )
        papers = list(client.results(search))
    except Exception as e:  # noqa: BLE001
        _exit_error(f"arXiv API 调用失败：{e}", "检查网络连接或稍后重试。")

    if not papers:
        _exit_error(
            f"未找到与「{query}」相关的论文。",
            "尝试更通用的关键词或英文搜索。",
        )

    _exit_ok({
        "query": query,
        "total": len(papers),
        "papers": [_paper_to_dict(p) for p in papers],
    })


# ───────────────────── get 命令 ───────────────────────────────

def cmd_get(args) -> None:
    arxiv = _ensure_arxiv()

    arxiv_id = args.arxiv_id.strip()
    if not arxiv_id:
        _exit_error("arxiv_id 不能为空。")

    try:
        client = arxiv.Client(delay_seconds=3.0)
        search = arxiv.Search(id_list=[arxiv_id])
        papers = list(client.results(search))
    except Exception as e:  # noqa: BLE001
        _exit_error(f"arXiv API 调用失败：{e}", "检查 arXiv ID 格式或网络连接。")

    if not papers:
        _exit_error(f"未找到 arXiv ID「{arxiv_id}」对应的论文。", "请检查 ID 是否正确。")

    _exit_ok({
        "total": len(papers),
        "papers": [_paper_to_dict(p) for p in papers],
    })


# ───────────────────── pdf 命令 ───────────────────────────────

def cmd_pdf(args) -> None:
    arxiv = _ensure_arxiv()

    arxiv_id = args.arxiv_id.strip()
    if not arxiv_id:
        _exit_error("arxiv_id 不能为空。")

    output_dir = args.output_dir
    if not output_dir:
        _exit_error("output_dir 不能为空。")

    import os
    os.makedirs(output_dir, exist_ok=True)

    # 获取论文信息
    try:
        client = arxiv.Client(delay_seconds=3.0)
        search = arxiv.Search(id_list=[arxiv_id])
        papers = list(client.results(search))
    except Exception as e:  # noqa: BLE001
        _exit_error(f"arXiv API 调用失败：{e}")

    if not papers:
        _exit_error(f"未找到 arXiv ID「{arxiv_id}」对应的论文。")

    paper = papers[0]

    # 下载 PDF
    pdf_filename = f"{arxiv_id.replace('/', '_')}.pdf"
    pdf_path = os.path.join(output_dir, pdf_filename)

    try:
        paper.download_pdf(dirpath=output_dir, filename=pdf_filename)
    except Exception as e:  # noqa: BLE001
        _exit_error(f"PDF 下载失败：{e}", "检查网络连接或稍后重试。")

    # 提取文本
    text_preview = ""
    try:
        from PyPDF2 import PdfReader  # noqa: PLC0415
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages[:10]:  # 最多读 10 页
            text_parts.append(page.extract_text() or "")
        full_text = "\n".join(text_parts)
        text_preview = full_text[:2000]
    except ImportError:
        text_preview = "(PyPDF2 未安装，无法提取文本。请执行 pip install PyPDF2)"
    except Exception as e:  # noqa: BLE001
        text_preview = f"(文本提取失败：{e})"

    _exit_ok({
        "arxiv_id": arxiv_id,
        "title": paper.title,
        "pdf_path": pdf_path,
        "text_preview": text_preview,
    })


# ───────────────────── CLI 入口 ───────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="arXiv 论文搜索与下载")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search
    sp_search = subparsers.add_parser("search", help="按关键词搜索论文")
    sp_search.add_argument("--query", required=True, help="搜索关键词")
    sp_search.add_argument("--max_results", type=int, default=10, help="返回结果数")
    sp_search.add_argument(
        "--sort_by",
        choices=["relevance", "lastUpdatedDate", "submittedDate"],
        default="relevance",
    )

    # get
    sp_get = subparsers.add_parser("get", help="按 arXiv ID 获取论文信息")
    sp_get.add_argument("--arxiv_id", required=True, help="arXiv 论文 ID")

    # pdf
    sp_pdf = subparsers.add_parser("pdf", help="下载 PDF 并提取文本")
    sp_pdf.add_argument("--arxiv_id", required=True, help="arXiv 论文 ID")
    sp_pdf.add_argument("--output_dir", required=True, help="PDF 保存目录")

    args = parser.parse_args()

    cmd_map = {"search": cmd_search, "get": cmd_get, "pdf": cmd_pdf}
    cmd_map[args.command](args)


if __name__ == "__main__":
    main()
