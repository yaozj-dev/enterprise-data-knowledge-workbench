"""Print a bounded trace budget summary from the local SQLite database."""

import argparse
import json
import sqlite3
from collections import Counter

from app.storage.app_db import DB_PATH


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect Agent tool budget traces"
    )
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT trace_summary FROM agent_runs "
            "WHERE trace_summary LIKE '%budget%' "
            "ORDER BY started_at DESC LIMIT ?",
            (args.limit,),
        ).fetchall()

    budgets = []
    for (raw,) in rows:
        try:
            value = json.loads(raw).get("budget")
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            budgets.append(value)

    report = {
        "observed": len(budgets),
        "call_count_distribution": dict(
            Counter(item.get("call_count") for item in budgets)
        ),
        "max_round_distribution": dict(
            Counter(item.get("max_observed_round") for item in budgets)
        ),
        "exhausted": sum(item.get("exhausted") is True for item in budgets),
        "examples": budgets[:5],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
