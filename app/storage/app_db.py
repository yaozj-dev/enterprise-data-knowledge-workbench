"""Small local application database used by the personal workbench.

The existing MySQL databases remain dedicated to metadata and demo analytics.
This SQLite store contains application-owned identities, sessions and audit data
so the workbench can be started without changing the existing Docker volumes.
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from threading import RLock

PROJECT_ROOT = Path(__file__).parents[2]
_configured_db_path = Path(os.getenv("APP_DB_PATH", "data/workbench.db"))
DB_PATH = (
    _configured_db_path
    if _configured_db_path.is_absolute()
    else PROJECT_ROOT / _configured_db_path
)
_lock = RLock()

_AGENT_RUN_COLUMNS = (
    "id",
    "user_id",
    "conversation_id",
    "query_preview",
    "query_hash",
    "route",
    "route_strategy",
    "route_confidence",
    "policy_version",
    "status",
    "first_event_ms",
    "total_latency_ms",
    "retrieval_latency_ms",
    "model_latency_ms",
    "sql_latency_ms",
    "citation_count",
    "result_row_count",
    "error_code",
    "trace_summary",
    "parent_run_id",
    "started_at",
    "completed_at",
)
_AGENT_RUNS_MIGRATION_TABLE = "agent_runs__partial_status_migration"


@contextmanager
def _connect():
    """Open, commit or roll back, and always close one SQLite connection."""

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash a password with a per-user salt using the standard library."""

    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"pbkdf2_sha256$210000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _migrate_agent_runs_partial_status(connection: sqlite3.Connection) -> None:
    """Allow legacy trace tables to persist a partial multi-Agent outcome.

    SQLite cannot add a value to an existing CHECK constraint. The replacement
    table is built with foreign keys temporarily disabled so child trace and
    feedback tables remain intact, then checked before the transaction commits.
    """

    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agent_runs'"
    ).fetchone()
    table_sql = (row[0] if row else "").lower()
    if "'partial'" in table_sql or '"partial"' in table_sql:
        return

    legacy_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")
    }
    columns = ", ".join(column for column in _AGENT_RUN_COLUMNS if column in legacy_columns)
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"""
            CREATE TABLE {_AGENT_RUNS_MIGRATION_TABLE} (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
                query_preview TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                route TEXT,
                route_strategy TEXT,
                route_confidence REAL,
                policy_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'running', 'completed', 'partial', 'clarified', 'failed', 'cancelled', 'incomplete'
                )),
                first_event_ms INTEGER,
                total_latency_ms INTEGER,
                retrieval_latency_ms INTEGER,
                model_latency_ms INTEGER,
                sql_latency_ms INTEGER,
                citation_count INTEGER NOT NULL DEFAULT 0,
                result_row_count INTEGER,
                error_code TEXT,
                trace_summary TEXT,
                parent_run_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        connection.execute(
            f"INSERT INTO {_AGENT_RUNS_MIGRATION_TABLE} ({columns}) "
            f"SELECT {columns} FROM agent_runs"
        )
        connection.execute("DROP TABLE agent_runs")
        connection.execute(
            f"ALTER TABLE {_AGENT_RUNS_MIGRATION_TABLE} RENAME TO agent_runs"
        )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            details = ", ".join(
                f"{item[0]} rowid={item[1]} parent={item[2]}" for item in violations
            )
            raise RuntimeError(f"agent_runs migration foreign-key check failed: {details}")
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def init_db() -> None:
    """Create application tables and optionally bootstrap an administrator."""

    schema = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin', 'user')) DEFAULT 'user',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
        content TEXT NOT NULL,
        event_data TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        action TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        filename TEXT NOT NULL,
        content_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        content_hash TEXT,
        status TEXT NOT NULL CHECK(status IN ('processing', 'ready', 'failed')),
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS document_chunks (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        chunk_index INTEGER NOT NULL,
        page_number INTEGER,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(document_id, chunk_index)
    );
    CREATE TABLE IF NOT EXISTS agent_runs (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
        query_preview TEXT NOT NULL,
        query_hash TEXT NOT NULL,
        route TEXT,
        route_strategy TEXT,
        route_confidence REAL,
        policy_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'partial', 'clarified', 'failed', 'cancelled', 'incomplete')),
        first_event_ms INTEGER,
        total_latency_ms INTEGER,
        retrieval_latency_ms INTEGER,
        model_latency_ms INTEGER,
        sql_latency_ms INTEGER,
        citation_count INTEGER NOT NULL DEFAULT 0,
        result_row_count INTEGER,
        error_code TEXT,
        trace_summary TEXT,
        parent_run_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS agent_run_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        step_name TEXT NOT NULL,
        category TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('success', 'error', 'cancelled')),
        started_offset_ms INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        metadata TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS agent_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        rating INTEGER NOT NULL CHECK(rating IN (-1, 1)),
        issue_type TEXT,
        comment TEXT,
        review_status TEXT NOT NULL CHECK(review_status IN ('pending', 'accepted', 'rejected')) DEFAULT 'pending',
        expected_route TEXT,
        reviewer_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        reviewer_note TEXT,
        reviewed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(run_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS regression_cases (
        id TEXT PRIMARY KEY,
        feedback_id INTEGER NOT NULL UNIQUE REFERENCES agent_feedback(id) ON DELETE CASCADE,
        query TEXT NOT NULL,
        expected_route TEXT NOT NULL CHECK(expected_route IN ('data', 'document', 'general', 'clarify')),
        source_issue_type TEXT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL
    );
    """
    with _lock, _connect() as connection:
        connection.executescript(schema)
        _migrate_agent_runs_partial_status(connection)
        agent_run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")
        }
        if "parent_run_id" not in agent_run_columns:
            connection.execute(
                "ALTER TABLE agent_runs ADD COLUMN parent_run_id TEXT "
                "REFERENCES agent_runs(id) ON DELETE SET NULL"
            )
        # Existing local databases need this additive migration before duplicate
        # detection can use a stable content fingerprint.
        document_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(documents)")
        }
        if "content_hash" not in document_columns:
            connection.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_active_content_hash "
            "ON documents(user_id, content_hash) "
            "WHERE content_hash IS NOT NULL AND status IN ('processing', 'ready')"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at ON agent_runs(started_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_user_created ON agent_runs(user_id, started_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run ON agent_run_steps(run_id, sequence)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_feedback_status ON agent_feedback(review_status, created_at DESC)"
        )
        email = os.getenv("APP_ADMIN_EMAIL", "").strip().lower()
        password = os.getenv("APP_ADMIN_PASSWORD", "")
        if email and password:
            existing = connection.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO users(email, display_name, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, 'admin', ?)",
                    (email, "Workspace Admin", hash_password(password), utc_now()),
                )


