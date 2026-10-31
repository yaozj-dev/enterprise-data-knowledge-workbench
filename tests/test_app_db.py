import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.security import create_access_token, get_current_user, require_admin
from app.storage import app_db
from app.storage.app_db import hash_password, verify_password


class PasswordTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verifiable(self):
        first = hash_password("correct horse battery staple")
        second = hash_password("correct horse battery staple")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("correct horse battery staple", first))
        self.assertFalse(verify_password("wrong password", first))


class TokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_subject_is_rejected_as_unauthorized(self):
        os.environ["APP_JWT_SECRET"] = "local-test-secret-please-use-a-long-value-123456"
        token = create_access_token({"id": "not-an-integer", "email": "x@y.local", "role": "user"})
        with self.assertRaises(HTTPException) as context:
            await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
        self.assertEqual(context.exception.status_code, 401)

    async def test_non_admin_role_is_denied(self):
        with self.assertRaises(HTTPException) as context:
            require_admin({"role": "user"})
        self.assertEqual(context.exception.status_code, 403)


class DocumentStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_db_path = app_db.DB_PATH
        app_db.DB_PATH = Path(self.temporary_directory.name) / "workbench.db"
        app_db.init_db()
        self.owner = app_db.create_user("db-owner@example.com", "DB Owner", "password-123")
        self.other_user = app_db.create_user("db-other@example.com", "DB Other", "password-123")

    def tearDown(self):
        app_db.DB_PATH = self.original_db_path
        self.temporary_directory.cleanup()

    def test_document_listing_counts_chunks_and_delete_is_user_scoped(self):
        document = app_db.create_document(
            self.owner["id"],
            "policy.txt",
            "text/plain",
            12,
            "a" * 64,
        )
        app_db.add_document_chunk(document["id"], 0, None, "chunk one")
        app_db.add_document_chunk(document["id"], 1, None, "chunk two")
        app_db.update_document_status(document["id"], self.owner["id"], "ready")

        listed = app_db.list_documents(self.owner["id"])
        self.assertEqual(listed[0]["chunk_count"], 2)
        self.assertEqual(
            app_db.find_active_document_by_hash(self.owner["id"], "a" * 64)["id"],
            document["id"],
        )
        self.assertIsNone(app_db.delete_document(document["id"], self.other_user["id"]))
        self.assertIsNotNone(app_db.get_document(document["id"], self.owner["id"]))
        self.assertEqual(app_db.delete_document(document["id"], self.owner["id"])["id"], document["id"])
        self.assertEqual(app_db.list_document_chunks(document["id"], self.owner["id"]), [])

    def test_audit_logs_support_filtering_and_pagination(self):
        app_db.write_audit(self.owner["id"], "upload_document", "policy.txt")
        app_db.write_audit(self.other_user["id"], "query", "sales by region")
        app_db.write_audit(self.owner["id"], "upload_document", "travel.md")

        filtered = app_db.list_audit_logs(limit=1, offset=0, action="upload_document")
        self.assertEqual(filtered["total"], 2)
        self.assertEqual(len(filtered["items"]), 1)
        self.assertEqual(filtered["items"][0]["action"], "upload_document")
        self.assertEqual(filtered["items"][0]["user_email"], self.owner["email"])

        second_page = app_db.list_audit_logs(limit=1, offset=1, action="upload_document")
        self.assertEqual(len(second_page["items"]), 1)
        self.assertNotEqual(filtered["items"][0]["id"], second_page["items"][0]["id"])

    def test_recent_messages_are_bounded_and_scoped_to_conversation_owner(self):
        owner_conversation = app_db.create_conversation(self.owner["id"], "Owner chat")
        other_conversation = app_db.create_conversation(self.other_user["id"], "Other chat")
        for index in range(3):
            app_db.add_message(owner_conversation["id"], "user", f"owner message {index}")
        app_db.add_message(other_conversation["id"], "user", "other secret")

        recent = app_db.list_recent_messages(owner_conversation["id"], self.owner["id"], limit=2)
        self.assertEqual([message["content"] for message in recent], ["owner message 1", "owner message 2"])
        self.assertEqual(
            app_db.list_recent_messages(owner_conversation["id"], self.other_user["id"], limit=8),
            [],
        )


class AgentRunStatusMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_db_path = app_db.DB_PATH
        self.db_path = Path(self.temporary_directory.name) / "legacy-workbench.db"
        app_db.DB_PATH = self.db_path
        self._create_legacy_database()

    def tearDown(self):
        app_db.DB_PATH = self.original_db_path
        self.temporary_directory.cleanup()

    def _create_legacy_database(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE agent_runs (
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
                        'running', 'completed', 'clarified', 'failed', 'cancelled', 'incomplete'
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
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE agent_run_steps (
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
                CREATE TABLE agent_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    rating INTEGER NOT NULL CHECK(rating IN (-1, 1)),
                    issue_type TEXT,
                    comment TEXT,
                    review_status TEXT NOT NULL DEFAULT 'pending',
                    expected_route TEXT,
                    reviewer_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    reviewer_note TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, user_id)
                );
                """
            )
            connection.execute(
                "INSERT INTO users(id, email, display_name, password_hash, role, created_at) "
                "VALUES (1, 'legacy@example.com', 'Legacy User', 'hash', 'user', '2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO conversations(id, user_id, title, created_at, updated_at) "
                "VALUES ('conversation-1', 1, 'Legacy conversation', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO agent_runs("
                "id, user_id, conversation_id, query_preview, query_hash, policy_version, status, started_at"
                ") VALUES ('run-1', 1, 'conversation-1', 'legacy query', 'hash', 'v1', 'completed', '2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO agent_run_steps("
                "run_id, sequence, step_name, category, status, started_offset_ms, duration_ms, created_at"
                ") VALUES ('run-1', 1, 'route', 'agent', 'success', 0, 5, '2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO agent_feedback("
                "run_id, user_id, rating, review_status, created_at, updated_at"
                ") VALUES ('run-1', 1, 1, 'pending', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            connection.commit()
        finally:
            connection.close()

    def test_legacy_agent_run_table_accepts_partial_and_keeps_children(self):
        app_db.init_db()
        app_db.finish_agent_run(
            "run-1",
            status="partial",
            first_event_ms=5,
            total_latency_ms=12,
        )

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agent_runs'"
            ).fetchone()[0]
            self.assertIn("'partial'", schema)
            self.assertEqual(
                connection.execute("SELECT status FROM agent_runs WHERE id = 'run-1'").fetchone()[0],
                "partial",
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM agent_run_steps WHERE run_id = 'run-1'").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM agent_feedback WHERE run_id = 'run-1'").fetchone()[0],
                1,
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertIn(
                "agent_runs",
                {row[2] for row in connection.execute("PRAGMA foreign_key_list(agent_run_steps)")},
            )
            self.assertIn(
                "agent_runs",
                {row[2] for row in connection.execute("PRAGMA foreign_key_list(agent_feedback)")},
            )
            connection.execute("DELETE FROM agent_runs WHERE id = 'run-1'")
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM agent_run_steps WHERE run_id = 'run-1'").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM agent_feedback WHERE run_id = 'run-1'").fetchone()[0],
                0,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
