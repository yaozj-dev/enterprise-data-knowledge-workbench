"""Export human-approved Agent feedback as a sanitized JSONL regression set."""

import argparse
import json
from pathlib import Path

from app.storage.app_db import init_db, list_regression_cases

PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "logs" / "feedback_regression_cases.jsonl"


def export_cases(output_path: Path) -> int:
    """Write only human-reviewed, already-sanitized regression inputs."""

    init_db()
    cases = list_regression_cases()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "query": case["query"],
                "expected": case["expected_route"],
                "source": "approved_user_feedback",
                "source_issue_type": case.get("source_issue_type"),
                "feedback_id": case["feedback_id"],
            },
            ensure_ascii=False,
        )
        for case in cases
    ]
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(cases)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export approved Agent feedback regression cases")
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    count = export_cases(args.output_path)
    print(f"regression_cases={count} output={args.output_path}")


if __name__ == "__main__":
    main()