def get_user_by_email(email: str) -> dict | None:
    with _lock, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
        return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    with _lock, _connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(email: str, display_name: str, password: str, role: str = "user") -> dict:
    now = utc_now()
    normalized_email = email.lower().strip()
    with _lock, _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO users(email, display_name, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (normalized_email, display_name.strip()[:80], hash_password(password), role, now),
        )
        user_id = int(cursor.lastrowid)
    return {
        "id": user_id,
        "email": normalized_email,
        "display_name": display_name.strip()[:80],
        "role": role,
    }


def list_users() -> list[dict]:
    with _lock, _connect() as connection:
        rows = connection.execute(
            "SELECT id, email, display_name, role, is_active, created_at "
            "FROM users ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]


def count_active_admins() -> int:
    with _lock, _connect() as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()[0]
        )


def delete_user(user_id: int) -> dict | None:
    """Delete one user and cascade application-owned conversations and documents."""

    with _lock, _connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return dict(row)


def create_conversation(user_id: int, title: str = "新会话") -> dict:
    conversation_id = secrets.token_urlsafe(18)
    now = utc_now()
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO conversations(id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, user_id, title[:120] or "新会话", now, now),
        )
    return {"id": conversation_id, "title": title[:120] or "新会话", "created_at": now, "updated_at": now}


def list_conversations(user_id: int) -> list[dict]:
    with _lock, _connect() as connection:
        rows = connection.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_conversation(conversation_id: str, user_id: int) -> dict | None:
    with _lock, _connect() as connection:
        row = connection.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def add_message(conversation_id: str, role: str, content: str, event_data: str | None = None) -> int:
    with _lock, _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO messages(conversation_id, role, content, event_data, created_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, content, event_data, utc_now()),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (utc_now(), conversation_id)
        )
        return int(cursor.lastrowid)


