import json
import sys

from ._tasks_store import list_jobs


def main() -> None:
    result = list_jobs()
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()

