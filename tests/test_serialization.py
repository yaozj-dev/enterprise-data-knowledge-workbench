import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.core.serialization import json_default
from app.services.workbench_service import event


def test_json_default_preserves_database_numbers_and_common_values():
    payload = {
        "amount": Decimal("1234.50"),
        "count": Decimal("4"),
        "date": date(2026, 10, 23),
        "timestamp": datetime(2026, 10, 23, 12, 30),
        "id": UUID("00000000-0000-0000-0000-000000000001"),
    }

    encoded = json.dumps(payload, default=json_default)
    decoded = json.loads(encoded)

    assert decoded == {
        "amount": 1234.5,
        "count": 4,
        "date": "2026-10-23",
        "timestamp": "2026-10-23T12:30:00",
        "id": "00000000-0000-0000-0000-000000000001",
    }


def test_sse_event_serializes_decimal_results():
    payload = json.loads(
        event({"type": "result", "data": [{"销售额": Decimal("70202.00")}]})[6:].strip()
    )

    assert payload["data"] == [{"销售额": 70202}]
