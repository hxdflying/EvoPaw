"""发送飞书图片消息（先上传图片，再发送 image 消息）。

用法：
    python send_image.py --routing_key p2p:ou_xxx --image_path /workspace/sessions/s-xxx/outputs/chart.png

图片上传限制：不超过 30MB，支持 jpg / jpeg / png / gif / webp 格式。
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import _feishu_auth as auth


def upload_image(image_path: str, auth_header: dict) -> str:
    """上传图片到飞书，返回 image_key。"""
    path = Path(image_path)
    if not path.exists():
        auth.output_error(f"图片文件不存在：{image_path}")

    with open(path, "rb") as f:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers=auth_header,
            data={"image_type": "message"},
            files={"image": (path.name, f, _mime_type(path.suffix))},
            timeout=60,
        )
    data = resp.json()
    auth.check_feishu_resp(data, hint="检查图片格式是否为 jpg/png/gif/webp，且不超过 30MB")
    return data["data"]["image_key"]


def _mime_type(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix.lower(), "application/octet-stream")


def main() -> None:
    parser = argparse.ArgumentParser(description="发送飞书图片消息")
    parser.add_argument("--routing_key", required=True,
                        help="目标路由键，p2p:ou_xxx 或 group:oc_xxx")
    parser.add_argument("--image_path", required=True,
                        help="沙盒内图片文件绝对路径，如 /workspace/sessions/s-xxx/outputs/img.png")
    args = parser.parse_args()

    auth_header = auth.get_auth_header()
    image_key = upload_image(args.image_path, auth_header)

    receive_id_type, receive_id = auth.parse_routing_key(args.routing_key)
    headers = {**auth_header, "Content-Type": "application/json"}

    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers=headers,
        json={
            "receive_id": receive_id,
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key}),
            "uuid": str(uuid.uuid4()),
        },
        timeout=10,
    )
    data = resp.json()
    auth.check_feishu_resp(data)
    msg_id = data.get("data", {}).get("message_id", "")
    auth.output_ok({"message_id": msg_id, "image_key": image_key})


if __name__ == "__main__":
    main()
