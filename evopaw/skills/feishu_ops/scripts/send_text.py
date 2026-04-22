"""发送飞书文字消息。

用法：
    python send_text.py --routing_key p2p:ou_xxx --text "消息内容"
    python send_text.py --routing_key group:oc_xxx --text "群消息"
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import _feishu_auth as auth


def main() -> None:
    parser = argparse.ArgumentParser(description="发送飞书文字消息")
    parser.add_argument("--routing_key", required=True,
                        help="目标路由键，格式：p2p:ou_xxx（用户）或 group:oc_xxx（群组）")
    parser.add_argument("--text", required=True, help="消息文本内容")
    args = parser.parse_args()

    receive_id_type, receive_id = auth.parse_routing_key(args.routing_key)
    headers = auth.get_headers()

    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers=headers,
        json={
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": args.text}),
            "uuid": str(uuid.uuid4()),
        },
        timeout=10,
    )
    data = resp.json()
    auth.check_feishu_resp(
        data,
        hint="p2p 消息用 p2p:ou_xxx，群消息用 group:oc_xxx，确认接收方 ID 正确",
    )
    msg_id = data.get("data", {}).get("message_id", "")
    auth.output_ok({"message_id": msg_id})


if __name__ == "__main__":
    main()
