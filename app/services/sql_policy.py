"""Conservative read-only policy for model-generated analytics SQL."""

import os
import re


class SQLPolicyError(ValueError):
    """Raised when generated SQL is outside the analytics read-only boundary."""


_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "replace", "grant", "revoke", "call", "set", "load", "outfile",
    "dumpfile", "handler", "prepare", "execute", "shutdown",
)
_TABLE_PATTERN = re.compile(
    r"\b(?:from|join)\s+(`[^`]+`|[a-zA-Z_][\w$]*(?:\.[a-zA-Z_][\w$]*)?)",
    re.IGNORECASE,
)
_CTE_PATTERN = re.compile(r"\bwith\s+([a-zA-Z_][\w$]*)\s+as\s*\(", re.IGNORECASE)


def _allowed_tables() -> set[str]:
    configured = os.getenv("SQL_ALLOWED_TABLES", "")
    if configured.strip():
        return {item.strip().lower() for item in configured.split(",") if item.strip()}
    return {"fact_order", "dim_region", "dim_customer", "dim_product", "dim_date"}


def normalize_read_query(sql: str) -> str:
    """Normalize one model response and reject anything that is not read-only SQL."""

    normalized = sql.strip()
    if not normalized:
        raise SQLPolicyError("模型没有生成 SQL")
    if "```" in normalized or "--" in normalized or "/*" in normalized or "#" in normalized:
        raise SQLPolicyError("SQL 不允许包含 Markdown 或注释")
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    if ";" in normalized:
        raise SQLPolicyError("只允许执行一条 SQL 语句")
    if not re.match(r"^(?:select|with)\b", normalized, re.IGNORECASE):
        raise SQLPolicyError("只允许执行 SELECT 查询")

    keyword_pattern = r"\b(?:" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b"
    forbidden = re.search(keyword_pattern, normalized, re.IGNORECASE)
    if forbidden:
        raise SQLPolicyError(f"SQL 包含被禁止的关键字：{forbidden.group(0).upper()}")
    if re.search(r"\b(?:for\s+update|lock\s+in\s+share\s+mode|sleep\s*\()", normalized, re.IGNORECASE):
        raise SQLPolicyError("SQL 不允许锁定数据或调用延时函数")

    cte_names = {match.lower() for match in _CTE_PATTERN.findall(normalized)}
    allowed_tables = _allowed_tables()
    for match in _TABLE_PATTERN.finditer(normalized):
        raw_name = match.group(1).strip("`").lower()
        table_name = raw_name.rsplit(".", 1)[-1]
        if table_name in cte_names:
            continue
        if table_name not in allowed_tables:
            raise SQLPolicyError(f"数据表不在允许范围内：{table_name}")
    return normalized


def bounded_read_query(sql: str, max_rows: int) -> str:
    """Add a hard result cap while preserving a model-provided smaller LIMIT."""

    normalized = normalize_read_query(sql)
    limit_match = re.search(r"\blimit\s+(\d+)(?:\s*,\s*\d+)?", normalized, re.IGNORECASE)
    if limit_match and int(limit_match.group(1)) <= max_rows:
        return normalized
    if limit_match:
        return f"SELECT * FROM ({normalized}) AS _agent_result LIMIT {max_rows}"
    return f"{normalized}\nLIMIT {max_rows}"
