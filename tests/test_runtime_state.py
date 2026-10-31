import time
import unittest
from unittest.mock import patch

from app.services.runtime_state import RuntimeStateStore


class _UnavailableRedis:
    ping_count = 0

    @classmethod
    def from_url(cls, *_args, **_kwargs):
        return cls()

    async def ping(self):
        type(self).ping_count += 1
        raise OSError("redis is unavailable")

    async def close(self):
        return None


class RuntimeStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_redis_fails_open_without_repeating_probe(self):
        _UnavailableRedis.ping_count = 0
        store = RuntimeStateStore(
            redis_url="redis://unavailable:6379/0",
            redis_timeout_seconds=0.1,
            redis_failure_cooldown_seconds=30,
        )
        with patch("redis.asyncio.Redis", _UnavailableRedis):
            started = time.perf_counter()
            await store.append_event(7, "conversation", "run", {"type": "started"})
            await store.append_event(7, "conversation", "run", {"type": "done"})
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 1.0)
        self.assertEqual(_UnavailableRedis.ping_count, 1)
        run = await store.get_run(7, "conversation", "run")
        self.assertEqual([item["type"] for item in run["events"]], ["started", "done"])
        self.assertEqual(run["source"], "in_process")
        self.assertTrue(run["degraded"])
        notice = store.consume_degradation_notice()
        self.assertEqual(notice["reason"], "REDIS_UNAVAILABLE")
        self.assertIsNone(store.consume_degradation_notice())

    async def test_runtime_events_drop_answer_and_query_payloads(self):
        store = RuntimeStateStore(redis_url="")
        await store.append_event(
            7,
            "conversation",
            "run-safe",
            {
                "type": "answer",
                "content": "用户的敏感回答",
                "sql": "SELECT secret_column FROM private_table",
                "agent_name": "data",
                "status": "succeeded",
            },
        )

        event = (await store.get_run(7, "conversation", "run-safe"))["events"][0]
        self.assertEqual(event["type"], "answer")
        self.assertEqual(event["agent_name"], "data")
        self.assertNotIn("content", event)
        self.assertNotIn("sql", event)


if __name__ == "__main__":
    unittest.main()
