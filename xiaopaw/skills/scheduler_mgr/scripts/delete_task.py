import argparse
import json
import sys

from ._tasks_store import delete_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete a task by job_id.")
    parser.add_argument("--job_id", required=True)
    args = parser.parse_args()

    result = delete_job(args.job_id)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()

