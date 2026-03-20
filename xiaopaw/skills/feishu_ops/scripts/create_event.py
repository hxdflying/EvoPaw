"""在飞书日历中创建事件（需应用已订阅目标日历且有写入权限）。

用法：
    python create_event.py \\
        --calendar_id feishu_xxxxxx \\
        --summary "周例会" \\
        --start_time 2026-03-09T10:00:00+08:00 \\
        --end_time 2026-03-09T11:00:00+08:00 \\
        --description "本周项目进度同步" \\
        --attendees '["ou_aaaa", "ou_bbbb"]'

注意：tenant_access_token 只能在应用有写入权限的日历上创建事件。
"""

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import _feishu_auth as auth


def main() -> None:
    parser = argparse.ArgumentParser(description="在飞书日历中创建事件")
    parser.add_argument("--calendar_id", required=True,
                        help="日历 ID，应用有写入权限的共享日历")
    parser.add_argument("--summary", required=True, help="事件标题")
    parser.add_argument("--start_time", required=True,
                        help="开始时间，RFC3339 格式，如 2026-03-09T10:00:00+08:00")
    parser.add_argument("--end_time", required=True,
                        help="结束时间，RFC3339 格式，如 2026-03-09T11:00:00+08:00")
    parser.add_argument("--description", default="", help="事件描述（可选）")
    parser.add_argument("--attendees", default="[]",
                        help='与会者 open_id 列表，JSON 数组，如 \'["ou_xxx", "ou_yyy"]\'')
    args = parser.parse_args()

    try:
        attendee_ids: list[str] = json.loads(args.attendees)
    except json.JSONDecodeError as e:
        auth.output_error(f"--attendees 必须是合法的 JSON 字符串数组：{e}")
        return

    headers = auth.get_headers()

    body: dict = {
        "summary": args.summary,
        "start_time": {"timestamp": _to_unix_ts(args.start_time), "timezone": "Asia/Shanghai"},
        "end_time": {"timestamp": _to_unix_ts(args.end_time), "timezone": "Asia/Shanghai"},
    }
    if args.description:
        body["description"] = args.description
    if attendee_ids:
        body["attendees"] = [{"type": "user", "user_id": oid} for oid in attendee_ids]

    resp = requests.post(
        f"https://open.feishu.cn/open-apis/calendar/v4/calendars/{args.calendar_id}/events"
        "?user_id_type=open_id",
        headers=headers,
        json=body,
        timeout=15,
    )
    data = resp.json()
    auth.check_feishu_resp(
        data,
        hint="确认 calendar_id 正确，且应用有日历写入权限（calendar:calendar）",
    )
    event = data.get("data", {}).get("event", {})
    auth.output_ok({
        "event_id": event.get("event_id", ""),
        "summary": args.summary,
        "start_time": args.start_time,
        "end_time": args.end_time,
    })


def _to_unix_ts(rfc3339: str) -> str:
    """将 RFC3339 时间字符串转为 Unix 时间戳字符串（飞书 API 要求）。"""
    from datetime import datetime, timezone
    import re
    # 兼容 +08:00 / Z 两种格式
    s = rfc3339.replace("Z", "+00:00")
    # Python 3.7+ 支持 %z 解析 +HH:MM
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        auth.output_error(f"时间格式错误：{rfc3339}，请使用 RFC3339 格式，如 2026-03-09T10:00:00+08:00")
        return ""  # output_error calls sys.exit; this line is a safety guard
    dt_utc = dt.astimezone(timezone.utc)
    return str(int(dt_utc.timestamp()))


if __name__ == "__main__":
    main()