def list_messages(conversation_id: str, user_id: int) -> list[dict]:
    with _lock, _connect() as connection:
        rows = connection.execute(
            "SELECT m.id, m.role, m.content, m.event_data, m.created_at "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.conversation_id = ? AND c.user_id = ? ORDER BY m.id",
            (conversation_id, user_id),
        ).fetchall()
        return [dict(row) for row in rows]


def list_recent_messages(conversation_id: str, user_id: int, limit: int = 8) -> list[dict]:
    """Return a bounded chronological history only when the caller owns the chat."""

    limit = max(1, min(limit, 20))
    with _lock, _connect() as connection:
        rows = connection.execute(
            "SELECT m.id, m.role, m.content, m.created_at "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.conversation_id = ? AND c.user_id = ? "
            "ORDER BY m.id DESC LIMIT ?",
            (conversation_id, user_id, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]


def write_audit(user_id: int | None, action: str, detail: str = "") -> None:
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO audit_logs(user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (user_id, action, detail[:2000], utc_now()),
        )


def list_audit_logs(limit: int = 30, offset: int = 0, action: str | None = None) -> dict:
    """Return a bounded, admin-facing audit page without exposing password data."""

    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    normalized_action = (action or "").strip()
    where_clause = "WHERE a.action = ?" if normalized_action else ""
    parameters: tuple[object, ...] = (normalized_action,) if normalized_action else ()
    with _lock, _connect() as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM audit_logs a {where_clause}", parameters
            ).fetchone()[0]
        )
        rows = connection.execute(
            "SELECT a.id, a.user_id, u.email AS user_email, u.display_name AS user_display_name, "
            "a.action, a.detail, a.created_at "
            "FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id "
            f"{where_clause} ORDER BY a.id DESC LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def create_document(
    user_id: int,
    filename: str,
    content_type: str,
    size_bytes: int,
    content_hash: str,
) -> dict:
    document_id = secrets.token_urlsafe(18)
    now = utc_now()
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO documents(id, user_id, filename, content_type, size_bytes, content_hash, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?)",
            (
                document_id,
                user_id,
                filename[:255],
                content_type[:120],
                size_bytes,
                content_hash,
                now,
                now,
            ),
        )
    return {
        "id": document_id,
        "filename": filename[:255],
        "content_type": content_type[:120],
        "size_bytes": size_bytes,
        "status": "processing",
        "content_hash": content_hash,
        "created_at": now,
        "updated_at": now,
    }


def update_document_status(
    document_id: str, user_id: int, status: str, error_message: str = ""
) -> None:
    with _lock, _connect() as connection:
        connection.execute(
            "UPDATE documents SET status = ?, error_message = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (status, error_message[:2000], utc_now(), document_id, user_id),
        )


