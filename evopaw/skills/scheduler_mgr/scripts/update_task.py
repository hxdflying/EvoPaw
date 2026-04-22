import argparse
import json
import sys

from ._tasks_store import update_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Update an existing task.")
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--name")
    parser.add_argument("--routing_key")
    parser.add_argument("--message")
    parser.add_argument("--schedule_kind", choices=["at", "every", "cron"])
    parser.add_argument("--expr")
    parser.add_argument("--tz")
    parser.add_argument("--at_ms", type=int)
    parser.add_argument("--every_ms", type=int)
    parser.add_argument("--enabled", choices=["true", "false"])
    parser.add_argument("--delete_after_run", choices=["true", "false"])
    args = parser.parse_args()

    enabled = None
    if args.enabled is not None:
        enabled = args.enabled == "true"
    delete_after_run = None
    if args.delete_after_run is not None:
        delete_after_run = args.delete_after_run == "true"

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
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()

