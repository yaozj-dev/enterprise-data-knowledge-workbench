"""Evaluate the deterministic first-pass intent router on static and reviewed JSONL cases."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.workbench_service import classify_intent  # noqa: E402, I001


def load_cases(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"找不到评测集：{path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def evaluate(cases: list[dict]) -> dict:
    predictions = [classify_intent(case["query"]) for case in cases]
    correct = sum(
        prediction == case["expected"] for case, prediction in zip(cases, predictions)
    )
    accuracy = correct / len(cases) if cases else 0
    expected_counts = Counter(case["expected"] for case in cases)
    by_intent = {}
    for intent in sorted(expected_counts):
        intent_correct = sum(
            prediction == intent and case["expected"] == intent
            for case, prediction in zip(cases, predictions)
        )
        total = expected_counts[intent]
        by_intent[intent] = {"correct": intent_correct, "total": total}
    return {"total": len(cases), "correct": correct, "accuracy": accuracy, "by_intent": by_intent}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the deterministic intent router")
    parser.add_argument("--cases-path", type=Path, default=PROJECT_ROOT / "evals" / "router_cases.jsonl")
    parser.add_argument(
        "--regression-path",
        type=Path,
        help="Optional JSONL exported from approved user feedback",
    )
    args = parser.parse_args()
    cases = load_cases(args.cases_path)
    if args.regression_path:
        cases.extend(load_cases(args.regression_path))
    metrics = evaluate(cases)
    print(
        f"router_cases={metrics['total']} correct={metrics['correct']} "
        f"accuracy={metrics['accuracy']:.2%}"
    )
    for intent, value in sorted(metrics["by_intent"].items()):
        print(f"intent={intent} correct={value['correct']}/{value['total']} accuracy={value['correct'] / value['total']:.2%}")


if __name__ == "__main__":
    main()
