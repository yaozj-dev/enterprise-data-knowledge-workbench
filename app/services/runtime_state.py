"""Fail-open short-lived Agent run state.

LangGraph owns the state of one graph execution.  This store is intentionally
separate: it keeps a bounded, user-scoped timeline that the frontend and a
future resume endpoint can inspect across HTTP requests.  Redis is optional in
local development; an in-memory implementation keeps the same interface when
the service or package is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


def state_key(user_id: int, conversation_id: str, run_id: str) -> str:
    return f"agent:{user_id}:{conversation_id}:{run_id}"


@dataclass
class _MemoryRecord:
    plan: dict[str, Any] | None = None
    events: deque[dict[str, Any]] = field(default_factory=deque)
    expires_at: float = 0.0


class RuntimeStateStore:
    """Redis-backed store with an in-process fallback and bounded writes."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        ttl_seconds: int = 3_600,
        max_events: int = 200,
        max_context_chars: int = 8_000,
        redis_timeout_seconds: float = 1.5,
        redis_failure_cooldown_seconds: float = 30.0,
    ):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "")
        self.ttl_seconds = max(60, ttl_seconds)
        self.max_events = max(10, max_events)
        self.max_context_chars = max(1_000, max_context_chars)
        self.redis_timeout_seconds = max(0.1, float(redis_timeout_seconds))
        self.redis_failure_cooldown_seconds = max(1.0, float(redis_failure_cooldown_seconds))
        self._redis: Any = None
        self._redis_retry_at = 0.0
        self._memory: dict[str, _MemoryRecord] = {}
        self._degraded_reason: str | None = None
        self._degradation_reported = False

    def _mark_degraded(self, reason: str) -> None:
        """Remember one safe operator-facing reason for the in-process fallback."""

        if self._degraded_reason != reason:
            self._degradation_reported = False
        self._degraded_reason = reason

    def consume_degradation_notice(self) -> dict[str, str] | None:
        """Return a one-shot notice suitable for SSE, without exposing internals."""

        if not self._degraded_reason or self._degradation_reported:
            return None
        self._degradation_reported = True
        return {
            "service": "redis",
            "status": "degraded",
            "fallback": "in_process",
            "reason": self._degraded_reason,
        }

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    def _memory_record(self, key: str) -> _MemoryRecord:
        now = time.monotonic()
        record = self._memory.get(key)
        if record is None or record.expires_at <= now:
            record = _MemoryRecord(expires_at=now + self.ttl_seconds)
            self._memory[key] = record
        else:
            record.expires_at = now + self.ttl_seconds
        return record

    async def _client(self) -> Any:
        if self._redis is not None:
            return self._redis
        if not self.redis_url:
            self._mark_degraded("REDIS_NOT_CONFIGURED")
            return None
        if time.monotonic() < self._redis_retry_at:
            return None
        client = None
        try:
            from redis.asyncio import Redis

            client = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=self.redis_timeout_seconds,
                socket_timeout=self.redis_timeout_seconds,
                retry_on_timeout=False,
            )
            async with asyncio.timeout(self.redis_timeout_seconds + 0.5):
                await client.ping()
            self._redis = client
            self._redis_retry_at = 0.0
            self._degraded_reason = None
            self._degradation_reported = False
        except Exception:
            # A missing Redis package or an unavailable local service must not
            # prevent the current answer from completing.
            self._redis = None
            self._redis_retry_at = time.monotonic() + self.redis_failure_cooldown_seconds
            self._mark_degraded("REDIS_UNAVAILABLE")
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass
        return self._redis

    @staticmethod
    def _bounded_json(value: Any, max_chars: int) -> str:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded) <= max_chars:
            return encoded
        return json.dumps({"truncated": True, "preview": encoded[: max_chars - 40]}, ensure_ascii=False)

    @staticmethod
    def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
        """Keep trajectory metadata while dropping user/model payloads."""

        allowed = {
            "type",
            "step",
            "status",
            "execution_status",
            "agent_name",
            "task_id",
            "tool_name",
            "summary",
            "latency_ms",
            "round",
            "service",
            "fallback",
            "reason",
            "error_code",
            "decision",
            "verification",
            "model_status",
            "model_confidence",
            "replan_count",
            "from_agent",
            "to_agent",
        }
        return {
            key: value
            for key, value in event.items()
            if key in allowed and isinstance(value, (str, int, float, bool))
        }

    async def save_plan(
        self,
        user_id: int,
        conversation_id: str,
        run_id: str,
        plan: dict[str, Any],
    ) -> None:
        key = state_key(user_id, conversation_id, run_id)
        client = await self._client()
        if client is not None:
            try:
                await client.hset(key, mapping={"plan": self._bounded_json(plan, self.max_context_chars)})
                await client.expire(key, self.ttl_seconds)
                return
            except Exception:
                self._redis = None
                self._redis_retry_at = time.monotonic() + self.redis_failure_cooldown_seconds
                self._mark_degraded("REDIS_WRITE_FAILED")
        record = self._memory_record(key)
        record.plan = json.loads(self._bounded_json(plan, self.max_context_chars))

    async def append_event(
        self,
        user_id: int,
        conversation_id: str,
        run_id: str,
        event: dict[str, Any],
    ) -> None:
        key = state_key(user_id, conversation_id, run_id)
        safe_event = json.loads(
            self._bounded_json(self._safe_event(event), self.max_context_chars)
        )
        client = await self._client()
        if client is not None:
            try:
                await client.rpush(f"{key}:events", json.dumps(safe_event, ensure_ascii=False))
                await client.ltrim(f"{key}:events", -self.max_events, -1)
                await client.expire(f"{key}:events", self.ttl_seconds)
                return
            except Exception:
                self._redis = None
                self._redis_retry_at = time.monotonic() + self.redis_failure_cooldown_seconds
                self._mark_degraded("REDIS_WRITE_FAILED")
        record = self._memory_record(key)
        record.events.append(safe_event)
        while len(record.events) > self.max_events:
            record.events.popleft()

    async def get_run(
        self, user_id: int, conversation_id: str, run_id: str
    ) -> dict[str, Any]:
        key = state_key(user_id, conversation_id, run_id)
        client = await self._client()
        if client is not None:
            try:
                values = await client.hgetall(key)
                events = await client.lrange(f"{key}:events", 0, -1)
                return {
                    "key": key,
                    "plan": json.loads(values["plan"]) if values.get("plan") else None,
                    "events": [json.loads(item) for item in events],
                    "source": "redis",
                    "degraded": False,
                }
            except Exception:
                self._redis = None
                self._redis_retry_at = time.monotonic() + self.redis_failure_cooldown_seconds
                self._mark_degraded("REDIS_READ_FAILED")
        record = self._memory.get(key)
        if record is None or record.expires_at <= time.monotonic():
            self._memory.pop(key, None)
            record = _MemoryRecord()
        return {
            "key": key,
            "plan": record.plan,
            "events": list(record.events),
            "source": "in_process",
            "degraded": bool(self._degraded_reason),
        }

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
        self._redis_retry_at = 0.0


runtime_state_store = RuntimeStateStore(
    ttl_seconds=RuntimeStateStore._env_int("REDIS_TTL_SECONDS", 3_600),
    max_events=RuntimeStateStore._env_int("REDIS_MAX_EVENTS", 200),
    redis_timeout_seconds=float(os.getenv("REDIS_TIMEOUT_SECONDS", "1.5")),
    redis_failure_cooldown_seconds=float(os.getenv("REDIS_FAILURE_COOLDOWN_SECONDS", "30")),
)

