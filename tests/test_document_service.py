import tempfile
import unittest
from pathlib import Path

from app.services.document_service import (
    MAX_EMBEDDING_TOKENS,
    DocumentNotFoundError,
    DocumentService,
    DuplicateDocumentError,
    _chunk_text,
)
from app.services.hybrid_retrieval import RerankerUnavailable
from app.storage import app_db


class FakeEmbeddings:
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(index), 0.5] for index, _ in enumerate(texts)]

    async def aembed_query(self, query: str) -> list[float]:
        return [0.1, 0.2]


class InspectingEmbeddings(FakeEmbeddings):
    def __init__(self):
        self.calls: list[list[str]] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return await super().aembed_documents(texts)


class FakeDocumentRepository:
    def __init__(self):
        self.upsert_calls: list[tuple[list[str], list[list[float]], list[dict]]] = []
        self.deleted_documents: list[tuple[int, str]] = []

    async def upsert(
        self, ids: list[str], embeddings: list[list[float]], payloads: list[dict]
    ) -> None:
        self.upsert_calls.append((ids, embeddings, payloads))

    async def delete_document(self, user_id: int, document_id: str) -> None:
        self.deleted_documents.append((user_id, document_id))

    async def search(self, user_id: int, embedding: list[float], limit: int = 5) -> list[dict]:
        return []


class HybridDocumentRepository(FakeDocumentRepository):
    async def search(self, user_id: int, embedding: list[float], limit: int = 5) -> list[dict]:
        return [
            {
                "user_id": user_id,
                "document_id": "policy-1",
                "chunk_index": 0,
                "filename": "policy.md",
                "content": "费用申请需要先审批。",
            }
        ]


class FakeKeywordRepository:
    async def search(self, user_id: int, query: str, limit: int = 20) -> list[dict]:
        return [
            {
                "user_id": user_id,
                "document_id": "policy-1",
                "chunk_index": 0,
                "filename": "policy.md",
                "content": "费用申请需要先审批。",
            }
        ]


class UnavailableReranker:
    async def rank(self, query: str, hits: list[dict]) -> list[dict]:
        raise RerankerUnavailable("test outage")


class UnavailableKeywordRepository:
    async def search(self, user_id: int, query: str, limit: int = 20) -> list[dict]:
        raise RuntimeError("elasticsearch is unavailable")


class DocumentServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_db_path = app_db.DB_PATH
        app_db.DB_PATH = Path(self.temporary_directory.name) / "workbench.db"
        app_db.init_db()
        self.owner = app_db.create_user("owner@example.com", "Owner", "password-123")
        self.other_user = app_db.create_user("other@example.com", "Other", "password-123")
        self.repository = FakeDocumentRepository()
        self.service = DocumentService(self.repository, FakeEmbeddings())

    def tearDown(self):
        app_db.DB_PATH = self.original_db_path
        self.temporary_directory.cleanup()

    async def test_duplicate_upload_reindex_and_delete_lifecycle(self):
        content = "差旅报销需要提交发票和审批单。".encode()
        document = await self.service.ingest(
            self.owner["id"], "travel-policy.md", "text/markdown", content
        )

        self.assertEqual(document["status"], "ready")
        self.assertEqual(document["chunk_count"], 1)
        self.assertEqual(len(self.repository.upsert_calls), 1)
        self.assertEqual(
            self.repository.upsert_calls[0][2][0]["user_id"], self.owner["id"]
        )

        with self.assertRaises(DuplicateDocumentError):
            await self.service.ingest(
                self.owner["id"], "same-content.txt", "text/plain", content
            )

        reindexed = await self.service.reindex(self.owner["id"], document["id"])
        self.assertEqual(reindexed["status"], "ready")
        self.assertEqual(self.repository.deleted_documents, [(self.owner["id"], document["id"])])
        self.assertEqual(len(self.repository.upsert_calls), 2)

        deleted = await self.service.delete(self.owner["id"], document["id"])
        self.assertEqual(deleted["id"], document["id"])
        self.assertIsNone(app_db.get_document(document["id"], self.owner["id"]))
        self.assertEqual(app_db.list_document_chunks(document["id"], self.owner["id"]), [])

    async def test_reindex_does_not_disclose_another_users_document(self):
        document = await self.service.ingest(
            self.owner["id"], "owner-only.txt", "text/plain", "private policy".encode()
        )

        with self.assertRaises(DocumentNotFoundError):
            await self.service.reindex(self.other_user["id"], document["id"])

        self.assertEqual(self.repository.deleted_documents, [])

    async def test_hybrid_search_keeps_rrf_result_when_reranker_is_unavailable(self):
        service = DocumentService(
            HybridDocumentRepository(),
            FakeEmbeddings(),
            FakeKeywordRepository(),
            UnavailableReranker(),
        )

        hits = await service.search(self.owner["id"], "费用审批")

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["retrieval_sources"], ["keyword", "vector"])
        self.assertEqual(hits[0]["retrieval_degraded"], "reranker_unavailable")

    async def test_hybrid_search_marks_single_path_degradation(self):
        service = DocumentService(
            HybridDocumentRepository(),
            FakeEmbeddings(),
            UnavailableKeywordRepository(),
        )

        hits = await service.search(self.owner["id"], "费用审批")

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["retrieval_sources"], ["vector"])
        self.assertEqual(hits[0]["retrieval_degraded"], "keyword_unavailable")

    async def test_markdown_chunks_stay_below_embedding_token_limit(self):
        content = "\n\n".join(
            f"## Section {index}\n" + "API endpoint details and **important policy** " * 28
            for index in range(10)
        )

        chunks = _chunk_text(content)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.content.encode("utf-8")) > 0 for chunk in chunks))
        from app.services.document_service import _token_count

        self.assertTrue(all(_token_count(chunk.content) <= MAX_EMBEDDING_TOKENS for chunk in chunks))

    async def test_large_document_embeddings_are_batched(self):
        embeddings = InspectingEmbeddings()
        service = DocumentService(FakeDocumentRepository(), embeddings)
        content = "\n\n".join(
            (f"## Section {index}\n" + "Policy workflow requires approval before payment. " * 35)
            for index in range(24)
        )

        document = await service.ingest(
            self.owner["id"], "long.md", "text/markdown", content.encode()
        )

        self.assertEqual(document["status"], "ready")
        self.assertGreater(len(embeddings.calls), 1)
        self.assertTrue(all(len(batch) <= 8 for batch in embeddings.calls))
        self.assertEqual(sum(len(batch) for batch in embeddings.calls), document["chunk_count"])

    async def test_reindex_repairs_legacy_oversized_chunks(self):
        document = await self.service.ingest(
            self.owner["id"], "legacy.md", "text/markdown", b"seed"
        )
        legacy_chunk = "word " * 400
        with app_db._lock, app_db._connect() as connection:
            connection.execute(
                "UPDATE document_chunks SET content = ? WHERE document_id = ?",
                (legacy_chunk, document["id"]),
            )

        reindexed = await self.service.reindex(self.owner["id"], document["id"])

        self.assertEqual(reindexed["status"], "ready")
        # Inspect the persisted upsert payload to prove every vector input is
        # bounded even when the local tokenizer is unavailable.
        payloads = self.repository.upsert_calls[-1][2]
        self.assertTrue(payloads)
        self.assertTrue(all(len(item["content"]) <= 420 for item in payloads))
        stored = app_db.list_document_chunks(document["id"], self.owner["id"])
        self.assertEqual(len(stored), len(payloads))
        self.assertEqual(stored[0]["content"], payloads[0]["content"])


if __name__ == "__main__":
    unittest.main()
