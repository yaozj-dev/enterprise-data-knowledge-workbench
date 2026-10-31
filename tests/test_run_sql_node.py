import unittest
from types import SimpleNamespace

from app.agent.nodes.run_sql import run_sql


class FakeDWRepository:
    async def run(self, sql: str) -> list[dict]:
        self.sql = sql
        return [{"sales": 100.0}]


class RunSQLNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_event_contains_the_executed_statement_and_data(self):
        events: list[dict] = []
        repository = FakeDWRepository()
        runtime = SimpleNamespace(
            stream_writer=events.append,
            context={"dw_mysql_repository": repository},
        )

        await run_sql({"sql": "SELECT 100 AS sales"}, runtime)

        self.assertEqual(repository.sql, "SELECT 100 AS sales")
        self.assertEqual(events[-1], {
            "type": "result",
            "data": [{"sales": 100.0}],
            "sql": "SELECT 100 AS sales",
        })


if __name__ == "__main__":
    unittest.main()
