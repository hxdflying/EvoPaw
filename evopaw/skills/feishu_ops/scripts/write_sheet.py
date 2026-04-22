"""向飞书电子表格写入数据。

用法：
    python write_sheet.py \
        --sheet "https://xxx.feishu.cn/sheets/shtcnXXXX" \
        --values '[["姓名","年龄","部门"],["Alice",30,"工程"],["Bob",25,"设计"]]' \
        [--start_cell A1] \
        [--sheet_id Sheet1]

说明：
    --values 是 JSON 二维数组，第一行通常是表头。
    数据从 start_cell 开始写入，自动计算覆盖范围。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import requests  # noqa: E402
from _feishu_auth import (  # noqa: E402
    check_feishu_resp,
    get_headers,
    output_error,
    output_ok,
    parse_sheet_token,
)

_BASE = "https://open.feishu.cn/open-apis"


def _col_letter(n: int) -> str:
    """列序号（从 1 开始）转字母，如 1→A, 26→Z, 27→AA。"""
    result = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(ord("A") + r) + result
    return result


def _parse_cell(cell: str) -> tuple[str, int]:
    """将 "A1" 分解为 (列字母, 行号)，如 "B3" → ("B", 3)。"""
    col = "".join(c for c in cell if c.isalpha()).upper()
    row = int("".join(c for c in cell if c.isdigit()))
    return col, row


def _col_index(col_letters: str) -> int:
    """列字母转序号，如 A→1, Z→26, AA→27。"""
    result = 0
    for ch in col_letters.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result


def _end_cell(start: str, rows: int, cols: int) -> str:
    """根据起始单元格和数据维度计算终止单元格。"""
    start_col, start_row = _parse_cell(start)
    end_col = _col_letter(_col_index(start_col) + cols - 1)
    end_row = start_row + rows - 1
    return f"{end_col}{end_row}"


def _get_first_sheet_id(token: str) -> str:
    """获取表格第一个 Sheet 的 sheet_id。"""
    resp = requests.get(
        f"{_BASE}/sheets/v3/spreadsheets/{token}/sheets/query",
        headers=get_headers(),
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(data, "无法获取表格的 Sheet 列表，请确认 spreadsheet_token 正确")
    sheets = data.get("data", {}).get("sheets", [])
    if not sheets:
        output_error("该表格没有任何 Sheet", "请先在表格中创建至少一个 Sheet")
    return sheets[0]["sheet_id"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet", required=True, help="电子表格 URL 或 spreadsheet_token")
    parser.add_argument(
        "--values",
        required=True,
        help='JSON 二维数组，如 \'[["姓名","年龄"],["Alice",30]]\'',
    )
    parser.add_argument(
        "--start_cell",
        default="A1",
        help="写入起始单元格，默认 A1",
    )
    parser.add_argument(
        "--sheet_id",
        default="",
        help="Sheet ID（非 Sheet 名称），不填则写入第一个 Sheet",
    )
    args = parser.parse_args()

    # 解析参数
    token = parse_sheet_token(args.sheet)
    try:
        values: list[list] = json.loads(args.values)
    except json.JSONDecodeError as e:
        output_error(f"--values 不是合法 JSON：{e}", "示例：'[[\"姓名\",\"年龄\"],[\"Alice\",30]]'")

    if not isinstance(values, list) or not values:
        output_error("--values 必须是非空的二维数组")

    sheet_id = args.sheet_id or _get_first_sheet_id(token)
    start = args.start_cell.upper()
    rows = len(values)
    cols = max(len(row) for row in values) if values else 1

    # 飞书 sheets/v2 values 接口每次最多写入 5000 行
    _BATCH_ROWS = 5000
    start_col, start_row = _parse_cell(start)
    rows_written = 0
    for i in range(0, rows, _BATCH_ROWS):
        batch = values[i:i + _BATCH_ROWS]
        batch_start_row = start_row + i
        batch_start = f"{start_col}{batch_start_row}"
        end = _end_cell(batch_start, len(batch), cols)
        cell_range = f"{sheet_id}!{batch_start}:{end}"

        resp = requests.put(
            f"{_BASE}/sheets/v2/spreadsheets/{token}/values",
            headers=get_headers(),
            json={"valueRange": {"range": cell_range, "values": batch}},
            timeout=30,
        )
        data = resp.json()
        check_feishu_resp(
            data,
            "请确认 spreadsheet_token 和 sheet_id 正确，且应用已有表格编辑权限",
        )
        rows_written += len(batch)

    output_ok({
        "spreadsheet_token": token,
        "sheet_id": sheet_id,
        "range": f"{sheet_id}!{start}:{_end_cell(start, rows, cols)}",
        "rows_written": rows_written,
        "cols_written": cols,
    })


if __name__ == "__main__":
    main()
