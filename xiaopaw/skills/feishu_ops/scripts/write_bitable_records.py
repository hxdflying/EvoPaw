"""向多维表格（Bitable）数据表批量写入记录。

用法：
    python write_bitable_records.py \
        --app "https://xxx.feishu.cn/base/BxxXXXX" \
        --table_id tblXXXXXX \
        --records '[
            {"任务名称": "完成 API 文档", "优先级": "高", "完成": false},
            {"任务名称": "代码 Review", "优先级": "中", "完成": true}
        ]'

说明：
    --records 中的键使用字段名称（而非字段 ID），飞书 API 直接支持。
    每批最多 500 条，超过时自动分批写入。
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
    parse_bitable_token,
)

_BASE = "https://open.feishu.cn/open-apis"
_BATCH_SIZE = 500


def _batch_create(app_token: str, table_id: str, records_batch: list[dict]) -> list[str]:
    """批量创建记录，返回 record_id 列表。"""
    payload = {"records": [{"fields": r} for r in records_batch]}
    resp = requests.post(
        f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
        headers=get_headers(),
        json=payload,
        timeout=30,
    )
    data = resp.json()
    check_feishu_resp(
        data,
        "请确认 app_token、table_id 正确，且字段名称与数据表定义一致",
    )
    return [r["record_id"] for r in data["data"].get("records", [])]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, help="多维表格 URL 或 app_token")
    parser.add_argument("--table_id", required=True, help="数据表 ID（如 tblXXXXXX）")
    parser.add_argument(
        "--records",
        required=True,
        help=(
            "JSON 数组，每项是一条记录（键为字段名称）。\n"
            '示例：\'[{"任务": "写文档", "优先级": "高"}, {"任务": "测试", "优先级": "中"}]\''
        ),
    )
    args = parser.parse_args()

    app_token = parse_bitable_token(args.app)

    try:
        records: list[dict] = json.loads(args.records)
    except json.JSONDecodeError as e:
        output_error(f"--records 不是合法 JSON：{e}", '示例：\'[{"字段名": "值"}]\'')

    if not isinstance(records, list):
        output_error("--records 必须是 JSON 数组")
    if not records:
        output_error("--records 不能为空数组")

    # 分批写入
    all_record_ids: list[str] = []
    for i in range(0, len(records), _BATCH_SIZE):
        batch = records[i: i + _BATCH_SIZE]
        ids = _batch_create(app_token, args.table_id, batch)
        all_record_ids.extend(ids)

    output_ok({
        "app_token": app_token,
        "table_id": args.table_id,
        "record_count": len(all_record_ids),
        "record_ids": all_record_ids,
    })


if __name__ == "__main__":
    main()
