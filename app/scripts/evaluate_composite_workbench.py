"""Evaluate the bounded multi-Agent composite path.

The default mode is deterministic and offline: it verifies that reviewed
business questions produce a valid Supervisor plan with Data, Knowledge and
Report agents. ``--live`` additionally calls an already authenticated local
workbench and reports orchestration, evidence and latency outcomes. Live mode
does not create accounts, upload documents, or print credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
import uuid
from math import ceil
from pathlib import Path
from time import perf_counter
from typing import Any

PROJECT_ROOT = Path(__file__).parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.supervisor import build_execution_plan  # noqa: E402, I001
CASES_PATH = PROJECT_ROOT / "evals" / "composite_cases.jsonl"
DEFAULT_ENDPOINT = "http://127.0.0.1:8000"
REQUIRED_AGENTS = {"data", "knowledge", "report"}


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"找不到复合问题评测集：{path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def select_cases(
    cases: list[dict[str, Any]], *, offset: int = 0, limit: int | None = None
) -> list[dict[str, Any]]:
    if offset < 0:
        raise ValueError("offset cannot be negative")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if cases and offset >= len(cases):
        raise ValueError("offset is outside the case list")
    return cases[offset : offset + limit if limit is not None else None]


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(percentile / 100 * len(ordered)) - 1))
    return round(ordered[index], 2)


def _latency_metrics(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "p50_ms": _percentile(values, 50),
        "p95_ms": _percentile(values, 95),
        "average_ms": round(sum(values) / len(values), 2) if values else None,
    }


def evaluate_plan_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Check the server-owned plan contract without depending on an LLM."""

    records: list[dict[str, Any]] = []
    for case in cases:
        decision, plan = build_execution_plan(case["query"], "auto")
        task_map = {task.agent_name: task for task in plan.tasks}
        issues: list[str] = []
        if decision.intent != case.get("expected_intent", "composite"):
            issues.append(f"intent={decision.intent}")
        if set(task_map) != REQUIRED_AGENTS:
            issues.append(f"agents={sorted(task_map)}")
        report = task_map.get("report")
        if report is None or set(report.dependencies) != {"data-1", "knowledge-1"}:
            issues.append("report_dependencies_invalid")
        data = task_map.get("data")
        if data is None or set(data.allowed_tools) != {
            "data_catalog_lookup",
            "safe_readonly_query",
        }:
            issues.append("data_tool_boundary_invalid")
        knowledge = task_map.get("knowledge")
        if knowledge is None or set(knowledge.allowed_tools) != {
            "hybrid_document_search"
        }:
            issues.append("knowledge_tool_boundary_invalid")
        records.append(
            {
                "id": case.get("id", case["query"]),
                "query": case["query"],
                "intent": decision.intent,
                "confidence": decision.confidence,
                "passed": not issues,
                "issues": issues,
            }
        )

    total = len(records)
    passed = sum(record["passed"] for record in records)
    route_correct = sum(record["intent"] == "composite" for record in records)
    return {
        "mode": "plan_only",
        "total": total,
        "passed": passed,
        "plan_contract_rate": passed / total if total else 0.0,
        "composite_route_rate": route_correct / total if total else 0.0,
        "failures": [record for record in records if not record["passed"]],
    }


