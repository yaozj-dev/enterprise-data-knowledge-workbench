"""Document ingestion and retrieval service for the enterprise knowledge base."""

import hashlib
import io
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.repositories.es.document_es_repository import DocumentESRepository
from app.repositories.qdrant.document_qdrant_repository import DocumentQdrantRepository
from app.services.hybrid_retrieval import (
    Reranker,
    RerankerUnavailable,
    chunk_fingerprint,
    reciprocal_rank_fusion,
)
from app.storage.app_db import (
    add_document_chunk,
    create_document,
    delete_document,
    find_active_document_by_hash,
    get_document,
    list_document_chunks,
    replace_document_chunks,
    update_document_status,
)

MAX_EMBEDDING_TOKENS = 480
# Once a paragraph is this large, tokenizer calls can become both unnecessary
# and disproportionately expensive. Keep a hard character ceiling as a final
# guard for code-heavy Markdown where one character may still be several tokens.
MAX_CHUNK_CHARACTERS = 420
EMBEDDING_BATCH_SIZE = 8
_TOKENIZER: Any | None = None
_TOKENIZER_ATTEMPTED = False


def _tokenizer_path() -> Path | None:
    """Find the local tokenizer without requiring the model server at parse time."""

    configured = os.getenv("EMBEDDING_TOKENIZER_PATH", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(__file__).parents[2] / "docker/embedding/bge-large-zh-v1.5/tokenizer.json",
        Path("/models/bge-large-zh-v1.5/tokenizer.json"),
    ]
    return next((path for path in candidates if path and path.is_file()), None)


def _tokenizer():
    global _TOKENIZER, _TOKENIZER_ATTEMPTED

    if _TOKENIZER_ATTEMPTED:
        return _TOKENIZER
    _TOKENIZER_ATTEMPTED = True
    path = _tokenizer_path()
    if path is None:
        return None
    try:
        from tokenizers import Tokenizer

        _TOKENIZER = Tokenizer.from_file(str(path))
    except Exception:
        _TOKENIZER = None
    return _TOKENIZER


def _token_count(text: str) -> int:
    tokenizer = _tokenizer()
    if tokenizer is not None:
        return len(tokenizer.encode(text).ids)
    # Conservative fallback: code, English and CJK can all consume more than
    # one token per character, so never assume 900 characters equals 900 tokens.
    return len(text)


def _split_long_segment(segment: str, page_number: int | None) -> list[ParsedChunk]:
    if _token_count(segment) <= MAX_EMBEDDING_TOKENS:
        return _hard_character_limit(segment, page_number)

    tokenizer = _tokenizer()
    if tokenizer is not None:
        encoding = tokenizer.encode(segment)
        if len(encoding.ids) > MAX_EMBEDDING_TOKENS:
            # Slice by tokenizer offsets so the model receives valid chunks
            # rather than arbitrary UTF-8 byte boundaries.
            pieces: list[ParsedChunk] = []
            start = 0
            while start < len(encoding.ids):
                end = min(len(encoding.ids), start + MAX_EMBEDDING_TOKENS)
                token_start = max(0, encoding.offsets[start][0])
                token_end = max(token_start + 1, encoding.offsets[end - 1][1])
                token_end = min(len(segment), token_end)
                piece = segment[token_start:token_end].strip()
                if piece:
                    pieces.append(ParsedChunk(piece, page_number))
                # Offsets are character positions, while ``start`` advances
                # in token positions. Never derive one from the other or a
                # multi-token character can make the loop stall.
                start = end
            return [
                bounded
                for piece in pieces
                for bounded in _hard_character_limit(piece.content, page_number)
            ]

    # Fallback for environments without the local tokenizer file. A character
    # ceiling is deliberately conservative: TEI rejects the whole request if
    # even one input reaches 512 tokens.
    return _hard_character_limit(segment, page_number)


def _hard_character_limit(segment: str, page_number: int | None) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    for start in range(0, len(segment), MAX_CHUNK_CHARACTERS):
        piece = segment[start : start + MAX_CHUNK_CHARACTERS].strip()
        if piece:
            chunks.append(ParsedChunk(piece, page_number))
    return chunks


@dataclass(frozen=True)
class ParsedChunk:
    content: str
    page_number: int | None


