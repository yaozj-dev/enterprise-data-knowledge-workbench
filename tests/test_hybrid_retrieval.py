import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services.hybrid_retrieval import (
    RerankerUnavailable,
    _extract_scores,
    chunk_fingerprint,
    reciprocal_rank_fusion,
)


class HybridRetrievalTests(unittest.IsolatedAsyncioTestCase):
    def test_rrf_merges_same_chunk_and_preserves_sources(self):
        vector = [
            {"document_id": "doc-a", "chunk_index": 0, "content": "alpha"},
            {"document_id": "doc-b", "chunk_index": 0, "content": "beta"},
        ]
        keyword = [
            {"document_id": "doc-a", "chunk_index": 0, "content": "alpha"},
            {"document_id": "doc-c", "chunk_index": 0, "content": "gamma"},
        ]
        result = reciprocal_rank_fusion(vector, keyword, k=60, limit=3)

        self.assertEqual(result[0]["document_id"], "doc-a")
        self.assertEqual(result[0]["retrieval_sources"], ["keyword", "vector"])
        self.assertEqual(result[0]["content_hash"], chunk_fingerprint("alpha"))
        self.assertGreater(result[0]["rrf_score"], result[1]["rrf_score"])

    def test_rrf_limit_and_empty_inputs(self):
        self.assertEqual(reciprocal_rank_fusion([], [], limit=5), [])
        self.assertEqual(
            len(
                reciprocal_rank_fusion(
                    [{"document_id": str(i), "chunk_index": 0} for i in range(5)],
                    [],
                    limit=2,
                )
            ),
            2,
        )

    def test_indexed_tei_scores_are_restored_to_candidate_order(self):
        scores = _extract_scores(
            [{"index": 1, "score": 0.91}, {"index": 0, "score": 0.42}],
            expected=2,
        )

        self.assertEqual(scores, [0.42, 0.91])

    def test_reranker_rejects_missing_or_duplicate_candidate_scores(self):
        with self.assertRaises(RerankerUnavailable):
            _extract_scores([{"index": 1, "score": 0.91}], expected=2)
        with self.assertRaises(RerankerUnavailable):
            _extract_scores(
                [{"index": 0, "score": 0.91}, {"index": 0, "score": 0.42}],
                expected=2,
            )

    async def test_http_reranker_uses_tei_rerank_payload(self):
        from app.services.hybrid_retrieval import HttpReranker

        response = Mock()
        response.json.return_value = [
            {"index": 1, "score": 0.91},
            {"index": 0, "score": 0.42},
        ]
        response.raise_for_status = Mock()
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=response)

        with patch("httpx.AsyncClient", return_value=client):
            ranked = await HttpReranker("http://reranker/rerank", batch_size=2).rank(
                "销售额",
                [
                    {"document_id": "doc-a", "chunk_index": 0, "content": "制度"},
                    {"document_id": "doc-b", "chunk_index": 0, "content": "销售"},
                ],
            )

        payload = client.post.await_args.kwargs["json"]
        self.assertEqual(payload["query"], "销售额")
        self.assertEqual(payload["texts"], ["制度", "销售"])
        self.assertNotIn("inputs", payload)
        self.assertEqual(ranked[0]["document_id"], "doc-b")

    async def test_http_reranker_chunks_candidates_for_tei_batch_limit(self):
        from app.services.hybrid_retrieval import HttpReranker

        responses = []
        for scores in (
            [
                {"index": 0, "score": 0.10},
                {"index": 1, "score": 0.20},
            ],
            [
                {"index": 0, "score": 0.40},
                {"index": 1, "score": 0.30},
            ],
            [{"index": 0, "score": 0.90}],
        ):
            response = Mock()
            response.json.return_value = scores
            response.raise_for_status = Mock()
            responses.append(response)

        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=responses)
        hits = [
            {"document_id": f"doc-{index}", "chunk_index": 0, "content": str(index)}
            for index in range(5)
        ]

        with patch("httpx.AsyncClient", return_value=client):
            ranked = await HttpReranker(
                "http://reranker/rerank", batch_size=2
            ).rank("查询", hits)

        self.assertEqual(client.post.await_count, 3)
        self.assertEqual(
            [call.kwargs["json"]["texts"] for call in client.post.await_args_list],
            [["0", "1"], ["2", "3"], ["4"]],
        )
        self.assertEqual(ranked[0]["document_id"], "doc-4")

    async def test_http_reranker_opens_cooldown_after_failure(self):
        from app.services.hybrid_retrieval import HttpReranker

        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=RuntimeError("service unavailable"))
        reranker = HttpReranker(
            "http://reranker/rerank", failure_cooldown_seconds=60
        )
        hits = [{"document_id": "doc-a", "chunk_index": 0, "content": "a"}]

        with patch("httpx.AsyncClient", return_value=client):
            with self.assertRaises(RerankerUnavailable):
                await reranker.rank("查询", hits)
            with self.assertRaises(RerankerUnavailable):
                await reranker.rank("查询", hits)

        self.assertEqual(client.post.await_count, 1)


if __name__ == "__main__":
    unittest.main()

