"""创建飞书多维表格（Bitable）应用。

用法：
    python create_bitable.py --name "项目管理" [--folder_token <token>]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import requests  # noqa: E402
from _feishu_auth import check_feishu_resp, get_headers, output_ok  # noqa: E402

_BASE = "https://open.feishu.cn/open-apis"


def _set_tenant_readable(app_token: str) -> None:
    """设置多维表格为组织内成员持链接可阅读。"""
    requests.patch(
        f"{_BASE}/drive/v2/permissions/{app_token}/public",
        params={"type": "bitable"},
        headers=get_headers(),
        json={"link_share_entity": "tenant_readable"},
        timeout=10,
    )


def _get_real_url(app_token: str) -> str:
    """通过 drive meta 接口获取多维表格的真实用户端 URL（含租户域名）。"""
    resp = requests.post(
        f"{_BASE}/drive/v1/metas/batch_query",
        headers=get_headers(),
        json={"request_docs": [{"doc_token": app_token, "doc_type": "bitable"}], "with_url": True},
        timeout=10,
    )
    try:
        url = resp.json()["data"]["metas"][0]["url"]
        if url:
            return url
    except (KeyError, IndexError):
        pass
    return f"https://open.feishu.cn/base/{app_token}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="多维表格名称")
    parser.add_argument(
        "--folder_token",
        default="",
        help="存放多维表格的云空间文件夹 token（留空则放在 我的云空间 根目录）",
    )
    args = parser.parse_args()

    body: dict = {"name": args.name}
    if args.folder_token:
        body["folder_token"] = args.folder_token

    resp = requests.post(
        f"{_BASE}/bitable/v1/apps",
        headers=get_headers(),
        json=body,
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(data, "请确认 folder_token 正确，且应用已有该文件夹的编辑权限")

    app = data["data"]["app"]
    app_token = app.get("app_token", "")

    _set_tenant_readable(app_token)
    url = _get_real_url(app_token)

    output_ok({"app_token": app_token, "url": url, "name": args.name})


if __name__ == "__main__":
    main()
