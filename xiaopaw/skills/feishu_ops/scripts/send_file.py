"""发送飞书文件消息（先上传文件，再发送 file 消息）。

用法：
    python send_file.py --routing_key p2p:ou_xxx --file_path /workspace/sessions/s-xxx/outputs/report.pdf

支持的文件类型：pdf, doc/docx, xls/xlsx, ppt/pptx, mp4, opus；其他格式以 stream 类型发送。
文件大小上限：30MB。

典型场景：
    用户上传文件 → Sub-Crew 处理（如 PDF 转 Word）→ 输出保存到 outputs/ → feishu_ops 发回给用户
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import _feishu_auth as auth


# 飞书文件类型映射（扩展名 → file_type）
_FILE_TYPE_MAP = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}


def _feishu_file_type(path: Path) -> str:
    return _FILE_TYPE_MAP.get(path.suffix.lower(), "stream")


def upload_file(file_path: str, auth_header: dict) -> str:
    """上传文件到飞书，返回 file_key。"""
    path = Path(file_path)
    if not path.exists():
        auth.output_error(f"文件不存在：{file_path}")

    file_type = _feishu_file_type(path)
    with open(path, "rb") as f:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers=auth_header,
            data={"file_type": file_type, "file_name": path.name},
            files={"file": (path.name, f, "application/octet-stream")},
            timeout=120,
        )
    data = resp.json()
    auth.check_feishu_resp(
        data,
        hint="检查文件大小是否超过 30MB，文件格式是否受支持",
    )
    return data["data"]["file_key"]


def main() -> None:
    parser = argparse.ArgumentParser(description="发送飞书文件消息")
    parser.add_argument("--routing_key", required=True,
                        help="目标路由键，p2p:ou_xxx 或 group:oc_xxx")
    parser.add_argument("--file_path", required=True,
                        help="沙盒内文件绝对路径，如 /workspace/sessions/s-xxx/outputs/report.pdf")
    args = parser.parse_args()

    auth_header = auth.get_auth_header()
    file_key = upload_file(args.file_path, auth_header)

    receive_id_type, receive_id = auth.parse_routing_key(args.routing_key)
    headers = {**auth_header, "Content-Type": "application/json"}

    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers=headers,
        json={
            "receive_id": receive_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}),
            "uuid": str(uuid.uuid4()),
        },
        timeout=10,
    )
    data = resp.json()
    auth.check_feishu_resp(data)
    msg_id = data.get("data", {}).get("message_id", "")
    path = Path(args.file_path)
    auth.output_ok({
        "message_id": msg_id,
        "file_key": file_key,
        "file_name": path.name,
        "file_type": _feishu_file_type(path),
    })


if __name__ == "__main__":
    main()
