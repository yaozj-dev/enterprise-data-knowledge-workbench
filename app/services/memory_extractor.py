"""Conservative extraction of explicit user memory requests.

This is intentionally rule-based.  A model may suggest a candidate in a
future release, but only explicit user language can enter this first version,
and every candidate still requires confirmation before becoming durable.
"""

from __future__ import annotations

import re
from typing import TypedDict


class MemoryProposal(TypedDict):
    kind: str
    content: str


_EXPLICIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("preference", re.compile(r"^(?:请)?记住[：:，,]?\s*(.+)$", re.IGNORECASE)),
    ("preference", re.compile(r"^以后(?:请|都)?(?:按|使用|优先)?[：:，,]?\s*(.+)$", re.IGNORECASE)),
    ("preference", re.compile(r"^(?:我的)?偏好是[：:，,]?\s*(.+)$", re.IGNORECASE)),
    ("fact", re.compile(r"^(?:我的)?(?:姓名|职位|部门|时区)是[：:，,]?\s*(.+)$", re.IGNORECASE)),
)


def extract_explicit_memory(query: str) -> MemoryProposal | None:
    """Return one bounded proposal only when the user explicitly asks to save it."""

    normalized = " ".join(query.strip().split())
    if not normalized:
        return None
    for kind, pattern in _EXPLICIT_PATTERNS:
        match = pattern.match(normalized)
        if match:
            content = match.group(1).strip().rstrip("。.!！")
            if 2 <= len(content) <= 2_000:
                return {"kind": kind, "content": content}
    return None
