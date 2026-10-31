"""Evaluate the live NL2SQL endpoint against fixed demo-warehouse results."""

import argparse
import asyncio
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from app.services.sql_policy import SQLPolicyError, normalize_read_query

PROJECT_ROOT = Path(__file__).parents[2]
CASES_PATH = PROJECT_ROOT / "evals" / "sql_result_cases.jsonl"
DEFAULT_ENDPOINT = "http://127.0.0.1:8000/api/query"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def parse_sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for chunk in body.split("\n\n"):
        payload = "\n".join(
            line.removeprefix("data:").strip()
            for line in chunk.splitlines()
            if line.startswith("data:")
        )
        if payload:
            events.append(json.loads(payload))
    return events


def normalized_values(rows: Any) -> list[str]:
    """保留旧评测用例的扁平值比较，供已有调用兼容使用。"""

    if not isinstance(rows, list):
        return []
    values: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            return []
        for value in row.values():
            if isinstance(value, float):
                values.append(f"{value:.6f}".rstrip("0").rstrip("."))
            else:
                values.append(str(value))
    return sorted(values)


def normalized_cell(value: Any) -> str:
    """将数值等价形式统一，避免 1、1.0、Decimal('1.00') 被误判不同。"""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, (int, float, Decimal, str)):
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            pass
        else:
            return f"number:{number.normalize()}"
    return f"text:{str(value)}"


def normalized_rows(rows: Any) -> list[tuple[str, ...]]:
    """按每一行的值组合比较，忽略模型生成 SQL 的列别名和返回顺序。"""

    if not isinstance(rows, list):
        return []
    normalized: list[tuple[str, ...]] = []
    for row in rows:
        if isinstance(row, dict):
            values = row.values()
        elif isinstance(row, (list, tuple)):
            values = row
        else:
            return []
        normalized.append(tuple(normalized_cell(value) for value in values))
    return sorted(normalized)


def result_matches(expected: list[Any], rows: Any) -> bool:
    """支持旧的单值数组，也支持新用例的按行结果集断言。"""

    if expected and isinstance(expected[0], (list, tuple)):
        return normalized_rows(rows) == normalized_rows(expected)
    return normalized_values(rows) == sorted(str(value) for value in expected)


async def evaluate(
    endpoint: str = DEFAULT_ENDPOINT,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> dict:
    cases = load_jsonl(CASES_PATH)
    if offset < 0:
        raise ValueError("offset cannot be negative")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    cases = cases[offset : offset + limit if limit is not None else None]
    metrics: dict[str, Any] = {
        "total": len(cases),
        "executed": 0,
        "safe_sql": 0,
        "correct": 0,
        "failures": [],
    }
    timeout = httpx.Timeout(90.0, connect=10.0)
    local_env = dotenv_values(PROJECT_ROOT / ".env")
    email = local_env.get("APP_ADMIN_EMAIL")
    password = local_env.get("APP_ADMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError("评测需要在 .env 中配置 APP_ADMIN_EMAIL 和 APP_ADMIN_PASSWORD")
    base_url = endpoint.removesuffix("/api/query")
    async with httpx.AsyncClient(timeout=timeout) as client:
        login = await client.post(
            f"{base_url}/api/workbench/auth/login",
            json={"email": email, "password": password},
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        for case in cases:
            response = await client.post(
                endpoint, json={"query": case["query"]}, headers=headers
            )
            response.raise_for_status()
            events = parse_sse_events(response.text)
            result_events = [event for event in events if event.get("type") == "result"]
            if not result_events:
                public_events = [
                    {
                        key: event[key]
                        for key in ("type", "step", "status", "message")
                        if key in event
                    }
                    for event in events
                    if event.get("type") in {"error", "clarification", "progress"}
                    and (event.get("type") != "progress" or event.get("status") == "error")
                ]
                metrics["failures"].append(
                    {
                        "query": case["query"],
                        "kind": "no_result_event",
                        "events": public_events,
                        "event_types": [event.get("type") for event in events],
                    }
                )
                continue
            metrics["executed"] += 1
            result = result_events[-1]
            try:
                normalize_read_query(result.get("sql", ""))
            except SQLPolicyError:
                pass
            else:
                metrics["safe_sql"] += 1
            expected = case.get("expected_rows", case.get("expected_values", []))
            if result_matches(expected, result.get("data")):
                metrics["correct"] += 1
            else:
                metrics["failures"].append(
                    {
                        "query": case["query"],
                        "expected": expected,
                        "actual_rows": normalized_rows(result.get("data")),
                        "sql": result.get("sql", ""),
                    }
                )
    total = metrics["total"]
    metrics["execution_rate"] = metrics["executed"] / total if total else 0
    metrics["safe_sql_rate"] = metrics["safe_sql"] / total if total else 0
    metrics["result_accuracy"] = metrics["correct"] / total if total else 0
    return metrics


def write_report(metrics: dict, path: Path) -> None:
    """Persist one evaluation run so repeated-model results can be compared later."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help="Authenticated legacy NL2SQL endpoint to evaluate.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        help="Optional local JSON path for this evaluation run.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Zero-based starting position in the fixed case list.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of cases to run from the selected offset.",
    )
    args = parser.parse_args()
    try:
        metrics = asyncio.run(evaluate(args.endpoint, offset=args.offset, limit=args.limit))
    except ValueError as exc:
        parser.error(str(exc))
    print(
        "sql_cases={total} executed={executed} execution_rate={execution_rate:.2%} "
        "safe_sql={safe_sql} safe_sql_rate={safe_sql_rate:.2%} correct={correct} "
        "result_accuracy={result_accuracy:.2%}".format(**metrics)
    )
    for failure in metrics["failures"]:
        print(f"sql_failure={json.dumps(failure, ensure_ascii=False)}")
    if args.report_path:
        write_report(metrics, args.report_path)
        print(f"report_path={args.report_path}")


if __name__ == "__main__":
    main()
