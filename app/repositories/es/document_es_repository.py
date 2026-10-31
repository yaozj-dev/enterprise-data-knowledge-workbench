"""Elasticsearch BM25 storage for user-isolated document chunks."""

from __future__ import annotations

from typing import Any

from elasticsearch import AsyncElasticsearch


class DocumentESRepository:
    index_name = "workbench_document_chunks"
    mappings = {
        "dynamic": False,
        "properties": {
            "user_id": {"type": "long"},
            "document_id": {"type": "keyword"},
            "filename": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
            "page_number": {"type": "integer"},
            "content": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
            "content_hash": {"type": "keyword"},
        },
    }

    def __init__(self, client: AsyncElasticsearch | None, index_name: str | None = None):
        self.client = client
        # Evaluation must not write into the production document index.  Keeping
        # the index configurable also makes isolated integration tests possible.
        self.index_name = index_name or type(self).index_name

    async def ensure_index(self) -> None:
        if self.client is None:
            raise RuntimeError("Elasticsearch client is not initialized")
        if not await self.client.indices.exists(index=self.index_name):
            await self.client.indices.create(index=self.index_name, mappings=self.mappings)

    async def upsert(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        await self.ensure_index()
        operations: list[dict[str, Any]] = []
        for chunk in chunks:
            document_id = f"{chunk['document_id']}:{chunk['chunk_index']}"
            operations.extend(
                [
                    {"index": {"_index": self.index_name, "_id": document_id}},
                    chunk,
                ]
            )
        await self.client.bulk(operations=operations, refresh="wait_for")

    async def delete_document(self, user_id: int, document_id: str) -> None:
        if self.client is None:
            return
        if not await self.client.indices.exists(index=self.index_name):
            return
        await self.client.delete_by_query(
            index=self.index_name,
            query={
                "bool": {
                    "filter": [
                        {"term": {"user_id": user_id}},
                        {"term": {"document_id": document_id}},
                    ]
                }
            },
            refresh=True,
            conflicts="proceed",
        )

    async def search(self, user_id: int, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if self.client is None:
            raise RuntimeError("Elasticsearch client is not initialized")
        if not await self.client.indices.exists(index=self.index_name):
            return []
        response = await self.client.search(
            index=self.index_name,
            size=limit,
            query={
                "bool": {
                    "must": [{"match": {"content": {"query": query, "operator": "or"}}}],
                    "filter": [{"term": {"user_id": user_id}}],
                }
            },
        )
        hits: list[dict[str, Any]] = []
        for hit in response["hits"]["hits"]:
            source = dict(hit.get("_source") or {})
            source["keyword_score"] = float(hit.get("_score") or 0.0)
            source["score"] = float(hit.get("_score") or 0.0)
            source["retrieval_source"] = "keyword"
            hits.append(source)
        return hits