def parse_sse_event_line(line: str) -> dict[str, Any] | None:
    """Parse the one-line SSE events emitted by the FastAPI workbench."""

    if not line.startswith("data:"):
        return None
    try:
        payload = json.loads(line.removeprefix("data:").strip())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def score_live_events(
    case: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    first_event_ms: float | None,
    total_latency_ms: float,
) -> dict[str, Any]:
    """Score one live stream without inspecting hidden prompts or thoughts."""

    route = next((event for event in events if event.get("type") == "route"), {})
    route_intent = route.get("agent_intent", route.get("intent"))
    started_agents = {
        str(event.get("agent_name"))
        for event in events
        if event.get("type") == "agent_started" and event.get("agent_name")
    }
    completed_agents = {
        str(event.get("agent_name"))
        for event in events
        if event.get("type") == "agent_completed"
        and event.get("status") in {"succeeded", "partial"}
        and event.get("agent_name")
    }
    agent_statuses = {
        str(event.get("agent_name")): str(event.get("status"))
        for event in events
        if event.get("type") == "agent_completed" and event.get("agent_name")
    }
    agent_latencies = {
        str(event.get("agent_name")): event.get("latency_ms")
        for event in events
        if event.get("type") == "agent_completed" and event.get("agent_name")
    }
    verification_events = [
        event
        for event in events
        if event.get("type") == "tool_completed"
        and event.get("tool_name") == "verify_evidence"
    ]
    verification = verification_events[-1] if verification_events else {}
    done = next((event for event in reversed(events) if event.get("type") == "done"), {})
    answer = next((event for event in reversed(events) if event.get("type") == "answer"), {})
    failure_events = [
        {
            key: event[key]
            for key in ("type", "status", "message", "agent_name", "tool_name")
            if key in event
        }
        for event in events
        if event.get("type") in {"error", "clarification", "partial_result"}
    ]
    data_evidence = any(event.get("type") == "result" for event in events)
    citations = answer.get("citations") if isinstance(answer.get("citations"), list) else []
    orchestration_ok = REQUIRED_AGENTS.issubset(started_agents)
    terminal_status = done.get("execution_status", "missing")
    return {
        "id": case.get("id", case["query"]),
        "query": case["query"],
        "route_intent": route_intent,
        "started_agents": sorted(started_agents),
        "completed_agents": sorted(completed_agents),
        "agent_statuses": agent_statuses,
        "agent_latencies_ms": agent_latencies,
        "verification_decision": verification.get("decision"),
        "verification_reasons": verification.get("reasons", []),
        "execution_status": terminal_status,
        "orchestration_ok": orchestration_ok,
        "data_evidence": data_evidence,
        "citation_count": len(citations),
        "first_event_ms": round(first_event_ms, 2) if first_event_ms is not None else None,
        "total_latency_ms": round(total_latency_ms, 2),
        "fully_completed": terminal_status == "completed" and orchestration_ok,
        "evidence_backed": data_evidence and bool(citations),
        "failure_events": failure_events,
        "answer_preview": str(answer.get("content", ""))[:1_000],
    }


def summarize_live_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    first_event_latencies = [
        float(record["first_event_ms"])
        for record in records
        if record["first_event_ms"] is not None
    ]
    total_latencies = [float(record["total_latency_ms"]) for record in records]
    return {
        "mode": "live",
        "total": total,
        "fully_completed": sum(record["fully_completed"] for record in records),
        "partial": sum(record["execution_status"] == "partial" for record in records),
        "failed": sum(record["execution_status"] == "failed" for record in records),
        "fully_completed_rate": (
            sum(record["fully_completed"] for record in records) / total if total else 0.0
        ),
        "orchestration_contract_rate": (
            sum(record["orchestration_ok"] for record in records) / total if total else 0.0
        ),
        "evidence_backed_rate": (
            sum(record["evidence_backed"] for record in records) / total if total else 0.0
        ),
        "time_to_first_event": _latency_metrics(first_event_latencies),
        "total_latency": _latency_metrics(total_latencies),
        "records": records,
    }


async def evaluate_live(
    cases: list[dict[str, Any]],
    *,
    endpoint: str,
    access_token: str,
    conversation_id: str,
) -> dict[str, Any]:
    """Run the reviewed cases against an existing authenticated conversation."""

    import httpx

    url = (
        f"{endpoint.rstrip('/')}/api/workbench/conversations/"
        f"{conversation_id}/query"
    )
    records: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "text/event-stream"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        for case in cases:
            started_at = perf_counter()
            first_event_ms: float | None = None
            events: list[dict[str, Any]] = []
            request_error: str | None = None
            try:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json={"query": case["query"], "mode": "composite"},
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        event = parse_sse_event_line(line)
                        if event is None:
                            continue
                        if first_event_ms is None:
                            first_event_ms = (perf_counter() - started_at) * 1_000
                        events.append(event)
            except httpx.TimeoutException:
                request_error = "timeout"
            except httpx.HTTPStatusError as exc:
                request_error = f"http_{exc.response.status_code}"
            except httpx.RequestError:
                request_error = "connection_error"
            if request_error:
                events.append({"type": "error", "message": request_error})
            record = score_live_events(
                case,
                events,
                first_event_ms=first_event_ms,
                total_latency_ms=(perf_counter() - started_at) * 1_000,
            )
            if request_error:
                record["request_error"] = request_error
            records.append(record)
    return summarize_live_records(records)


