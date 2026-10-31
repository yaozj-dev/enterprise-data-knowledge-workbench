import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.log import json_log_format


class StructuredLoggingTests(unittest.TestCase):
    def test_json_log_format_preserves_request_and_operational_fields(self):
        rendered = json_log_format(
            {
                "time": datetime(2026, 10, 19, 9, 0, tzinfo=UTC),
                "level": SimpleNamespace(name="INFO"),
                "message": "HTTP request completed",
                "name": "app.main",
                "function": "add_request_id",
                "line": 42,
                "extra": {
                    "request_id": "request-123",
                    "event": "http_request_completed",
                    "status_code": 200,
                },
                "exception": None,
            }
        )

        payload = json.loads(rendered)
        self.assertEqual(payload["request_id"], "request-123")
        self.assertEqual(payload["extra"]["status_code"], 200)
        self.assertEqual(payload["extra"]["event"], "http_request_completed")
        self.assertNotIn("request_id", payload["extra"])
