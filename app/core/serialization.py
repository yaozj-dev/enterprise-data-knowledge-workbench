"""JSON conversion for values returned by databases and service boundaries."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID


def json_default(value: Any) -> Any:
    """Convert common application values without leaking database-only types."""

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, set):
        return sorted(value, key=str)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
