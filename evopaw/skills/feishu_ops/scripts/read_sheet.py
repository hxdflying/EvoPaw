"""读取飞书电子表格数据。

用法：
    python read_sheet.py --sheet "https://xxx.feishu.cn/sheets/shtcnXXXXXX" --sheet_id Sheet1
    python read_sheet.py --sheet shtcnXXXXXX --sheet_id Sheet1 --range A1:D10
    python read_sheet.py --sheet shtcnXXXXXX  # 不指定 sheet_id，读第一个 Sheet
"""

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import _feishu_auth as auth


def get_first_sheet_id(spreadsheet_token: str, headers: dict) -> str:
    """获取表格中第一个 Sheet 的 sheetId。"""
    resp = requests.get(
        f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query",
        headers=headers,
        timeout=15,
    )
    data = resp.json()
    auth.check_feishu_resp(data, hint="确认 spreadsheet_token 正确，且应用有读取权限")
    sheets = data.get("data", {}).get("sheets", [])
    if not sheets:
        auth.output_error("电子表格中没有任何 Sheet")
    return sheets[0]["sheet_id"]


def main() -> None:
    parser = argparse.ArgumentParser(description="读取飞书电子表格数据")
    parser.add_argument("--sheet", required=True,
                        help="飞书电子表格 URL 或 spreadsheet_token")
    parser.add_argument("--sheet_id", default="",
                        help="Sheet 标识（sheetId，不是 Sheet 名称），不填则读取第一个 Sheet")
    parser.add_argument("--range", default="",
                        help="读取范围，如 A1:D10，不填则读整个 Sheet")
    args = parser.parse_args()

    spreadsheet_token = auth.parse_sheet_token(args.sheet)
    headers = auth.get_headers()

    sheet_id = args.sheet_id or get_first_sheet_id(spreadsheet_token, headers)
    range_spec = f"{sheet_id}!{args.range}" if args.range else sheet_id

    resp = requests.get(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{range_spec}",
        headers=headers,
        timeout=30,
    )
    data = resp.json()
    auth.check_feishu_resp(
        data,
        hint="确认 spreadsheet_token 和 sheet_id 正确，且应用有电子表格读取权限",
    )
    values = data.get("data", {}).get("valueRange", {}).get("values", [])
    auth.output_ok({
        "spreadsheet_token": spreadsheet_token,
        "sheet_id": sheet_id,
        "range": args.range or "全表",
        "row_count": len(values),
        "values": values,
    })


if __name__ == "__main__":
    main()
