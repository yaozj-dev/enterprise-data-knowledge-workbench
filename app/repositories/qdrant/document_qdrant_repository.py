"""Qdrant storage for user-isolated enterprise document chunks."""

from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import app_config


class DocumentQdrantRepository:
    collection_name = "workbench_document_chunks"

    def __init__(self, client: AsyncQdrantClient, collection_name: str | None = None):
        self.client = client
        self.collection_name = collection_name or type(self).collection_name

    async def ensure_collection(self) -> None:
        if not await self.client.collection_exists(self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=app_config.qdrant.embedding_size,
                    distance=models.Distance.COSINE,
                ),
            )

    async def upsert(
        self, ids: list[str], embeddings: list[list[float]], payloads: list[dict]
    ) -> None:
        await self.ensure_collection()
        points = [
            models.PointStruct(id=point_id, vector=embedding, payload=payload)
            for point_id, embedding, payload in zip(ids, embeddings, payloads)
        ]
        await self.client.upsert(collection_name=self.collection_name, points=points)

    async def delete_document(self, user_id: int, document_id: str) -> None:
        """Remove only vectors belonging to one owned source document."""

        if not await self.client.collection_exists(self.collection_name):
            return
        document_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="user_id", match=models.MatchValue(value=user_id)
                ),
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=document_id)
                ),
            ]
        )
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(filter=document_filter),
        )

    async def search(self, user_id: int, embedding: list[float], limit: int = 5) -> list[dict]:
        await self.ensure_collection()
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id", match=models.MatchValue(value=user_id)
                    )
                ]
            ),
            limit=limit,
            with_payload=True,
        )
        return [
            {"score": point.score, **(point.payload or {})}
            for point in result.points
        ]
