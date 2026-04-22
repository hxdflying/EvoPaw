"""发送飞书富文本消息（post 格式，支持标题 + 多段文字）。

用法：
    python send_post.py --routing_key p2p:ou_xxx --title "报告标题" \
        --paragraphs '["第一段内容", "第二段内容"]'

    # 带链接的段落（直接传 Markdown 风格链接，脚本自动解析）：
    python send_post.py --routing_key group:oc_xxx --title "通知" \
        --paragraphs '["请查看[文档](https://example.com)", "谢谢配合"]'
"""

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import _feishu_auth as auth


def _parse_paragraph(text: str) -> list[dict]:
    """将纯文本或含 [text](url) 格式的字符串解析为飞书 post paragraph 元素列表。"""
    elements = []
    pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            elements.append({"tag": "text", "text": text[last:m.start()]})
        elements.append({"tag": "a", "text": m.group(1), "href": m.group(2)})
        last = m.end()
    if last < len(text):
        elements.append({"tag": "text", "text": text[last:]})
    if not elements:
        elements.append({"tag": "text", "text": text})
    return elements


def main() -> None:
    parser = argparse.ArgumentParser(description="发送飞书富文本（post）消息")
    parser.add_argument("--routing_key", required=True,
                        help="目标路由键，p2p:ou_xxx 或 group:oc_xxx")
    parser.add_argument("--title", default="", help="消息标题（可选）")
    parser.add_argument("--paragraphs", required=True,
                        help='段落内容，JSON 字符串数组，如 ["第一段", "第二段"]')
    args = parser.parse_args()

    try:
        paragraphs: list[str] = json.loads(args.paragraphs)
    except json.JSONDecodeError as e:
        auth.output_error(f"--paragraphs 必须是合法的 JSON 字符串数组：{e}")
        return

    receive_id_type, receive_id = auth.parse_routing_key(args.routing_key)
    headers = auth.get_headers()

    content_body = {
        "zh_cn": {
            "title": args.title,
            "content": [_parse_paragraph(p) for p in paragraphs],
        }
    }

    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers=headers,
        json={
            "receive_id": receive_id,
            "msg_type": "post",
            "content": json.dumps(content_body),
            "uuid": str(uuid.uuid4()),
        },
        timeout=10,
    )
    data = resp.json()
    auth.check_feishu_resp(data, hint="检查 routing_key 格式和接收方 ID 是否正确")
    msg_id = data.get("data", {}).get("message_id", "")
    auth.output_ok({"message_id": msg_id})


if __name__ == "__main__":
    main()
