"""将本地 Excel（.xlsx）文件导入为飞书电子表格。

用法：
    python upload_sheet.py --file_path /workspace/sessions/xxx/outputs/report.xlsx
    python upload_sheet.py --file_path /workspace/data.xlsx --title "销售报告" --folder_token <token>

说明：
    三步导入流程：
      1. 上传文件到飞书云盘（upload_all）→ file_token
      2. 创建导入任务（import_tasks）→ ticket
      3. 轮询导入状态（import_tasks/{ticket}）→ spreadsheet_token + url

    飞书限制：
      - 文件大小 ≤ 20MB
      - 仅支持 .xlsx / .xls 格式（通过 --file_path 后缀自动判断）
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import requests  # noqa: E402
from _feishu_auth import (  # noqa: E402
    check_feishu_resp,
    get_auth_header,
    get_headers,
    output_error,
    output_ok,
)

_BASE = "https://open.feishu.cn/open-apis"
_POLL_INTERVAL = 2   # 每隔 2 秒轮询一次
_POLL_TIMEOUT = 60   # 最多等待 60 秒


def _set_tenant_readable(token: str) -> None:
    """设置表格为组织内成员持链接可阅读。"""
    requests.patch(
        f"{_BASE}/drive/v2/permissions/{token}/public",
        params={"type": "sheet"},
        headers=get_headers(),
        json={"link_share_entity": "tenant_readable"},
        timeout=10,
    )


def _get_real_url(token: str) -> str:
    """通过 drive meta 接口获取表格的真实用户端 URL（含租户域名）。"""
    resp = requests.post(
        f"{_BASE}/drive/v1/metas/batch_query",
        headers=get_headers(),
        json={"request_docs": [{"doc_token": token, "doc_type": "sheet"}], "with_url": True},
        timeout=10,
    )
    try:
        url = resp.json()["data"]["metas"][0]["url"]
        if url:
            return url
    except (KeyError, IndexError):
        pass
    return f"https://open.feishu.cn/sheets/{token}"


def _upload_file(file_path: Path, file_name: str) -> str:
    """将文件上传到飞书云盘根目录，返回 file_token。"""
    file_size = file_path.stat().st_size
    with file_path.open("rb") as f:
        resp = requests.post(
            f"{_BASE}/drive/v1/files/upload_all",
            headers=get_auth_header(),
            data={
                "file_name": file_name,
                "parent_type": "explorer",
                "parent_node": "",      # 根目录
                "size": str(file_size),
            },
            files={"file": (file_name, f, "application/octet-stream")},
            timeout=60,
        )
    data = resp.json()
    check_feishu_resp(data, "文件上传失败，请确认文件路径正确且文件大小不超过 20MB")
    return data["data"]["file_token"]


def _create_import_task(
    file_token: str,
    file_extension: str,
    file_name: str,
    folder_token: str,
) -> str:
    """创建导入任务，返回 ticket。"""
    point: dict = {"mount_type": 1}    # mount_type=1 表示放在 我的空间
    if folder_token:
        point["mount_key"] = folder_token

    payload = {
        "file_extension": file_extension,
        "file_token": file_token,
        "type": "sheet",
        "file_name": file_name,
        "point": point,
    }
    resp = requests.post(
        f"{_BASE}/drive/v1/import_tasks",
        headers=get_headers(),
        json=payload,
        timeout=15,
    )
    data = resp.json()
    check_feishu_resp(data, "创建导入任务失败，请确认文件 token 有效")
    return data["data"]["ticket"]


def _poll_import_task(ticket: str) -> dict:
    """轮询导入任务状态，完成后返回 {spreadsheet_token, url}。

    job_status:
      0 = 成功
      1 = 初始化
      2 = 处理中
      3 = 失败
    """
    deadline = time.monotonic() + _POLL_TIMEOUT
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{_BASE}/drive/v1/import_tasks/{ticket}",
            headers=get_headers(),
            timeout=15,
        )
        data = resp.json()
        check_feishu_resp(data, "查询导入任务状态失败")
        result = data["data"]["result"]
        status = result.get("job_status")
        if status == 0:
            return {
                "spreadsheet_token": result.get("token", ""),
                "url": result.get("url", ""),
            }
        if status == 3:
            error_msg = result.get("job_error_msg", "未知错误")
            output_error(f"导入任务失败：{error_msg}", "请确认文件内容格式正确（标准 xlsx 格式）")
        # status 1/2：继续等待
        time.sleep(_POLL_INTERVAL)

    output_error(
        f"导入任务超时（>{_POLL_TIMEOUT}s），可能文件较大或服务繁忙",
        "可稍后在飞书云空间确认文件是否已导入",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file_path", required=True, help="本地 xlsx/xls 文件绝对路径")
    parser.add_argument(
        "--title",
        default="",
        help="导入后的电子表格名称（留空则使用文件名）",
    )
    parser.add_argument(
        "--folder_token",
        default="",
        help="目标文件夹 token（留空则放在应用根目录）",
    )
    args = parser.parse_args()

    file_path = Path(args.file_path)

    # 参数校验
    if not file_path.exists():
        output_error(f"文件不存在：{args.file_path}", "请确认 --file_path 路径正确")
    if not file_path.is_file():
        output_error(f"路径不是文件：{args.file_path}")

    suffix = file_path.suffix.lower()
    if suffix not in (".xlsx", ".xls"):
        output_error(
            f"不支持的文件格式：{suffix}",
            "仅支持 .xlsx / .xls 格式",
        )

    file_extension = suffix.lstrip(".")  # "xlsx" or "xls"
    file_name = args.title if args.title else file_path.name

    # 确保文件名带扩展名
    if not file_name.endswith(f".{file_extension}"):
        file_name = f"{file_name}.{file_extension}"

    # 步骤 1：上传文件
    file_token = _upload_file(file_path, file_name)

    # 步骤 2：创建导入任务
    ticket = _create_import_task(file_token, file_extension, file_name, args.folder_token)

    # 步骤 3：轮询等待完成
    result = _poll_import_task(ticket)

    token = result["spreadsheet_token"]
    _set_tenant_readable(token)
    url = _get_real_url(token)

    output_ok({
        "spreadsheet_token": token,
        "url": url,
        "title": file_name,
        "file_size": file_path.stat().st_size,
    })


if __name__ == "__main__":
    main()
