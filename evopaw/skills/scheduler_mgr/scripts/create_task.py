import argparse
import json
import sys

from ._tasks_store import create_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new cron/at/every task.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--routing_key", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--schedule_kind", choices=["at", "every", "cron"], required=True)
    parser.add_argument("--expr")
    parser.add_argument("--tz")
    parser.add_argument("--at_ms", type=int)
    parser.add_argument("--every_ms", type=int)
    parser.add_argument("--delete_after_run", action="store_true")
    args = parser.parse_args()

    result = create_job(
        name=args.name,
        schedule_kind=args.schedule_kind,
        routing_key=args.routing_key,
        message=args.message,
        expr=args.expr,
        tz=args.tz,
        at_ms=args.at_ms,
        every_ms=args.every_ms,
        delete_after_run=args.delete_after_run,
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()

