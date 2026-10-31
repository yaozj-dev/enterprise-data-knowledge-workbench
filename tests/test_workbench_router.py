import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_document_service,
    get_memory_repository,
    get_query_service,
)
from app.security import create_access_token
from app.storage import app_db
from main import app


class FakeDocumentService:
    def __init__(self):
        self.deleted_documents: list[tuple[int, str]] = []

    async def delete(self, user_id: int, document_id: str) -> dict:
        self.deleted_documents.append((user_id, document_id))
        return {"id": document_id}


class FakeMemoryRepository:
    async def list_for_user(self, user_id: int, _status: str | None = None):
        return [
            {
                "id": "memory-1",
                "user_id": user_id,
                "conversation_id": None,
                "kind": "preference",
                "content": "用中文回答",
                "source_run_id": None,
                "status": "confirmed",
                "created_at": None,
                "confirmed_at": None,
            }
        ]

    async def confirm(self, user_id: int, memory_id: str):
        return (
            {
                "id": memory_id,
                "user_id": user_id,
                "conversation_id": None,
                "kind": "preference",
                "content": "用中文回答",
                "source_run_id": None,
                "status": "confirmed",
                "created_at": None,
                "confirmed_at": None,
            }
            if user_id == 2
            else None
        )


class WorkbenchAdminRouterTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_db_path = app_db.DB_PATH
        self.original_lifespan = app.router.lifespan_context
        self.original_secret = os.environ.get("APP_JWT_SECRET")
        self.original_admin_email = os.environ.get("APP_ADMIN_EMAIL")
        self.original_admin_password = os.environ.get("APP_ADMIN_PASSWORD")
        self.original_self_registration = os.environ.get("APP_ALLOW_SELF_REGISTRATION")
        app_db.DB_PATH = Path(self.temporary_directory.name) / "workbench.db"
        os.environ["APP_JWT_SECRET"] = "local-test-secret-please-use-a-long-value-123456"
        os.environ.pop("APP_ADMIN_EMAIL", None)
        os.environ.pop("APP_ADMIN_PASSWORD", None)
        os.environ["APP_ALLOW_SELF_REGISTRATION"] = "true"
        app_db.init_db()

        self.admin = app_db.create_user("admin@example.com", "Admin", "password-123", "admin")
        self.member = app_db.create_user("member@example.com", "Member", "password-123")
        self.document_service = FakeDocumentService()
        self.memory_repository = FakeMemoryRepository()

        @asynccontextmanager
        async def no_external_lifespan(_app):
            yield

        app.router.lifespan_context = no_external_lifespan
        app.dependency_overrides[get_document_service] = lambda: self.document_service
        app.dependency_overrides[get_memory_repository] = lambda: self.memory_repository
        app.dependency_overrides[get_query_service] = lambda: object()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        app.dependency_overrides.pop(get_document_service, None)
        app.dependency_overrides.pop(get_memory_repository, None)
        app.dependency_overrides.pop(get_query_service, None)
        app.router.lifespan_context = self.original_lifespan
        app_db.DB_PATH = self.original_db_path
        if self.original_secret is None:
            os.environ.pop("APP_JWT_SECRET", None)
        else:
            os.environ["APP_JWT_SECRET"] = self.original_secret
        if self.original_admin_email is None:
            os.environ.pop("APP_ADMIN_EMAIL", None)
        else:
            os.environ["APP_ADMIN_EMAIL"] = self.original_admin_email
        if self.original_admin_password is None:
            os.environ.pop("APP_ADMIN_PASSWORD", None)
        else:
            os.environ["APP_ADMIN_PASSWORD"] = self.original_admin_password
        if self.original_self_registration is None:
            os.environ.pop("APP_ALLOW_SELF_REGISTRATION", None)
        else:
            os.environ["APP_ALLOW_SELF_REGISTRATION"] = self.original_self_registration
        self.temporary_directory.cleanup()

    @staticmethod
    def _headers(user: dict) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user)}"}

    def test_member_cannot_read_admin_users(self):
        response = self.client.get("/api/workbench/admin/users", headers=self._headers(self.member))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "需要管理员权限")

    def test_plural_memory_routes_preserve_user_scoped_contract(self):
        headers = self._headers(self.member)
        plural = self.client.get("/api/workbench/memories", headers=headers)
        singular = self.client.get("/api/workbench/memory", headers=headers)
        confirmed = self.client.post(
            "/api/workbench/memories/memory-1/confirm",
            headers=headers,
        )

        self.assertEqual(plural.status_code, 200)
        self.assertEqual(plural.json()[0]["user_id"], self.member["id"])
        self.assertEqual(singular.status_code, 200)
        self.assertEqual(singular.json()[0]["user_id"], self.member["id"])
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["user_id"], self.member["id"])

    def test_self_registration_returns_a_standard_user_session(self):
        response = self.client.post(
            "/api/workbench/auth/register",
            json={
                "email": "new-user@example.com",
                "display_name": "New User",
                "password": "registration-password",
                "role": "admin",
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["user"]["email"], "new-user@example.com")
        self.assertEqual(payload["user"]["role"], "user")
        user = app_db.get_user_by_email("new-user@example.com")
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "user")

        headers = {"Authorization": f"Bearer {payload['access_token']}"}
        self.assertEqual(self.client.get("/api/workbench/auth/me", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/workbench/admin/users", headers=headers).status_code, 403)

        duplicate = self.client.post(
            "/api/workbench/auth/register",
            json={
                "email": "new-user@example.com",
                "display_name": "Duplicate User",
                "password": "registration-password",
            },
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_self_registration_can_be_disabled(self):
        os.environ["APP_ALLOW_SELF_REGISTRATION"] = "false"

        response = self.client.post(
            "/api/workbench/auth/register",
            json={
                "email": "blocked-user@example.com",
                "display_name": "Blocked User",
                "password": "registration-password",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(app_db.get_user_by_email("blocked-user@example.com"))

    def test_admin_deletion_cleans_vectors_before_database_cascade(self):
        document = app_db.create_document(
            self.member["id"],
            "member-policy.md",
            "text/markdown",
            20,
            "b" * 64,
        )
        app_db.add_document_chunk(document["id"], 0, None, "member-only content")

        response = self.client.delete(
            f"/api/workbench/admin/users/{self.member['id']}",
            headers=self._headers(self.admin),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], self.member["email"])
        self.assertEqual(self.document_service.deleted_documents, [(self.member["id"], document["id"])])
        self.assertIsNone(app_db.get_user(self.member["id"]))
        self.assertIsNone(app_db.get_document(document["id"], self.member["id"]))

    def test_admin_cannot_delete_current_account(self):
        response = self.client.delete(
            f"/api/workbench/admin/users/{self.admin['id']}",
            headers=self._headers(self.admin),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(app_db.get_user(self.admin["id"]))

    def test_feedback_is_owned_by_the_user_and_admin_can_review_it(self):
        conversation = app_db.create_conversation(self.member["id"], "feedback chat")
        run = app_db.create_agent_run(
            self.member["id"],
            conversation["id"],
            "统计本季度销售额",
            "a" * 64,
            "hybrid-router-v1",
        )
        app_db.finish_agent_run(
            run["id"],
            status="completed",
            first_event_ms=1,
            total_latency_ms=10,
        )

        member_response = self.client.post(
            f"/api/workbench/agent-runs/{run['id']}/feedback",
            json={"rating": -1, "issue_type": "wrong_route", "comment": "应该走数据分析"},
            headers=self._headers(self.member),
        )
        self.assertEqual(member_response.status_code, 200)
        feedback_id = member_response.json()["id"]

        other_user = app_db.create_user("other@example.com", "Other", "password-123")
        other_response = self.client.post(
            f"/api/workbench/agent-runs/{run['id']}/feedback",
            json={"rating": 1},
            headers=self._headers(other_user),
        )
        self.assertEqual(other_response.status_code, 404)

        blocked_overview = self.client.get(
            "/api/workbench/admin/quality/overview", headers=self._headers(self.member)
        )
        self.assertEqual(blocked_overview.status_code, 403)

        review_response = self.client.post(
            f"/api/workbench/admin/quality/feedback/{feedback_id}/review",
            json={
                "review_status": "accepted",
                "expected_route": "data",
                "regression_query": "统计本季度销售额",
                "promote_to_regression": True,
            },
            headers=self._headers(self.admin),
        )
        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(review_response.json()["review_status"], "accepted")
        overview = self.client.get(
            "/api/workbench/admin/quality/overview", headers=self._headers(self.admin)
        )
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["summary"]["run_count"], 1)

    def test_streaming_query_persists_a_trace_before_feedback_is_available(self):
        class FakeWorkbenchService:
            def __init__(self, *_args):
                pass

            async def stream(self, *_args):
                yield 'data: {"type":"progress","step":"识别业务意图","status":"running"}\n\n'
                yield 'data: {"type":"progress","step":"识别业务意图","status":"success"}\n\n'
                yield 'data: {"type":"route","intent":"general","strategy":"rules_general","confidence":0.95}\n\n'
                yield 'data: {"type":"answer","content":"测试回答"}\n\n'

        conversation = app_db.create_conversation(self.member["id"], "trace chat")
        with patch("app.api.routers.workbench_router.WorkbenchService", FakeWorkbenchService):
            response = self.client.post(
                f"/api/workbench/conversations/{conversation['id']}/query",
                json={"query": "你好"},
                headers=self._headers(self.member),
            )

        self.assertEqual(response.status_code, 200)
        trace_line = next(line for line in response.text.splitlines() if '"type": "trace"' in line)
        trace_id = json.loads(trace_line[6:])["run_id"]
        trace = app_db.get_agent_run_for_user(trace_id, self.member["id"])
        self.assertEqual(trace["status"], "completed")
        self.assertEqual(trace["route"], "general")
        self.assertIsNotNone(trace["first_event_ms"])

    def test_streaming_query_persists_degradation_and_verifier_metadata(self):
        class FakeWorkbenchService:
            def __init__(self, *_args):
                pass

            async def stream(self, *_args):
                yield 'data: {"type":"degraded","service":"redis","status":"degraded","fallback":"in_process","reason":"REDIS_UNAVAILABLE"}\n\n'
                yield 'data: {"type":"agent_completed","agent_name":"report","task_id":"report-1","status":"partial","verification":"pass","verification_meta":{"degraded":true,"model_status":"timeout","model_confidence":0.0}}\n\n'
                yield 'data: {"type":"answer","content":"已保留可核验证据。"}\n\n'
                yield 'data: {"type":"done","status":"partial","execution_status":"partial"}\n\n'

        conversation = app_db.create_conversation(self.member["id"], "metadata chat")
        with patch("app.api.routers.workbench_router.WorkbenchService", FakeWorkbenchService):
            response = self.client.post(
                f"/api/workbench/conversations/{conversation['id']}/query",
                json={"query": "请保留可核验证据"},
                headers=self._headers(self.member),
            )

        self.assertEqual(response.status_code, 200)
        messages = app_db.list_messages(conversation["id"], self.member["id"])
        metadata = json.loads(messages[-1]["event_data"])
        self.assertEqual(metadata["degradations"][0]["reason"], "REDIS_UNAVAILABLE")
        self.assertEqual(metadata["verification"]["decision"], "pass")
        self.assertEqual(metadata["verification"]["meta"]["model_status"], "timeout")

    def test_agent_run_snapshot_is_user_scoped_and_bounded(self):
        conversation = app_db.create_conversation(self.member["id"], "runtime chat")
        run = app_db.create_agent_run(
            self.member["id"],
            conversation["id"],
            "统计销售额",
            "b" * 64,
            "hybrid-router-v1",
        )
        app_db.finish_agent_run(
            run["id"], status="completed", first_event_ms=2, total_latency_ms=20
        )

        with patch("app.api.routers.workbench_router.runtime_state_store") as store:
            store.get_run = AsyncMock(
                return_value={
                    "key": f"agent:{self.member['id']}:{conversation['id']}:{run['id']}",
                    "plan": {"intent": "data"},
                    "events": [{"type": "done", "execution_status": "completed"}],
                    "source": "redis",
                    "degraded": False,
                }
            )
            response = self.client.get(
                f"/api/workbench/agent-runs/{run['id']}",
                headers=self._headers(self.member),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], run["id"])
        self.assertEqual(payload["runtime"]["source"], "redis")
        self.assertEqual(payload["runtime"]["events"][0]["type"], "done")

        other_user = app_db.create_user("runtime-other@example.com", "Other", "password-123")
        forbidden = self.client.get(
            f"/api/workbench/agent-runs/{run['id']}",
            headers=self._headers(other_user),
        )
        self.assertEqual(forbidden.status_code, 404)

    def test_agent_run_retry_is_scoped_and_rejects_completed_run(self):
        conversation = app_db.create_conversation(self.member["id"], "retry chat")
        app_db.add_message(conversation["id"], "user", "统计销售额")
        original = app_db.create_agent_run(
            self.member["id"],
            conversation["id"],
            "统计销售额",
            "c" * 64,
            "hybrid-router-v1",
        )
        app_db.finish_agent_run(
            original["id"], status="failed", first_event_ms=2, total_latency_ms=10
        )

        response = self.client.post(
            f"/api/workbench/agent-runs/{original['id']}/retry",
            headers=self._headers(self.member),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotEqual(payload["id"], original["id"])
        self.assertEqual(payload["parent_run_id"], original["id"])
        retried = app_db.get_agent_run_for_user(payload["id"], self.member["id"])
        self.assertIsNotNone(retried)
        assert retried is not None
        self.assertEqual(retried["query_preview"], "统计销售额")

        other_user = app_db.create_user("retry-other@example.com", "Other", "password-123")
        forbidden = self.client.post(
            f"/api/workbench/agent-runs/{original['id']}/retry",
            headers=self._headers(other_user),
        )
        self.assertEqual(forbidden.status_code, 404)

        app_db.finish_agent_run(
            payload["id"], status="completed", first_event_ms=1, total_latency_ms=5
        )
        conflict = self.client.post(
            f"/api/workbench/agent-runs/{payload['id']}/retry",
            headers=self._headers(self.member),
        )
        self.assertEqual(conflict.status_code, 409)


if __name__ == "__main__":
    unittest.main()
