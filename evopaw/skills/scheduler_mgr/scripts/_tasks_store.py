import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


TASKS_PATH = Path("/workspace/cron/tasks.json")


def _load_store() -> Dict[str, Any]:
    if not TASKS_PATH.exists():
        return {"version": 1, "jobs": []}
    try:
        data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("tasks.json root is not an object")
        jobs = data.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("tasks.json.jobs is not an array")
        return {"version": int(data.get("version", 1)), "jobs": list(jobs)}
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"failed to load tasks.json: {exc}") from exc


def _dump_store(store: Dict[str, Any]) -> None:
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(store, ensure_ascii=False, indent=2)
    tmp = TASKS_PATH.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(TASKS_PATH)


def _now_ms() -> int:
    return int(time.time() * 1000)


def list_jobs() -> Dict[str, Any]:
    store = _load_store()
    jobs: List[Dict[str, Any]] = store.get("jobs", [])
    return {
        "errcode": 0,
        "errmsg": "success",
        "data": {"total": len(jobs), "jobs": jobs},
    }


def create_job(
    name: str,
    schedule_kind: str,
    routing_key: str,
    message: str,
    expr: str | None = None,
    tz: str | None = None,
    at_ms: int | None = None,
    every_ms: int | None = None,
    delete_after_run: bool = False,
) -> Dict[str, Any]:
    import uuid

    if schedule_kind not in {"at", "every", "cron"}:
        return {
            "errcode": 1,
            "errmsg": f"无效的 schedule.kind：{schedule_kind}\n建议：使用 at/every/cron 之一。",
            "data": {},
        }

    if schedule_kind == "cron" and not expr:
        return {
            "errcode": 1,
            "errmsg": "cron 任务必须提供 expr 字段。\n建议：例如 '0 9 * * 1-5'。",
            "data": {},
        }

    if schedule_kind == "at" and at_ms is None:
        return {
            "errcode": 1,
            "errmsg": "at 任务必须提供 at_ms（Unix 毫秒时间戳）。",
            "data": {},
        }

    if schedule_kind == "every" and every_ms is None:
        return {
            "errcode": 1,
            "errmsg": "every 任务必须提供 every_ms（间隔毫秒数）。",
            "data": {},
        }

    store = _load_store()
    now_ms = _now_ms()

    job_id = f"job-{uuid.uuid4().hex[:8]}"
    schedule: Dict[str, Any] = {
        "kind": schedule_kind,
        "at_ms": at_ms if schedule_kind == "at" else None,
        "every_ms": every_ms if schedule_kind == "every" else None,
        "expr": expr if schedule_kind == "cron" else None,
        "tz": tz if schedule_kind == "cron" else None,
    }

    job: Dict[str, Any] = {
        "id": job_id,
        "name": name,
        "enabled": True,
        "schedule": schedule,
        "payload": {
            "routing_key": routing_key,
            "message": message,
        },
        "state": {
            "next_run_at_ms": None,
            "last_run_at_ms": None,
            "last_status": None,
            "last_error": None,
        },
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
        "delete_after_run": delete_after_run,
    }

    store["jobs"].append(job)
    _dump_store(store)

    return {
        "errcode": 0,
        "errmsg": "success",
        "data": {
            "action": "created",
            "job_id": job_id,
            "name": name,
            "schedule_kind": schedule_kind,
        },
    }


def delete_job(job_id: str) -> Dict[str, Any]:
    store = _load_store()
    jobs: List[Dict[str, Any]] = store.get("jobs", [])
    before = len(jobs)
    jobs = [j for j in jobs if j.get("id") != job_id]

    if len(jobs) == before:
        return {
            "errcode": 1,
            "errmsg": f"未找到要删除的任务：{job_id}",
            "data": {},
        }

    store["jobs"] = jobs
    _dump_store(store)
    return {
        "errcode": 0,
        "errmsg": "success",
        "data": {"action": "deleted", "job_id": job_id},
    }


