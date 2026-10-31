"""Hybrid document retrieval primitives.

The functions in this module are dependency-free so ranking behavior can be
tested without running Qdrant or Elasticsearch.  Infrastructure adapters only
need to return dictionaries containing ``document_id``, ``chunk_index`` and
``content``.
"""

from __future__ import annotations

import hashlib
from time import monotonic
from typing import Any, Protocol


def chunk_fingerprint(content: str) -> str:
    """Return a stable content fingerprint for citations and deduplication."""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def hit_identity(hit: dict[str, Any]) -> tuple[str, str]:
    """Use source identity rather than model score to merge two result lists."""

    document_id = str(hit.get("document_id") or hit.get("id") or "")
    chunk_index = str(hit.get("chunk_index", hit.get("_id", "")))
    return document_id, chunk_index


def reciprocal_rank_fusion(
    vector_hits: list[dict[str, Any]],
    keyword_hits: list[dict[str, Any]],
    *,
    k: int = 60,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Fuse ranked lists using RRF while preserving source metadata.

    Scores from Qdrant and BM25 are not directly comparable.  RRF only uses
    rank, then exposes a normalized convenience score for the existing
    relevance gate.  The raw ``rrf_score`` remains available for evaluation.
    """

    if k <= 0:
        raise ValueError("RRF k must be positive")
    if limit <= 0:
        return []

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    scores: dict[tuple[str, str], float] = {}
    sources: dict[tuple[str, str], set[str]] = {}
    for source, hits in (("vector", vector_hits), ("keyword", keyword_hits)):
        for rank, hit in enumerate(hits, start=1):
            identity = hit_identity(hit)
            if not identity[0] and not identity[1]:
                continue
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (k + rank)
            sources.setdefault(identity, set()).add(source)
            merged.setdefault(identity, dict(hit))

    ranked = sorted(scores, key=lambda identity: scores[identity], reverse=True)
    results: list[dict[str, Any]] = []
    for identity in ranked[:limit]:
        item = dict(merged[identity])
        raw_score = scores[identity]
        item["rrf_score"] = raw_score
        item["retrieval_sources"] = sorted(sources[identity])
        # A hit found at rank 1 in both lists is close to 1 after scaling;
        # this is only a gate-friendly score, not a probability.
        item["score"] = min(1.0, raw_score * k)
        item.setdefault("content_hash", chunk_fingerprint(str(item.get("content", ""))))
        results.append(item)
    return results


class Reranker(Protocol):
    async def rank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the same hit dictionaries ordered by relevance."""


class RerankerUnavailable(RuntimeError):
    """Raised when the optional precision stage cannot be reached."""


class HttpReranker:
    """Small adapter for a BGE reranker HTTP service.

    The adapter intentionally does not make the service mandatory.  With
    ``RERANKER_URL`` unset, callers keep the RRF result and expose a visible
    degradation event instead of making local startup depend on a second model.
    """

    def __init__(
        self,
        endpoint: str,
        timeout_seconds: float = 30.0,
        # The current TEI CPU image can terminate on multi-candidate BGE
        # rerank requests. A single-candidate batch is slower but keeps the
        # optional precision stage isolated from the main retrieval path.
        batch_size: int = 1,
        failure_cooldown_seconds: float = 60.0,
    ):
        if batch_size < 1:
            raise ValueError("reranker batch_size must be positive")
        if failure_cooldown_seconds < 0:
            raise ValueError("reranker failure cooldown cannot be negative")
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.batch_size = batch_size
        self.failure_cooldown_seconds = failure_cooldown_seconds
        self._disabled_until = 0.0
        self._last_failure: str | None = None

    async def rank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not hits:
            return []
        if monotonic() < self._disabled_until:
            raise RerankerUnavailable(
                self._last_failure or "reranker circuit is open"
            )
        try:
            import httpx

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                all_scores: list[float] = []
                for offset in range(0, len(hits), self.batch_size):
                    batch = hits[offset : offset + self.batch_size]
                    response = await client.post(
                        self.endpoint,
                        json={
                            # TEI's rerank endpoint uses the top-level
                            # query/texts contract; the nested ``inputs``
                            # shape belongs to embedding requests and returns
                            # 422 here.
                            "query": query,
                            "texts": [str(hit.get("content", "")) for hit in batch],
                            "truncate": True,
                        },
                    )
                    response.raise_for_status()
                    all_scores.extend(
                        _extract_scores(response.json(), len(batch))
                    )
        except Exception as exc:  # pragma: no cover - network behavior is integration-tested
            self._last_failure = str(exc) or "reranker request failed"
            self._disabled_until = monotonic() + self.failure_cooldown_seconds
            raise RerankerUnavailable(str(exc)) from exc

        scores = all_scores
        self._disabled_until = 0.0
        self._last_failure = None
        ranked = []
        for index, score in sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True):
            item = dict(hits[index])
            item["rerank_score"] = float(score)
            item["score"] = max(0.0, min(1.0, float(score)))
            ranked.append(item)
        return ranked


def _extract_scores(payload: Any, expected: int) -> list[float]:
    """Accept common TEI/reranker response shapes and reject malformed data."""

    values: list[Any]
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict) and isinstance(payload.get("scores"), list):
        values = payload["scores"]
    else:
        raise RerankerUnavailable("reranker response has no scores")
    if len(values) != expected:
        raise RerankerUnavailable("reranker score count does not match candidates")
    scores = [0.0] * expected
    indexed_values = any(isinstance(value, dict) and "index" in value for value in values)
    seen: set[int] = set()
    for position, value in enumerate(values):
        if isinstance(value, dict):
            index = value.get("index", position)
            value = value.get("score")
        else:
            index = position
        if not isinstance(value, (int, float)):
            raise RerankerUnavailable("reranker returned an invalid score")
        if not isinstance(index, int) or index < 0 or index >= expected:
            raise RerankerUnavailable("reranker returned an invalid candidate index")
        if indexed_values and index in seen:
            raise RerankerUnavailable("reranker returned duplicate candidate indexes")
        seen.add(index)
        scores[index] = float(value)
    if indexed_values and len(seen) != expected:
        raise RerankerUnavailable("reranker did not score every candidate")
    return scores