class DuplicateDocumentError(ValueError):
    def __init__(self, document: dict):
        self.document = document
        super().__init__(f"相同内容已存在：{document['filename']}")


class DocumentNotFoundError(ValueError):
    pass


def _chunk_text(text: str, page_number: int | None = None) -> list[ParsedChunk]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    chunks: list[ParsedChunk] = []
    current = ""
    for paragraph in normalized.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if current and _token_count(candidate) > MAX_EMBEDDING_TOKENS:
            chunks.append(ParsedChunk(current, page_number))
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(ParsedChunk(current, page_number))

    bounded: list[ParsedChunk] = []
    for chunk in chunks:
        bounded.extend(_split_long_segment(chunk.content, page_number))
    return bounded


def parse_document(filename: str, content: bytes) -> list[ParsedChunk]:
    extension = Path(filename).suffix.lower()
    if extension in {".txt", ".md"}:
        return _chunk_text(content.decode("utf-8-sig"))
    if extension == ".pdf":
        from pypdf import PdfReader

        chunks: list[ParsedChunk] = []
        for page_number, page in enumerate(PdfReader(io.BytesIO(content)).pages, start=1):
            chunks.extend(_chunk_text(page.extract_text() or "", page_number))
        return chunks
    if extension == ".docx":
        from docx import Document

        document = Document(io.BytesIO(content))
        return _chunk_text("\n".join(paragraph.text for paragraph in document.paragraphs))
    raise ValueError("仅支持 PDF、DOCX、Markdown 和 TXT 文件")


