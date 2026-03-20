"""查询飞书群组成员列表。

用法：
    python get_chat_members.py --chat_id oc_xxxxx
"""

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import _feishu_auth as auth


def main() -> None:
    parser = argparse.ArgumentParser(description="查询飞书群组成员列表")
    parser.add_argument("--chat_id", required=True,
                        help="群组 chat_id，格式 oc_xxxxx")
    args = parser.parse_args()

    headers = auth.get_headers()
    members = []
    page_token = ""

    while True:
        params: dict = {"member_id_type": "open_id", "page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(
            f"https://open.feishu.cn/open-apis/im/v1/chats/{args.chat_id}/members",
            headers=headers,
            params=params,
            timeout=15,
        )
        data = resp.json()
        auth.check_feishu_resp(
            data,
            hint="确认 chat_id 格式正确（oc_xxx），且应用已加入该群并有成员查询权限",
        )
        page_data = data.get("data", {})
        members.extend(page_data.get("items", []))
        if not page_data.get("has_more"):
            break
        page_token = page_data.get("page_token", "")

    auth.output_ok({
        "chat_id": args.chat_id,
        "member_count": len(members),
        "members": members,
    })


if __name__ == "__main__":
    main()