def update_job(
    job_id: str,
    name: str | None = None,
    enabled: bool | None = None,
    routing_key: str | None = None,
    message: str | None = None,
    schedule_kind: str | None = None,
    expr: str | None = None,
    tz: str | None = None,
    at_ms: int | None = None,
    every_ms: int | None = None,
    delete_after_run: bool | None = None,
) -> Dict[str, Any]:
    store = _load_store()
    jobs: List[Dict[str, Any]] = store.get("jobs", [])
    target = None
    for j in jobs:
        if j.get("id") == job_id:
            target = j
            break
    if target is None:
        return {
            "errcode": 1,
            "errmsg": f"未找到要更新的任务：{job_id}",
            "data": {},
        }

    changed = False
    if name is not None:
        target["name"] = name
        changed = True
    if enabled is not None:
        target["enabled"] = enabled
        changed = True
    if delete_after_run is not None:
        target["delete_after_run"] = delete_after_run
        changed = True

    payload = target.setdefault("payload", {})
    if routing_key is not None:
        payload["routing_key"] = routing_key
        changed = True
    if message is not None:
        payload["message"] = message
        changed = True

    schedule = target.setdefault("schedule", {})
    if schedule_kind is not None:
        schedule["kind"] = schedule_kind
        changed = True
    if expr is not None:
        schedule["expr"] = expr
        changed = True
    if tz is not None:
        schedule["tz"] = tz
        changed = True
    if at_ms is not None:
        schedule["at_ms"] = at_ms
        changed = True
    if every_ms is not None:
        schedule["every_ms"] = every_ms
        changed = True

    if not changed:
        return {
            "errcode": 0,
            "errmsg": "success",
            "data": {"action": "noop", "job_id": job_id},
        }

    target_state = target.setdefault("state", {})
    # 修改 schedule 后，让 CronService 重新计算 next_run_at_ms
    target_state["next_run_at_ms"] = None
    target_state.setdefault("last_run_at_ms", None)
    target_state.setdefault("last_status", None)
    target_state.setdefault("last_error", None)

    target["updated_at_ms"] = _now_ms()
    store["jobs"] = jobs
    _dump_store(store)

    return {
        "errcode": 0,
        "errmsg": "success",
        "data": {"action": "updated", "job_id": job_id},
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="Low-level tasks.json helper.")
    parser.add_argument("action", choices=["list", "create", "delete", "update"])
    parser.add_argument("--name")
    parser.add_argument("--job_id")
    parser.add_argument("--routing_key")
    parser.add_argument("--message")
    parser.add_argument("--schedule_kind")
    parser.add_argument("--expr")
    parser.add_argument("--tz")
    parser.add_argument("--at_ms", type=int)
    parser.add_argument("--every_ms", type=int)
    parser.add_argument("--enabled", type=str)
    parser.add_argument("--delete_after_run", type=str)
    args = parser.parse_args()

    try:
        if args.action == "list":
            result = list_jobs()
        elif args.action == "create":
            result = create_job(
                name=args.name or "未命名任务",
                schedule_kind=args.schedule_kind or "cron",
                routing_key=args.routing_key or "",
                message=args.message or "",
                expr=args.expr,
                tz=args.tz,
                at_ms=args.at_ms,
                every_ms=args.every_ms,
                delete_after_run=(
                    str(args.delete_after_run).lower() == "true"
                    if args.delete_after_run is not None
                    else False
                ),
            )
        elif args.action == "delete":
            if not args.job_id:
                raise ValueError("delete 操作必须提供 --job_id")
            result = delete_job(args.job_id)
        else:  # update
            if not args.job_id:
                raise ValueError("update 操作必须提供 --job_id")
            enabled = None
            if args.enabled is not None:
                enabled = args.enabled.lower() == "true"
            delete_after_run = None
            if args.delete_after_run is not None:
                delete_after_run = args.delete_after_run.lower() == "true"
            result = update_job(
                job_id=args.job_id,
                name=args.name,
                enabled=enabled,
                routing_key=args.routing_key,
                message=args.message,
                schedule_kind=args.schedule_kind,
                expr=args.expr,
                tz=args.tz,
                at_ms=args.at_ms,
                every_ms=args.every_ms,
                delete_after_run=delete_after_run,
            )
    except Exception as exc:  # noqa: BLE001
        result = {
            "errcode": 1,
            "errmsg": f"内部错误：{exc}",
            "data": {},
        }

    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    _main()

