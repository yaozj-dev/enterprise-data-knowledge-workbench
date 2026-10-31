"""Run a bounded local concurrency benchmark against the workbench SSE API."""

import argparse
import asyncio
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

import aiohttp


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return round(ordered[index], 2)


async def login(
    session: aiohttp.ClientSession, base_url: str, email: str, password: str
) -> str:
    async with session.post(
        f"{base_url}/api/workbench/auth/login",
        json={"email": email, "password": password},
    ) as response:
        response.raise_for_status()
        payload = await response.json()
        return str(payload["access_token"])


async def resolve_conversation(
    session: aiohttp.ClientSession, base_url: str, token: str
) -> str:
    async with session.get(
        f"{base_url}/api/workbench/conversations",
        headers={"Authorization": f"Bearer {token}"},
    ) as response:
        response.raise_for_status()
        payload = await response.json()
        if not payload.get("items"):
            raise RuntimeError("No conversation exists for this account")
        return str(payload["items"][0]["id"])


async def run_one(
    session: aiohttp.ClientSession,
    base_url: str,
    token: str,
    conversation_id: str,
    query: str,
    mode: str,
    timeout_seconds: float,
) -> dict:
    started = time.perf_counter()
    result = {
        "ok": False,
        "status": None,
        "error": None,
        "first_event_ms": None,
        "total_ms": None,
        "event_count": 0,
        "terminal": None,
    }
    first_event_at = None
    try:
        async with session.post(
            f"{base_url}/api/workbench/conversations/{conversation_id}/query",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
            json={"query": query, "mode": mode},
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            result["status"] = response.status
            response.raise_for_status()
            buffer = ""
            async for raw, _ in response.content.iter_chunks():
                if first_event_at is None:
                    first_event_at = time.perf_counter()
                buffer += raw.decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    chunk, buffer = buffer.split("\n\n", 1)
                    for line in chunk.splitlines():
                        if not line.startswith("data:"):
                            continue
                        result["event_count"] += 1
                        try:
                            payload = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        event_type = payload.get("type")
                        if event_type in {
                            "answer",
                            "result",
                            "clarification",
                            "done",
                            "error",
                        }:
                            result["terminal"] = event_type
            result["ok"] = result["terminal"] not in {None, "error"}
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        now = time.perf_counter()
        result["total_ms"] = round((now - started) * 1000, 2)
        result["first_event_ms"] = (
            round((first_event_at - started) * 1000, 2)
            if first_event_at is not None
            else None
        )
    return result


async def benchmark(args: argparse.Namespace) -> dict:
    connector = aiohttp.TCPConnector(limit=args.workers + 8)
    timeout = aiohttp.ClientTimeout(total=args.timeout_seconds + 10)
    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout
    ) as session:
        token = await login(session, args.base_url, args.email, args.password)
        conversation_id = await resolve_conversation(
            session, args.base_url, token
        )

        semaphore = asyncio.Semaphore(args.workers)

        async def worker(index: int) -> dict:
            async with semaphore:
                outcome = await run_one(
                    session,
                    args.base_url,
                    token,
                    conversation_id,
                    args.query,
                    args.mode,
                    args.timeout_seconds,
                )
                outcome["index"] = index
                return outcome

        results = []
        for wave in range(args.waves):
            wave_started = time.perf_counter()
            outcomes = await asyncio.gather(
                *(
                    worker(wave * args.workers + index)
                    for index in range(args.workers)
                )
            )
            results.extend(outcomes)
            successful = sum(item["ok"] for item in outcomes)
            wall_ms = round((time.perf_counter() - wave_started) * 1000, 2)
            print(
                f"wave={wave + 1}/{args.waves} "
                f"ok={successful}/{args.workers} wall_ms={wall_ms}"
            )

    successful = [item for item in results if item["ok"]]
    failed = [item for item in results if not item["ok"]]
    first_events = [
        float(item["first_event_ms"])
        for item in successful
        if item["first_event_ms"] is not None
    ]
    totals = [
        float(item["total_ms"])
        for item in successful
        if item["total_ms"] is not None
    ]
    terminals = Counter(item["terminal"] for item in results)
    errors = Counter(
        item["error"].split(":", 1)[0]
        if item["error"]
        else f"HTTP {item['status']}"
        for item in failed
    )
    by_wave = defaultdict(list)
    for item in results:
        by_wave[(item["index"] // args.workers) + 1].append(item)

    def wave_metric(items: list[dict], ratio: float) -> float:
        values = [
            float(item["total_ms"])
            for item in items
            if item["ok"] and item["total_ms"] is not None
        ]
        return percentile(values, ratio)

    return {
        "base_url": args.base_url,
        "mode": args.mode,
        "workers": args.workers,
        "waves": args.waves,
        "total_requests": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "success_rate": (
            round(len(successful) / len(results), 4) if results else 0
        ),
        "terminal_events": dict(terminals),
        "ttfe_ms": {
            "p50": percentile(first_events, 0.50),
            "p95": percentile(first_events, 0.95),
        },
        "total_ms": {
            "p50": percentile(totals, 0.50),
            "p95": percentile(totals, 0.95),
        },
        "errors": dict(errors),
        "wave_details": {
            str(wave): {
                "requests": len(items),
                "successful": sum(item["ok"] for item in items),
                "p50_total_ms": wave_metric(items, 0.50),
                "p95_total_ms": wave_metric(items, 0.95),
            }
            for wave, items in by_wave.items()
        },
        "guardrail": "Local benchmark only; not a production capacity claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark workbench SSE concurrency"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("BENCHMARK_BASE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--email", default=os.getenv("BENCHMARK_EMAIL", ""))
    parser.add_argument(
        "--password", default=os.getenv("BENCHMARK_PASSWORD", "")
    )
    parser.add_argument("--query", default="你好")
    parser.add_argument(
        "--mode",
        default="general",
        choices=["auto", "data", "knowledge", "general", "composite"],
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--waves", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=60)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=Path("logs/benchmark-concurrency.json"),
    )
    args = parser.parse_args()
    if not args.email or not args.password:
        parser.error("--email and --password are required")
    report = asyncio.run(benchmark(args))
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        key: report[key]
        for key in ("total_requests", "successful", "failed", "ttfe_ms", "total_ms")
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
