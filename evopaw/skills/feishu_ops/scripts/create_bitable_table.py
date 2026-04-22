"""在多维表格（Bitable）内创建数据表，并定义字段结构。

用法：
    python create_bitable_table.py \
        --app "https://xxx.feishu.cn/base/BxxXXXX" \
        --name "任务清单" \
        --fields '[
            {"name": "任务名称", "type": "text"},
            {"name": "优先级", "type": "select", "options": ["高","中","低"]},
            {"name": "标签", "type": "multiselect", "options": ["前端","后端","设计"]},
            {"name": "截止日期", "type": "date"},
            {"name": "完成", "type": "checkbox"},
            {"name": "工时(h)", "type": "number"},
            {"name": "参考链接", "type": "url"}
        ]'

字段类型（--fields 中的 type 字段）：
    text        — 多行文本
    number      — 数字
    select      — 单选（需提供 options 数组）
    multiselect — 多选（需提供 options 数组）
    date        — 日期
    checkbox    — 勾选框
    url         — 超链接
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

# 类型名称 → Feishu field type ID
_TYPE_MAP = {
    "text": 1,
    "number": 2,
    "select": 3,
    "multiselect": 4,
    "date": 5,
    "checkbox": 7,
    "url": 15,
}


def _build_field_body(field_def: dict) -> dict:
    """将用户友好的字段定义转换为飞书 API 所需的 body。

    Args:
        field_def: {"name": "状态", "type": "select", "options": ["A","B"]}

    Returns:
        {"field_name": "状态", "type": 3, "property": {"options": [...]}}
    """
    name = field_def.get("name", "").strip()
    type_str = field_def.get("type", "text").lower().strip()

    if not name:
        output_error("字段定义缺少 name", "每个字段必须有 name 属性，如 {\"name\": \"状态\", \"type\": \"select\"}")

    type_id = _TYPE_MAP.get(type_str)
    if type_id is None:
        supported = ", ".join(_TYPE_MAP.keys())
        output_error(
            f"不支持的字段类型：{type_str!r}",
            f"支持的类型：{supported}",
        )

    body: dict = {"field_name": name, "type": type_id}

    # 单选 / 多选需要提供 options
    if type_str in ("select", "multiselect"):
        options = field_def.get("options", [])
        if options:
            body["property"] = {"options": [{"name": str(opt)} for opt in options]}

    return body


def _create_table(app_token: str, table_name: str) -> str:
    """创建空数据表，返回 table_id。"""
    resp = requests.post(
        f"{_BASE}/bitable/v1/apps/{app_token}/tables",
        headers=get_headers(),
        json={"table": {"name": table_name}},
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(data, "请确认 app_token 正确，且应用已有多维表格的编辑权限")
    return data["data"]["table_id"]


def _create_field(app_token: str, table_id: str, field_body: dict) -> str:
    """创建单个字段，返回 field_id。"""
    resp = requests.post(
        f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        headers=get_headers(),
        json=field_body,
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(
        data,
        f"创建字段 {field_body.get('field_name')!r} 失败，请检查字段定义",
    )
    return data["data"]["field"]["field_id"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, help="多维表格 URL 或 app_token")
    parser.add_argument("--name", required=True, help="数据表名称")
    parser.add_argument(
        "--fields",
        default="[]",
        help=(
            "JSON 数组，定义要创建的字段。每项格式：\n"
            '  {"name": "字段名", "type": "text|number|select|multiselect|date|checkbox|url"}\n'
            "  select/multiselect 可额外提供 options 数组"
        ),
    )
    args = parser.parse_args()

    app_token = parse_bitable_token(args.app)

    try:
        fields_raw: list[dict] = json.loads(args.fields)
    except json.JSONDecodeError as e:
        output_error(f"--fields 不是合法 JSON：{e}", "示例：'[{\"name\": \"状态\", \"type\": \"select\", \"options\": [\"待处理\",\"已完成\"]}]'")

    if not isinstance(fields_raw, list):
        output_error("--fields 必须是 JSON 数组", "示例：'[{\"name\": \"任务\", \"type\": \"text\"}]'")

    # 1. 创建数据表
    table_id = _create_table(app_token, args.name)

    # 2. 逐个创建字段（跳过空列表）
    created_fields = []
    for field_def in fields_raw:
        field_body = _build_field_body(field_def)
        field_id = _create_field(app_token, table_id, field_body)
        created_fields.append({"name": field_def["name"], "type": field_def.get("type", "text"), "field_id": field_id})

    output_ok({
        "app_token": app_token,
        "table_id": table_id,
        "table_name": args.name,
        "fields_created": created_fields,
    })


if __name__ == "__main__":
    main()