class DocumentService:
    def __init__(
        self,
        repository: DocumentQdrantRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        keyword_repository: DocumentESRepository | None = None,
        reranker: Reranker | None = None,
    ):
        self.repository = repository
        self.embedding_client = embedding_client
        self.keyword_repository = keyword_repository
        self.reranker = reranker

    @staticmethod
    def _point_ids(document_id: str, chunk_count: int) -> list[str]:
        return [
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{index}"))
            for index in range(chunk_count)
        ]

    @staticmethod
    def _payloads(user_id: int, document: dict, chunks: list[ParsedChunk]) -> list[dict]:
        return [
            {
                "user_id": user_id,
                "document_id": document["id"],
                "filename": document["filename"],
                "chunk_index": index,
                "page_number": chunk.page_number,
                "content": chunk.content,
                "content_hash": chunk_fingerprint(chunk.content),
            }
            for index, chunk in enumerate(chunks)
        ]

    async def _write_vectors(
        self,
        user_id: int,
        document: dict,
        chunks: list[ParsedChunk],
        *,
        replace_existing: bool,
    ) -> None:
        # TEI applies its token limit per input and its payload limit per
        # request. Bounded batching keeps a large README from producing a 413
        # while preserving deterministic ordering for Qdrant point IDs.
        texts = [chunk.content for chunk in chunks]
        embeddings: list[list[float]] = []
        for offset in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[offset : offset + EMBEDDING_BATCH_SIZE]
            embeddings.extend(await self.embedding_client.aembed_documents(batch))
        if len(embeddings) != len(chunks):
            raise RuntimeError("Embedding 服务返回的向量数量与文档片段数量不一致")
        if replace_existing:
            await self.repository.delete_document(user_id, document["id"])
        await self.repository.upsert(
            self._point_ids(document["id"], len(chunks)),
            embeddings,
            self._payloads(user_id, document, chunks),
        )
        if self.keyword_repository is not None:
            await self.keyword_repository.delete_document(user_id, document["id"])
            await self.keyword_repository.upsert(self._payloads(user_id, document, chunks))

    async def ingest(
        self, user_id: int, filename: str, content_type: str, content: bytes
    ) -> dict:
        content_hash = hashlib.sha256(content).hexdigest()
        existing = find_active_document_by_hash(user_id, content_hash)
        if existing:
            raise DuplicateDocumentError(existing)
        try:
            document = create_document(
                user_id, filename, content_type, len(content), content_hash
            )
        except sqlite3.IntegrityError as exc:
            # The partial unique index closes the small concurrent-upload race.
            existing = find_active_document_by_hash(user_id, content_hash)
            if existing:
                raise DuplicateDocumentError(existing) from exc
            raise
        try:
            chunks = parse_document(filename, content)
            if not chunks:
                raise ValueError("文档没有提取到可检索的文字")
            for index, chunk in enumerate(chunks):
                add_document_chunk(document["id"], index, chunk.page_number, chunk.content)
            await self._write_vectors(user_id, document, chunks, replace_existing=False)
            update_document_status(document["id"], user_id, "ready")
        except Exception as exc:
            update_document_status(document["id"], user_id, "failed", str(exc))
            raise
        return {**document, "status": "ready", "chunk_count": len(chunks)}

    async def reindex(self, user_id: int, document_id: str) -> dict:
        """Rebuild one document's vectors from its persisted parsed chunks."""

        document = get_document(document_id, user_id)
        if document is None:
            raise DocumentNotFoundError("文档不存在或无权操作")
        stored_chunks = list_document_chunks(document_id, user_id)
        if not stored_chunks:
            raise ValueError("文档没有可用于重建索引的已保存内容")
        chunks = [
            ParsedChunk(chunk["content"], chunk["page_number"])
            for chunk in stored_chunks
        ]
        bounded_chunks: list[ParsedChunk] = []
        for chunk in chunks:
            bounded_chunks.extend(_split_long_segment(chunk.content, chunk.page_number))
        if not bounded_chunks:
            raise ValueError("文档没有可用于重建索引的已保存内容")
        chunks = bounded_chunks
        update_document_status(document_id, user_id, "processing")
        try:
            await self._write_vectors(user_id, document, chunks, replace_existing=True)
            replace_document_chunks(
                document_id,
                user_id,
                [
                    {
                        "content": chunk.content,
                        "page_number": chunk.page_number,
                    }
                    for chunk in chunks
                ],
            )
            update_document_status(document_id, user_id, "ready")
        except Exception as exc:
            # Parsed chunks remain in SQLite, so users can safely retry later.
            update_document_status(document_id, user_id, "failed", str(exc))
            raise
        refreshed = get_document(document_id, user_id)
        return refreshed or {**document, "status": "ready", "chunk_count": len(chunks)}

    async def delete(self, user_id: int, document_id: str) -> dict:
        document = get_document(document_id, user_id)
        if document is None:
            raise DocumentNotFoundError("文档不存在或无权操作")
        # Delete vectors first. If Qdrant is unavailable, leave the source and
        # metadata intact so a later retry cannot create orphaned vectors.
        await self.repository.delete_document(user_id, document_id)
        if self.keyword_repository is not None:
            await self.keyword_repository.delete_document(user_id, document_id)
        deleted = delete_document(document_id, user_id)
        if deleted is None:
            raise DocumentNotFoundError("文档不存在或无权操作")
        return document

    async def search(self, user_id: int, query: str, limit: int = 5) -> list[dict]:
        """Search with vector-only compatibility or hybrid RRF plus rerank."""

        if self.keyword_repository is None:
            embedding = await self.embedding_client.aembed_query(query)
            return await self.repository.search(user_id, embedding, limit=limit)

        embedding = await self.embedding_client.aembed_query(query)
        import asyncio

        vector_task = asyncio.create_task(self.repository.search(user_id, embedding, limit=20))
        keyword_task = asyncio.create_task(self.keyword_repository.search(user_id, query, limit=20))
        vector_result, keyword_result = await asyncio.gather(
            vector_task, keyword_task, return_exceptions=True
        )
        vector_hits = vector_result if isinstance(vector_result, list) else []
        keyword_hits = keyword_result if isinstance(keyword_result, list) else []
        degraded_reason = next(
            (
                reason
                for failed, reason in (
                    (vector_result, "vector_unavailable"),
                    (keyword_result, "keyword_unavailable"),
                )
                if not isinstance(failed, list)
            ),
            None,
        )
        if not vector_hits and not keyword_hits:
            errors = [item for item in (vector_result, keyword_result) if isinstance(item, Exception)]
            if len(errors) == 2:
                raise RuntimeError("vector and keyword retrieval are unavailable") from errors[0]
            return []
        fused = reciprocal_rank_fusion(vector_hits, keyword_hits, k=60, limit=10)
        if degraded_reason:
            for hit in fused:
                hit["retrieval_degraded"] = degraded_reason
        if self.reranker is not None and fused:
            try:
                fused = await self.reranker.rank(query, fused)
            except RerankerUnavailable:
                for hit in fused:
                    hit["retrieval_degraded"] = "reranker_unavailable"
        return fused[:limit]
