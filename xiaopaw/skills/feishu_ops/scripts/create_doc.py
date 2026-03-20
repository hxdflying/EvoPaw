"""创建飞书文档（Docx），可选写入 Markdown 内容。

用法：
    python create_doc.py --title "季度报告"
    python create_doc.py --title "季度报告" --content "# 一季度\n\n正文内容..."
    python create_doc.py --title "季度报告" --content_file /workspace/report.md

支持的 Markdown 语法（--content / --content_file）：
    # 标题1  ## 标题2  ### 标题3  #### 标题4  ##### 标题5  ###### 标题6
    - 项目  * 项目       无序列表
    1. 项目  2. 项目      有序列表
    ``` ... ```           代码块（三反引号包裹）
    普通段落文字          文本段落
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import requests  # noqa: E402
from _feishu_auth import check_feishu_resp, get_headers, output_error, output_ok  # noqa: E402

_BASE = "https://open.feishu.cn/open-apis"

# 飞书 block_type 常量
_BT_TEXT = 2
_BT_H1 = 3
_BT_H2 = 4
_BT_H3 = 5
_BT_H4 = 6
_BT_H5 = 7
_BT_H6 = 8
_BT_BULLET = 12
_BT_ORDERED = 13
_BT_CODE = 14

# 标题级别 → block_type 映射
_HEADING_BT = {1: _BT_H1, 2: _BT_H2, 3: _BT_H3, 4: _BT_H4, 5: _BT_H5, 6: _BT_H6}
# 标题级别 → block 字段名映射
_HEADING_FIELD = {
    1: "heading1", 2: "heading2", 3: "heading3",
    4: "heading4", 5: "heading5", 6: "heading6",
}


def _text_block(content: str, block_type: int, field: str) -> dict:
    """构造文本类 block（text / heading / bullet / ordered）。"""
    return {
        "block_type": block_type,
        field: {
            "elements": [{"text_run": {"content": content}}],
        },
    }


def _code_block(content: str) -> dict:
    """构造代码块 block（block_type=14，语言默认 PlainText=1）。"""
    return {
        "block_type": _BT_CODE,
        "code": {
            "elements": [{"text_run": {"content": content}}],
            "style": {"language": 1},
        },
    }


def _md_to_blocks(md: str) -> list[dict]:
    """将 Markdown 字符串转换为飞书 docx block 列表。

    逐行解析：
      - # ~ ######     → heading1~heading6
      - - / * / +      → bullet（无序列表）
      - 1. 2. 数字+点  → ordered（有序列表）
      - ``` ... ```    → code（代码块，支持多行）
      - 空行           → 跳过
      - 其他           → text（普通段落）
    """
    blocks: list[dict] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # ── 代码块 ────────────────────────────────────────────
        if line.strip().startswith("```"):
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            code_content = "\n".join(code_lines)
            if code_content.strip():
                blocks.append(_code_block(code_content))
            i += 1
            continue

        # ── 标题 ──────────────────────────────────────────────
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            content = m.group(2).strip()
            if content:
                bt = _HEADING_BT[level]
                field = _HEADING_FIELD[level]
                blocks.append(_text_block(content, bt, field))
            i += 1
            continue

        # ── 无序列表 ──────────────────────────────────────────
        m = re.match(r"^[\s]*[-*+]\s+(.*)", line)
        if m:
            content = m.group(1).strip()
            if content:
                blocks.append(_text_block(content, _BT_BULLET, "bullet"))
            i += 1
            continue

        # ── 有序列表 ──────────────────────────────────────────
        m = re.match(r"^[\s]*\d+\.\s+(.*)", line)
        if m:
            content = m.group(1).strip()
            if content:
                blocks.append(_text_block(content, _BT_ORDERED, "ordered"))
            i += 1
            continue

        # ── 空行 ──────────────────────────────────────────────
        if not line.strip():
            i += 1
            continue

        # ── 普通段落 ──────────────────────────────────────────
        blocks.append(_text_block(line.strip(), _BT_TEXT, "text"))
        i += 1

    return blocks


def _write_blocks(document_id: str, blocks: list[dict]) -> None:
    """将 blocks 分批追加到文档末尾（每批≤50，绕过 API 单次限制）。"""
    batch_size = 50
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i:i + batch_size]
        resp = requests.post(
            f"{_BASE}/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            headers=get_headers(),
            json={"children": batch, "index": i},
            timeout=30,
        )
        data = resp.json()
        check_feishu_resp(data, "写入文档内容失败，请确认 document_id 正确且应用有编辑权限")


def _get_real_url(document_id: str) -> str:
    """通过 drive meta 接口获取文档的真实用户端 URL（含租户域名）。"""
    resp = requests.post(
        f"{_BASE}/drive/v1/metas/batch_query",
        headers=get_headers(),
        json={"request_docs": [{"doc_token": document_id, "doc_type": "docx"}], "with_url": True},
        timeout=10,
    )
    try:
        url = resp.json()["data"]["metas"][0]["url"]
        if url:
            return url
    except (KeyError, IndexError):
        pass
    return f"https://open.feishu.cn/docx/{document_id}"  # fallback


def _set_tenant_readable(document_id: str) -> None:
    """设置文档为组织内成员持链接可阅读（drive/v2 接口）。"""
    requests.patch(
        f"{_BASE}/drive/v2/permissions/{document_id}/public",
        params={"type": "docx"},
        headers=get_headers(),
        json={"link_share_entity": "tenant_readable"},
        timeout=10,
    )  # 权限设置失败不阻断主流程，静默忽略


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True, help="文档标题")
    parser.add_argument(
        "--folder_token",
        default="",
        help="存放文档的云空间文件夹 token（留空则放在应用根目录）",
    )
    parser.add_argument(
        "--content",
        default="",
        help="Markdown 格式的文档内容字符串（可选）",
    )
    parser.add_argument(
        "--content_file",
        default="",
        help="Markdown 内容文件路径（与 --content 二选一，文件优先）",
    )
    args = parser.parse_args()

    # 解析内容来源
    md_content = ""
    if args.content_file:
        try:
            md_content = Path(args.content_file).read_text(encoding="utf-8")
        except OSError as e:
            output_error(f"读取内容文件失败：{e}", "请确认 --content_file 路径正确")
    elif args.content:
        md_content = args.content

    # 1. 创建文档
    body: dict = {"title": args.title}
    if args.folder_token:
        body["folder_token"] = args.folder_token

    resp = requests.post(
        f"{_BASE}/docx/v1/documents",
        headers=get_headers(),
        json=body,
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(data, "请确认 folder_token 正确，且应用已有该文件夹的编辑权限")

    doc = data["data"]["document"]
    document_id = doc.get("document_id", "")

    # 2. 设置组织内持链接可阅读
    _set_tenant_readable(document_id)

    # 3. 获取真实用户端 URL（含租户域名）
    url = _get_real_url(document_id)

    # 4. 写入内容（可选）
    blocks_written = 0
    if md_content.strip():
        blocks = _md_to_blocks(md_content)
        if blocks:
            _write_blocks(document_id, blocks)
            blocks_written = len(blocks)

    output_ok({
        "document_id": document_id,
        "url": url,
        "title": args.title,
        "blocks_written": blocks_written,
    })


if __name__ == "__main__":
    main()
