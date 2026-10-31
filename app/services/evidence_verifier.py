"""Program-first verification for composite Agent reports.

The report model is useful for explaining evidence, but it is not a trusted
authority.  This module keeps the cheap, deterministic checks independent from
any provider SDK so they can run in tests and in a degraded deployment.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

VerificationDecision = Literal["pass", "revise", "block"]

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
# Chinese text is a valid boundary for a business number.  Using ``\w`` here
# would treat Chinese characters as word characters and split ``1月109030.5``
# into fragments such as ``5``.  Only exclude ASCII identifier characters so
# decimals and numbers adjacent to Chinese labels remain intact.
_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9_])"
)
_CHINESE_FACT_NUMBER_PATTERN = re.compile(
    r"([零〇一二两三四五六七八九十百千万亿]+)"
    r"(?=(?:个|天|日|分钟|小时|年|月|季度|工作日|项|条|次|级|人|元|岁|名|张|份|周|轮|步|种|类|件))"
)
_LIST_MARKER_PATTERN = re.compile(r"(?m)^\s*\d{1,3}[.)、]\s*")
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1_000, "万": 10_000, "亿": 100_000_000}


@dataclass(frozen=True)
class ProgrammaticVerification:
    decision: VerificationDecision
    reasons: tuple[str, ...] = ()


def _number_key(value: str) -> str:
    """Normalize display formatting without treating years as report metrics."""

    normalized = value.replace(",", "").strip()
    suffix = "%" if normalized.endswith("%") else ""
    normalized = normalized.removesuffix("%")
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        return value
    if number == number.to_integral_value():
        return f"{number.to_integral_value()}{suffix}"
    # Decimal avoids silently rounding large monetary values or meaningful
    # fractional amounts while still removing insignificant trailing zeroes.
    return f"{number.normalize():f}{suffix}"


def _chinese_number_key(value: str) -> str:
    """Normalize the bounded Chinese number forms used in policy evidence."""

    total = 0
    section = 0
    current = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
            continue
        unit = _CHINESE_UNITS[char]
        if unit < 10_000:
            section += (current or 1) * unit
            current = 0
        else:
            section = (section + current) * unit
            total += section
            section = 0
            current = 0
    return str(total + section + current)


def _numbers(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (dict, list, tuple)):
        return {item for child in value for item in _numbers(child)} if isinstance(value, (list, tuple)) else {
            item for child in value.values() for item in _numbers(child)
        }
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {_number_key(str(value))}
    numbers = {
        _number_key(token)
        for token in _NUMBER_PATTERN.findall(str(value))
        # Four-digit years commonly occur in a report title or time range and
        # are not expected to be present in an aggregate result row.
        if not (len(token.replace(",", "").replace("+", "").replace("-", "")) == 4
                and token.replace(",", "").replace("+", "").replace("-", "").isdigit())
    }
    numbers.update(
        _chinese_number_key(token)
        for token in _CHINESE_FACT_NUMBER_PATTERN.findall(str(value))
    )
    return numbers


def citation_indices(answer: str) -> set[int]:
    return {int(value) for value in _CITATION_PATTERN.findall(answer)}


def verify_report_programmatically(
    answer: str,
    *,
    result: Any,
    citations: list[dict[str, Any]],
    data_succeeded: bool,
    knowledge_succeeded: bool,
    document_evidence: Any = None,
) -> ProgrammaticVerification:
    """Check hard evidence invariants before consulting a Verifier LLM."""

    reasons: list[str] = []
    if result is not None and not data_succeeded:
        reasons.append("SQL execution did not complete successfully")
    if result is None and not knowledge_succeeded:
        reasons.append("no verified data or document evidence is available")

    if citations:
        valid_indices = {int(item.get("index")) for item in citations if item.get("index") is not None}
        references = citation_indices(answer)
        if references - valid_indices:
            reasons.append("answer contains an unknown citation index")
        elif not references:
            reasons.append("document answer has no citation reference")

    # Citation markers are identifiers, not factual numeric claims.
    answer_without_citations = _CITATION_PATTERN.sub("", answer)
    # Markdown list labels are presentation syntax, not factual claims.
    answer_numbers = _numbers(_LIST_MARKER_PATTERN.sub("", answer_without_citations))
    # A composite answer legitimately contains numbers from two evidence
    # domains: SQL metrics and policy facts such as response windows.  Compare
    # the claim against both raw evidence sources instead of treating every
    # number as a SQL metric.
    evidence_numbers = _numbers(result) | _numbers(document_evidence)
    # Only reject numbers that look like factual metrics.  Answers without a
    # numeric claim are safe to pass; a mismatch asks the model for one repair.
    if answer_numbers and evidence_numbers and not answer_numbers.issubset(evidence_numbers):
        reasons.append("numeric claims are not present in the structured result")

    if any(reason in reasons for reason in ("SQL execution did not complete successfully", "no verified data or document evidence is available", "answer contains an unknown citation index")):
        return ProgrammaticVerification("block", tuple(reasons))
    if reasons:
        return ProgrammaticVerification("revise", tuple(reasons))
    return ProgrammaticVerification("pass")


def parse_verifier_decision(value: Any) -> tuple[VerificationDecision, float] | None:
    """Parse only the small public decision contract from a verifier model."""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    match = re.search(r"\{.*?\}", text, flags=re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            payload = {}
        decision = payload.get("decision")
        confidence = payload.get("confidence", 0.0)
        if decision in {"pass", "revise", "block"} and isinstance(confidence, (int, float)):
            return decision, max(0.0, min(1.0, float(confidence)))
    lowered = text.lower()
    for decision in ("block", "revise", "pass"):
        if re.search(rf"\b{decision}\b", lowered):
            return decision, 0.5
    return None