def get_document(document_id: str, user_id: int) -> dict | None:
    with _lock, _connect() as connection:
        row = connection.execute(
            "SELECT d.id, d.filename, d.content_type, d.size_bytes, d.status, d.error_message, "
            "d.created_at, d.updated_at, COUNT(c.id) AS chunk_count "
            "FROM documents d LEFT JOIN document_chunks c ON c.document_id = d.id "
            "WHERE d.id = ? AND d.user_id = ? GROUP BY d.id",
            (document_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def find_active_document_by_hash(user_id: int, content_hash: str) -> dict | None:
    with _lock, _connect() as connection:
        row = connection.execute(
            "SELECT id, filename, status, created_at FROM documents "
            "WHERE user_id = ? AND content_hash = ? AND status IN ('processing', 'ready')",
            (user_id, content_hash),
        ).fetchone()
        return dict(row) if row else None


def list_documents(user_id: int) -> list[dict]:
    with _lock, _connect() as connection:
        rows = connection.execute(
            "SELECT d.id, d.filename, d.content_type, d.size_bytes, d.status, d.error_message, "
            "d.created_at, d.updated_at, COUNT(c.id) AS chunk_count "
            "FROM documents d LEFT JOIN document_chunks c ON c.document_id = d.id "
            "WHERE d.user_id = ? GROUP BY d.id ORDER BY d.updated_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def add_document_chunk(
    document_id: str, chunk_index: int, page_number: int | None, content: str
) -> str:
    chunk_id = secrets.token_urlsafe(18)
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO document_chunks(id, document_id, chunk_index, page_number, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chunk_id, document_id, chunk_index, page_number, content, utc_now()),
        )
    return chunk_id


def list_document_chunks(document_id: str, user_id: int) -> list[dict]:
    with _lock, _connect() as connection:
        rows = connection.execute(
            "SELECT c.chunk_index, c.page_number, c.content "
            "FROM document_chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE c.document_id = ? AND d.user_id = ? ORDER BY c.chunk_index",
            (document_id, user_id),
        ).fetchall()
        return [dict(row) for row in rows]


def replace_document_chunks(
    document_id: str, user_id: int, chunks: list[dict]
) -> None:
    """Replace parsed chunks only after a successful owned-document reindex."""

    with _lock, _connect() as connection:
        owned = connection.execute(
            "SELECT 1 FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if owned is None:
            raise ValueError("文档不存在或无权操作")
        connection.execute(
            "DELETE FROM document_chunks WHERE document_id = ?", (document_id,)
        )
        now = utc_now()
        connection.executemany(
            "INSERT INTO document_chunks(id, document_id, chunk_index, page_number, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    secrets.token_urlsafe(18),
                    document_id,
                    index,
                    chunk.get("page_number"),
                    chunk["content"],
                    now,
                )
                for index, chunk in enumerate(chunks)
            ],
        )


def delete_document(document_id: str, user_id: int) -> dict | None:
    """Delete an owned document. Foreign keys remove its persisted chunks."""

    with _lock, _connect() as connection:
        row = connection.execute(
            "SELECT id, filename, content_type, size_bytes, status, error_message, "
            "created_at, updated_at FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "DELETE FROM documents WHERE id = ? AND user_id = ?", (document_id, user_id)
        )
        return dict(row)


def create_agent_run(
    user_id: int,
    conversation_id: str | None,
    query_preview: str,
    query_hash: str,
    policy_version: str,
    run_id: str | None = None,
    parent_run_id: str | None = None,
) -> dict:
    """Create a lightweight execution trace before an Agent stream starts."""

    run_id = run_id or secrets.token_urlsafe(18)
    started_at = utc_now()
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO agent_runs("
            "id, user_id, conversation_id, query_preview, query_hash, policy_version, "
            "status, parent_run_id, started_at"
            ") VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)",
            (
                run_id,
                user_id,
                conversation_id,
                query_preview[:800],
                query_hash[:128],
                policy_version[:80],
                parent_run_id,
                started_at,
            ),
        )
    return {"id": run_id, "started_at": started_at}


def update_agent_run_route(
    run_id: str,
    route: str,
    strategy: str,
    confidence: float | None,
    policy_version: str,
) -> None:
    with _lock, _connect() as connection:
        connection.execute(
            "UPDATE agent_runs SET route = ?, route_strategy = ?, route_confidence = ?, "
            "policy_version = ? WHERE id = ?",
            (route[:40], strategy[:80], confidence, policy_version[:80], run_id),
        )


def add_agent_run_step(
    run_id: str,
    sequence: int,
    step_name: str,
    category: str,
    status: str,
    started_offset_ms: int,
    duration_ms: int,
    metadata: str | None = None,
) -> None:
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO agent_run_steps("
            "run_id, sequence, step_name, category, status, started_offset_ms, duration_ms, metadata, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                sequence,
                step_name[:160],
                category[:40],
                status,
                max(0, int(started_offset_ms)),
                max(0, int(duration_ms)),
                metadata[:4_000] if metadata else None,
                utc_now(),
            ),
        )


