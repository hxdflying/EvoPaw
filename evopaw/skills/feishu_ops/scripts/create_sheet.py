"""创建飞书电子表格（Spreadsheet）。

用法：
    python create_sheet.py --title "销售数据" [--folder_token <token>]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import requests  # noqa: E402
from _feishu_auth import check_feishu_resp, get_headers, output_ok  # noqa: E402

_BASE = "https://open.feishu.cn/open-apis"


def _set_tenant_readable(token: str) -> None:
    """设置表格为组织内成员持链接可阅读。"""
    requests.patch(
        f"{_BASE}/drive/v2/permissions/{token}/public",
        params={"type": "sheet"},
        headers=get_headers(),
        json={"link_share_entity": "tenant_readable"},
        timeout=10,
    )


def _get_real_url(token: str) -> str:
    """通过 drive meta 接口获取表格的真实用户端 URL（含租户域名）。"""
    resp = requests.post(
        f"{_BASE}/drive/v1/metas/batch_query",
        headers=get_headers(),
        json={"request_docs": [{"doc_token": token, "doc_type": "sheet"}], "with_url": True},
        timeout=10,
    )
    try:
        url = resp.json()["data"]["metas"][0]["url"]
        if url:
            return url
    except (KeyError, IndexError):
        pass
    return f"https://open.feishu.cn/sheets/{token}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True, help="电子表格标题")
    parser.add_argument(
        "--folder_token",
        default="",
        help="存放表格的云空间文件夹 token（留空则放在 我的云空间 根目录）",
    )
    args = parser.parse_args()

    body: dict = {"title": args.title}
    if args.folder_token:
        body["folder_token"] = args.folder_token

    resp = requests.post(
        f"{_BASE}/sheets/v3/spreadsheets",
        headers=get_headers(),
        json=body,
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(data, "请确认 folder_token 正确，且应用已有该文件夹的编辑权限")

    sheet_info = data["data"]["spreadsheet"]
    token = sheet_info.get("spreadsheet_token", "")

    _set_tenant_readable(token)
    url = _get_real_url(token)

    output_ok({"spreadsheet_token": token, "url": url, "title": args.title})


if __name__ == "__main__":
    main()