async def evaluate_provisioned_live(
    cases: list[dict[str, Any]],
    *,
    endpoint: str,
    admin_email: str,
    admin_password: str,
) -> dict[str, Any]:
    """Run live cases with temporary data and remove it in a finally block."""

    import httpx

    base_url = endpoint.rstrip("/")
    temporary_user_id: int | None = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
        admin_login = await client.post(
            f"{base_url}/api/workbench/auth/login",
            json={"email": admin_email, "password": admin_password},
        )
        admin_login.raise_for_status()
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        temporary_email = f"composite-eval-{uuid.uuid4().hex}@workbench.local"
        temporary_password = secrets.token_urlsafe(32)
        try:
            created = await client.post(
                f"{base_url}/api/workbench/admin/users",
                headers=admin_headers,
                json={
                    "email": temporary_email,
                    "display_name": "Temporary Composite Evaluation",
                    "password": temporary_password,
                    "role": "user",
                },
            )
            created.raise_for_status()
            temporary_user_id = int(created.json()["id"])
            user_login = await client.post(
                f"{base_url}/api/workbench/auth/login",
                json={"email": temporary_email, "password": temporary_password},
            )
            user_login.raise_for_status()
            user_token = user_login.json()["access_token"]
            user_headers = {"Authorization": f"Bearer {user_token}"}

            corpus_path = PROJECT_ROOT / "evals" / "document_corpus.jsonl"
            for line in corpus_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                document = json.loads(line)
                filename = Path(str(document["filename"])).name
                content = f"# {Path(filename).stem}\n\n{document['content']}\n"
                upload = await client.post(
                    f"{base_url}/api/workbench/documents",
                    headers=user_headers,
                    files={"file": (filename, content.encode("utf-8"), "text/markdown")},
                )
                upload.raise_for_status()

            conversation = await client.post(
                f"{base_url}/api/workbench/conversations",
                headers=user_headers,
                json={"title": "Temporary composite evaluation"},
            )
            conversation.raise_for_status()
            return await evaluate_live(
                cases,
                endpoint=base_url,
                access_token=user_token,
                conversation_id=conversation.json()["id"],
            )
        finally:
            if temporary_user_id is not None:
                deleted = await client.delete(
                    f"{base_url}/api/workbench/admin/users/{temporary_user_id}",
                    headers=admin_headers,
                )
                if deleted.status_code != 200:
                    raise RuntimeError(
                        f"temporary evaluation user cleanup failed: HTTP {deleted.status_code}"
                    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate composite Agent plans or a live workbench")
    parser.add_argument("--cases-path", type=Path, default=CASES_PATH)
    parser.add_argument("--provision-live", action="store_true", help="Create and remove temporary evaluation data.")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--live", action="store_true", help="Call an existing local workbench API.")
    parser.add_argument("--endpoint", default=os.getenv("EVAL_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--access-token", default=os.getenv("EVAL_ACCESS_TOKEN", ""))
    parser.add_argument("--conversation-id", default=os.getenv("EVAL_CONVERSATION_ID", ""))
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    try:
        cases = select_cases(load_cases(args.cases_path), offset=args.offset, limit=args.limit)
    except ValueError as exc:
        parser.error(str(exc))
    if args.provision_live:
        from dotenv import dotenv_values

        local_env = dotenv_values(PROJECT_ROOT / ".env")
        admin_email = local_env.get("APP_ADMIN_EMAIL")
        admin_password = local_env.get("APP_ADMIN_PASSWORD")
        if not admin_email or not admin_password:
            parser.error("--provision-live requires APP_ADMIN_EMAIL and APP_ADMIN_PASSWORD in .env")
        report = asyncio.run(
            evaluate_provisioned_live(
                cases,
                endpoint=args.endpoint,
                admin_email=admin_email,
                admin_password=admin_password,
            )
        )
    elif args.live:
        if not args.access_token or not args.conversation_id:
            parser.error(
                "--live requires --access-token and --conversation-id, or EVAL_ACCESS_TOKEN and EVAL_CONVERSATION_ID"
            )
        report = asyncio.run(
            evaluate_live(
                cases,
                endpoint=args.endpoint,
                access_token=args.access_token,
                conversation_id=args.conversation_id,
            )
        )
    else:
        report = evaluate_plan_cases(cases)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