def finish_agent_run(
    run_id: str,
    *,
    status: str,
    first_event_ms: int | None,
    total_latency_ms: int,
    retrieval_latency_ms: int = 0,
    model_latency_ms: int = 0,
    sql_latency_ms: int = 0,
    citation_count: int = 0,
    result_row_count: int | None = None,
    error_code: str | None = None,
    trace_summary: str | None = None,
) -> None:
    with _lock, _connect() as connection:
        connection.execute(
            "UPDATE agent_runs SET status = ?, first_event_ms = ?, total_latency_ms = ?, "
            "retrieval_latency_ms = ?, model_latency_ms = ?, sql_latency_ms = ?, "
            "citation_count = ?, result_row_count = ?, error_code = ?, trace_summary = ?, "
            "completed_at = ? WHERE id = ?",
            (
                status,
                first_event_ms,
                max(0, int(total_latency_ms)),
                max(0, int(retrieval_latency_ms)),
                max(0, int(model_latency_ms)),
                max(0, int(sql_latency_ms)),
                max(0, int(citation_count)),
                result_row_count,
                error_code[:120] if error_code else None,
                trace_summary[:8_000] if trace_summary else None,
                utc_now(),
                run_id,
            ),
        )


def get_agent_run_for_user(run_id: str, user_id: int) -> dict | None:
    with _lock, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM agent_runs WHERE id = ? AND user_id = ?", (run_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def update_agent_run_parent(run_id: str, parent_run_id: str) -> None:
    """Link a retry to its failed run without copying sensitive execution data."""

    with _lock, _connect() as connection:
        connection.execute(
            "UPDATE agent_runs SET parent_run_id = ? WHERE id = ?",
            (parent_run_id, run_id),
        )


def upsert_agent_feedback(
    run_id: str,
    user_id: int,
    rating: int,
    issue_type: str | None = None,
    comment: str | None = None,
) -> dict:
    """Store one mutable end-user rating per run and return the saved feedback."""

    now = utc_now()
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO agent_feedback("
            "run_id, user_id, rating, issue_type, comment, review_status, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, 'pending', ?, ?) "
            "ON CONFLICT(run_id, user_id) DO UPDATE SET "
            "rating = excluded.rating, issue_type = excluded.issue_type, comment = excluded.comment, "
            "review_status = 'pending', expected_route = NULL, reviewer_id = NULL, reviewer_note = NULL, "
            "reviewed_at = NULL, updated_at = excluded.updated_at",
            (run_id, user_id, rating, issue_type, comment[:2_000] if comment else None, now, now),
        )
        row = connection.execute(
            "SELECT * FROM agent_feedback WHERE run_id = ? AND user_id = ?", (run_id, user_id)
        ).fetchone()
        return dict(row) if row else {}


def get_agent_feedback(feedback_id: int) -> dict | None:
    with _lock, _connect() as connection:
        row = connection.execute("SELECT * FROM agent_feedback WHERE id = ?", (feedback_id,)).fetchone()
        return dict(row) if row else None


def review_agent_feedback(
    feedback_id: int,
    reviewer_id: int,
    review_status: str,
    reviewer_note: str | None = None,
    expected_route: str | None = None,
    regression_query: str | None = None,
    promote_to_regression: bool = False,
) -> dict | None:
    """Review a feedback item and optionally promote a sanitized regression case."""

    now = utc_now()
    normalized_route = (expected_route or "").strip() or None
    normalized_query = (regression_query or "").strip()
    with _lock, _connect() as connection:
        row = connection.execute("SELECT * FROM agent_feedback WHERE id = ?", (feedback_id,)).fetchone()
        if row is None:
            return None
        if promote_to_regression:
            if review_status != "accepted":
                raise ValueError("只有已接受的反馈可以进入回归集")
            if normalized_route not in {"data", "document", "general", "clarify"}:
                raise ValueError("纳入回归集时必须选择期望路由")
            if not normalized_query:
                raise ValueError("纳入回归集时必须填写脱敏后的回归问题")
        connection.execute(
            "UPDATE agent_feedback SET review_status = ?, expected_route = ?, reviewer_id = ?, "
            "reviewer_note = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
            (
                review_status,
                normalized_route,
                reviewer_id,
                reviewer_note[:2_000] if reviewer_note else None,
                now,
                now,
                feedback_id,
            ),
        )
        if promote_to_regression:
            connection.execute(
                "INSERT INTO regression_cases(id, feedback_id, query, expected_route, source_issue_type, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(feedback_id) DO UPDATE SET query = excluded.query, "
                "expected_route = excluded.expected_route, source_issue_type = excluded.source_issue_type, "
                "created_by = excluded.created_by, created_at = excluded.created_at",
                (
                    secrets.token_urlsafe(18),
                    feedback_id,
                    normalized_query[:4_000],
                    normalized_route,
                    row["issue_type"],
                    reviewer_id,
                    now,
                ),
            )
        elif review_status != "accepted":
            connection.execute("DELETE FROM regression_cases WHERE feedback_id = ?", (feedback_id,))
        saved = connection.execute("SELECT * FROM agent_feedback WHERE id = ?", (feedback_id,)).fetchone()
        return dict(saved) if saved else None


