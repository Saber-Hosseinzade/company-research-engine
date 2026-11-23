"""Turn `gh run list --json conclusion,createdAt` output into daily CI failure rates.

Reads the runs on stdin and prints a JSON list of readings, oldest first: one per UTC day that had
finished runs, each the share of runs that failed. Cancelled and skipped runs don't count.
"""

import json
import sys
from collections import defaultdict
from typing import Any

COUNTED = {"success", "failure", "timed_out", "startup_failure"}
FAILED = {"failure", "timed_out", "startup_failure"}


def daily_failure_rates(runs: list[dict[str, Any]], days: int = 31) -> list[float]:
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # day -> [failed, counted]
    for run in runs:
        conclusion = run.get("conclusion")
        if conclusion not in COUNTED:
            continue
        day = str(run["createdAt"])[:10]
        totals[day][1] += 1
        totals[day][0] += conclusion in FAILED
    return [round(failed / counted, 4) for _, (failed, counted) in sorted(totals.items())[-days:]]


if __name__ == "__main__":
    print(json.dumps(daily_failure_rates(json.load(sys.stdin))))
