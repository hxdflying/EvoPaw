"""飞书认证与公共工具模块。

所有 feishu_ops scripts 通过 sys.path 导入本模块，避免重复代码。
凭证来自沙盒路径 /workspace/.config/feishu.json，不暴露给 LLM。
"""

import json
import re
import sys
import requests


# ───────────────────────── 认证 ─────────────────────────


def _get_token() -> str:
    """从凭证文件获取 tenant_access_token。"""
    try:
        with open("/workspace/.config/feishu.json") as f:
            creds = json.load(f)
    except FileNotFoundError:
        _exit_error("凭证文件不存在：/workspace/.config/feishu.json")

    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": creds["app_id"], "app_secret": creds["app_secret"]},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        _exit_error(f"获取 tenant_access_token 失败：code={data.get('code')}, msg={data.get('msg')}")
    return data["tenant_access_token"]


def get_headers() -> dict:
    """返回 JSON 请求 headers（含 Authorization Bearer token）。"""
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
    }


def get_auth_header() -> dict:
    """返回仅含 Authorization 的 headers（用于 multipart 文件上传）。"""
    return {"Authorization": f"Bearer {_get_token()}"}


# ───────────────────────── 路由解析 ─────────────────────────


def parse_routing_key(routing_key: str) -> tuple[str, str]:
    """将 routing_key 转换为 (receive_id_type, receive_id)。

    支持格式：
    - p2p:ou_xxx   → ("open_id", "ou_xxx")
    - group:oc_xxx → ("chat_id", "oc_xxx")
    - ou_xxx       → ("open_id", "ou_xxx")  直接传 open_id
    - oc_xxx       → ("chat_id", "oc_xxx")  直接传 chat_id
    """
    if routing_key.startswith("p2p:"):
        return "open_id", routing_key[4:]
    if routing_key.startswith("group:"):
        return "chat_id", routing_key[6:]
    if routing_key.startswith("ou_"):
        return "open_id", routing_key
    if routing_key.startswith("oc_"):
        return "chat_id", routing_key
    # 无法推断，默认当 open_id
    return "open_id", routing_key


# ───────────────────────── URL 解析 ─────────────────────────


def parse_doc_token(url_or_token: str) -> str:
    """从飞书文档 URL 或 token 字符串中提取 doc_token。

    支持：
    - https://xxx.feishu.cn/docx/{token}
    - https://xxx.feishu.cn/docs/{token}  （旧版）
    - 直接传 token
    """
    m = re.search(r"/doc[xs]?/([A-Za-z0-9_-]+)", url_or_token)
    if m:
        return m.group(1)
    return url_or_token.strip().rstrip("/")


def parse_sheet_token(url_or_token: str) -> str:
    """从飞书电子表格 URL 或 token 字符串中提取 spreadsheet_token。

    支持：
    - https://xxx.feishu.cn/sheets/{token}
    - https://xxx.feishu.cn/spreadsheets/{token}
    - 直接传 token
    """
    m = re.search(r"/s(?:preadsheet|heet)s?/([A-Za-z0-9_-]+)", url_or_token)
    if m:
        return m.group(1)
    return url_or_token.strip().rstrip("/")


def parse_bitable_token(url_or_token: str) -> str:
    """从飞书多维表格 URL 或 token 字符串中提取 app_token。

    支持：
    - https://xxx.feishu.cn/base/{token}
    - https://xxx.feishu.cn/base/{token}?table=tblXXX
    - 直接传 token
    """
    m = re.search(r"/base/([A-Za-z0-9_-]+)", url_or_token)
    if m:
        return m.group(1)
    return url_or_token.strip().rstrip("/")


# ───────────────────────── 输出规范 ─────────────────────────


def output_ok(data: dict) -> None:
    """打印成功结果并退出（exit 0）。"""
    print(json.dumps({"errcode": 0, "errmsg": "success", "data": data}, ensure_ascii=False))
    sys.exit(0)


def output_error(errmsg: str, hint: str = "") -> None:
    """打印错误结果并退出（exit 0，errcode=1）。"""
    msg = errmsg + (f"\n建议：{hint}" if hint else "")
    print(json.dumps({"errcode": 1, "errmsg": msg, "data": {}}, ensure_ascii=False))
    sys.exit(0)


def _exit_error(errmsg: str) -> None:
    """内部错误出口，不对外暴露。"""
    output_error(errmsg)


def check_feishu_resp(resp_data: dict, hint: str = "") -> None:
    """检查飞书 API 响应，非 0 时打印错误并退出。"""
    if resp_data.get("code") != 0:
        output_error(
            f"飞书 API 错误：code={resp_data.get('code')}, msg={resp_data.get('msg')}",
            hint,
        )