def list_regression_cases() -> list[dict]:
    with _lock, _connect() as connection:
        rows = connection.execute(
            "SELECT rc.id, rc.feedback_id, rc.query, rc.expected_route, rc.source_issue_type, "
            "rc.created_at, u.display_name AS created_by_name "
            "FROM regression_cases rc LEFT JOIN users u ON u.id = rc.created_by "
            "ORDER BY rc.created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def list_quality_feedback(
    *,
    status: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> dict:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    normalized_status = (status or "").strip()
    where_clause = "WHERE f.review_status = ?" if normalized_status else ""
    parameters: tuple[object, ...] = (normalized_status,) if normalized_status else ()
    select = (
        "SELECT f.id, f.run_id, f.rating, f.issue_type, f.comment, f.review_status, "
        "f.expected_route, f.reviewer_note, f.reviewed_at, f.created_at, f.updated_at, "
        "r.query_preview, r.route, r.route_strategy, r.route_confidence, r.policy_version, "
        "r.status AS run_status, r.total_latency_ms, r.retrieval_latency_ms, r.model_latency_ms, "
        "r.sql_latency_ms, r.citation_count, r.error_code, "
        "u.email AS user_email, u.display_name AS user_display_name, "
        "reviewer.display_name AS reviewer_display_name, "
        "rc.id AS regression_case_id "
        "FROM agent_feedback f JOIN agent_runs r ON r.id = f.run_id "
        "LEFT JOIN users u ON u.id = f.user_id "
        "LEFT JOIN users reviewer ON reviewer.id = f.reviewer_id "
        "LEFT JOIN regression_cases rc ON rc.feedback_id = f.id "
    )
    with _lock, _connect() as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM agent_feedback f {where_clause}", parameters
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"{select} {where_clause} ORDER BY f.id DESC LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(percentile / 100 * len(ordered)) - 1))
    return ordered[index]


def _latency_metrics(values: list[int]) -> dict:
    return {
        "count": len(values),
        "p50_ms": _percentile(values, 50),
        "p95_ms": _percentile(values, 95),
        "average_ms": round(sum(values) / len(values), 1) if values else None,
    }


def get_quality_overview(window_days: int = 14) -> dict:
    """Aggregate runtime and feedback signals for the admin quality dashboard."""

    window_days = max(1, min(window_days, 90))
    since = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
    with _lock, _connect() as connection:
        runs = [
            dict(row)
            for row in connection.execute(
                "SELECT id, route, status, total_latency_ms, first_event_ms, retrieval_latency_ms, "
                "model_latency_ms, sql_latency_ms, trace_summary "
                "FROM agent_runs WHERE started_at >= ?",
                (since,),
            ).fetchall()
        ]
        feedback = [
            dict(row)
            for row in connection.execute(
                "SELECT f.rating, f.review_status, r.route FROM agent_feedback f "
                "JOIN agent_runs r ON r.id = f.run_id WHERE r.started_at >= ?",
                (since,),
            ).fetchall()
        ]
        steps = [
            dict(row)
            for row in connection.execute(
                "SELECT s.step_name, s.category, s.duration_ms, s.status FROM agent_run_steps s "
                "JOIN agent_runs r ON r.id = s.run_id WHERE r.started_at >= ?",
                (since,),
            ).fetchall()
        ]

    successful_statuses = {"completed", "clarified"}
    total_latencies = [int(run["total_latency_ms"]) for run in runs if run["total_latency_ms"] is not None]
    first_event_latencies = [int(run["first_event_ms"]) for run in runs if run["first_event_ms"] is not None]
    positive_feedback = sum(item["rating"] == 1 for item in feedback)
    negative_feedback = sum(item["rating"] == -1 for item in feedback)
    routes = sorted({run["route"] or "unknown" for run in runs})
    by_route: list[dict] = []
    for route in routes:
        route_runs = [run for run in runs if (run["route"] or "unknown") == route]
        route_feedback = [item for item in feedback if (item["route"] or "unknown") == route]
        route_latencies = [int(run["total_latency_ms"]) for run in route_runs if run["total_latency_ms"] is not None]
        route_successes = sum(run["status"] in successful_statuses for run in route_runs)
        by_route.append(
            {
                "route": route,
                "run_count": len(route_runs),
                "success_rate": round(route_successes / len(route_runs), 4) if route_runs else None,
                "feedback_count": len(route_feedback),
                "positive_feedback_count": sum(item["rating"] == 1 for item in route_feedback),
                **_latency_metrics(route_latencies),
            }
        )

    step_groups: dict[tuple[str, str], list[int]] = {}
    step_errors: dict[tuple[str, str], int] = {}
    for step in steps:
        key = (step["step_name"], step["category"])
        step_groups.setdefault(key, []).append(int(step["duration_ms"]))
        if step["status"] == "error":
            step_errors[key] = step_errors.get(key, 0) + 1
    slow_steps = [
        {
            "step_name": key[0],
            "category": key[1],
            "error_count": step_errors.get(key, 0),
            **_latency_metrics(durations),
        }
        for key, durations in step_groups.items()
    ]
    slow_steps.sort(key=lambda item: item["p95_ms"] or 0, reverse=True)
    successful_runs = sum(run["status"] in successful_statuses for run in runs)

    budget_rows: list[dict] = []
    for run in runs:
        raw_summary = run.get("trace_summary")
        if not raw_summary:
            continue
        try:
            summary = json.loads(raw_summary)
        except json.JSONDecodeError:
            continue
        budget = summary.get("budget")
        if isinstance(budget, dict):
            budget_rows.append(budget)

    call_counts = [
        int(item["call_count"])
        for item in budget_rows
        if isinstance(item.get("call_count"), int)
    ]
    round_counts = [
        int(item["round_count"])
        for item in budget_rows
        if isinstance(item.get("round_count"), int)
    ]
    max_observed_rounds = [
        int(item["max_observed_round"])
        for item in budget_rows
        if isinstance(item.get("max_observed_round"), int)
    ]
    budget_summary = {
        "observed_runs": len(budget_rows),
        "tool_calls": _latency_metrics(call_counts) if call_counts else {
            "count": 0, "p50_ms": None, "p95_ms": None, "average_ms": None
        },
        "replanning_rounds": _latency_metrics(round_counts) if round_counts else {
            "count": 0, "p50_ms": None, "p95_ms": None, "average_ms": None
        },
        "max_observed_round": max(max_observed_rounds, default=None),
        "exhausted_count": sum(
            item.get("exhausted") is True for item in budget_rows
        ),
    }
    return {
        "window_days": window_days,
        "summary": {
            "run_count": len(runs),
            "successful_run_count": successful_runs,
            "success_rate": round(successful_runs / len(runs), 4) if runs else None,
            "feedback_count": len(feedback),
            "positive_feedback_count": positive_feedback,
            "negative_feedback_count": negative_feedback,
            "helpful_rate": round(positive_feedback / len(feedback), 4) if feedback else None,
            "pending_feedback_count": sum(item["review_status"] == "pending" for item in feedback),
            "latency": _latency_metrics(total_latencies),
            "time_to_first_event": _latency_metrics(first_event_latencies),
        },
        "tool_budget": budget_summary,
        "by_route": by_route,
        "slow_steps": slow_steps[:8],
    }


def purge_expired_quality_data(retention_days: int = 90) -> int:
    """Delete expired execution traces and their dependent feedback through foreign keys."""

    retention_days = max(1, min(retention_days, 3_650))
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    with _lock, _connect() as connection:
        cursor = connection.execute("DELETE FROM agent_runs WHERE started_at < ?", (cutoff,))
        return cursor.rowcount
