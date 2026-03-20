"""读取飞书云文档（Docx）纯文本内容。

用法：
    python read_doc.py --doc "https://xxx.feishu.cn/docx/doccnXXXXXXXX"
    python read_doc.py --doc doccnXXXXXXXX
"""

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import _feishu_auth as auth


def main() -> None:
    parser = argparse.ArgumentParser(description="读取飞书云文档纯文本内容")
    parser.add_argument("--doc", required=True,
                        help="飞书文档 URL（https://xxx.feishu.cn/docx/token）或 doc_token")
    args = parser.parse_args()

    doc_token = auth.parse_doc_token(args.doc)
    headers = auth.get_headers()

    resp = requests.get(
        f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content",
        headers=headers,
        timeout=30,
    )
    data = resp.json()
    auth.check_feishu_resp(
        data,
        hint="确认 doc_token 正确，且应用已获得文档读取权限（docs:doc:readonly）",
    )
    content = data.get("data", {}).get("content", "")
    auth.output_ok({"doc_token": doc_token, "content": content, "length": len(content)})


if __name__ == "__main__":
    main()
