"""查询飞书日历事件列表（需应用已订阅目标日历）。

用法：
    python list_events.py --calendar_id "primary" --start_time 2026-03-01T00:00:00+08:00 --end_time 2026-03-31T23:59:59+08:00
    python list_events.py --calendar_id "feishu_xxxxxx" --start_time 2026-03-06T00:00:00Z --end_time 2026-03-07T00:00:00Z

注意：tenant_access_token 只能访问应用已订阅（subscribe）的日历，无法访问用户个人 primary 日历。
      如需访问共享团队日历，先确认应用已在飞书后台完成日历订阅。
"""

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import _feishu_auth as auth


def main() -> None:
    parser = argparse.ArgumentParser(description="查询飞书日历事件")
    parser.add_argument("--calendar_id", required=True,
                        help="日历 ID，应用订阅的共享日历 ID（如 feishu_xxxxxx）")
    parser.add_argument("--start_time", required=True,
                        help="查询开始时间，RFC3339 格式，如 2026-03-01T00:00:00+08:00")
    parser.add_argument("--end_time", required=True,
                        help="查询结束时间，RFC3339 格式，如 2026-03-31T23:59:59+08:00")
    args = parser.parse_args()

    headers = auth.get_headers()
    events = []
    page_token = ""

    while True:
        params: dict = {
            "start_time": args.start_time,
            "end_time": args.end_time,
            "page_size": 50,
        }
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(
            f"https://open.feishu.cn/open-apis/calendar/v4/calendars/{args.calendar_id}/events",
            headers=headers,
            params=params,
            timeout=15,
        )
        data = resp.json()
        auth.check_feishu_resp(
            data,
            hint="确认 calendar_id 正确，且应用已订阅该日历（calendar:calendar:readonly 权限）",
        )
        page_data = data.get("data", {})
        events.extend(page_data.get("items", []))
        if not page_data.get("has_more"):
            break
        page_token = page_data.get("page_token", "")

    auth.output_ok({
        "calendar_id": args.calendar_id,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "event_count": len(events),
        "events": events,
    })


if __name__ == "__main__":
    main()
