import unittest
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from main import app


class LegacyQueryRouterTests(unittest.TestCase):
    def test_legacy_query_endpoint_rejects_missing_bearer_token(self):
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def no_external_lifespan(_app):
            yield

        app.router.lifespan_context = no_external_lifespan
        try:
            with TestClient(app) as client:
                response = client.post("/api/query", json={"query": "统计销售额"})
        finally:
            app.router.lifespan_context = original_lifespan

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "请先登录")
        self.assertTrue(response.headers["X-Request-ID"])


if __name__ == "__main__":
    unittest.main()
