"""Benchmark vector, BM25, RRF and optional reranked document retrieval.

The evaluation corpus is written to isolated Qdrant and Elasticsearch indexes,
then deleted in ``finally``. It therefore never changes a user's uploaded
knowledge base. Reranking is intentionally optional: an unavailable service
is reported as unavailable instead of being counted as an RRF result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from math import ceil
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.es.document_es_repository import DocumentESRepository
from app.repositories.qdrant.document_qdrant_repository import DocumentQdrantRepository
from app.services.hybrid_retrieval import (
    HttpReranker,
    Reranker,
    RerankerUnavailable,
    reciprocal_rank_fusion,
)

PROJECT_ROOT = Path(__file__).parents[2]
CORPUS_PATH = PROJECT_ROOT / "evals" / "document_corpus.jsonl"
CASES_PATH = PROJECT_ROOT / "evals" / "document_retrieval_cases.jsonl"
EVALUATION_QDRANT_COLLECTION = "workbench_document_retrieval_eval"
EVALUATION_ES_INDEX = "workbench_document_retrieval_eval"
EVALUATION_USER_ID = 9_999_991


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"找不到评测集：{path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(percentile / 100 * len(ordered)) - 1))
    return round(ordered[index], 2)


def _latency_metrics(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "p50_ms": _percentile(values, 50),
        "p95_ms": _percentile(values, 95),
        "average_ms": round(sum(values) / len(values), 2) if values else None,
    }


def _document_matches(case: dict[str, Any], hit: dict[str, Any]) -> bool:
    return hit.get("document_id") == case["expected_document_id"]


def _citation_matches(case: dict[str, Any], hit: dict[str, Any]) -> bool:
    return _document_matches(case, hit) and hit.get("page_number") == case[
        "expected_page_number"
    ]


def score_cases(
    results: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    *,
    latencies_ms: list[float] | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """Calculate Top-K, MRR, citation accuracy and irrelevant top-1 rate."""

    if top_k < 1:
        raise ValueError("top_k must be positive")
    if latencies_ms is not None and len(latencies_ms) != len(results):
        raise ValueError("latencies_ms must align with results")

    retrieval_hits = 0
    citation_hits = 0
    reciprocal_ranks: list[float] = []
    irrelevant_top_one = 0
    for case, hits in results:
        ranked_hits = hits[:top_k]
        matching_rank = next(
            (
                index
                for index, hit in enumerate(ranked_hits, start=1)
                if _document_matches(case, hit)
            ),
            None,
        )
        if matching_rank is not None:
            retrieval_hits += 1
            reciprocal_ranks.append(1 / matching_rank)
        else:
            reciprocal_ranks.append(0.0)
        if hits and _citation_matches(case, hits[0]):
            citation_hits += 1
        if hits and not _citation_matches(case, hits[0]):
            irrelevant_top_one += 1

    total = len(results)
    return {
        "total": total,
        "top_k": top_k,
        "retrieval_hits": retrieval_hits,
        "citation_hits": citation_hits,
        "retrieval_hit_rate": retrieval_hits / total if total else 0.0,
        "mrr": sum(reciprocal_ranks) / total if total else 0.0,
        "citation_accuracy": citation_hits / total if total else 0.0,
        "irrelevant_top1_count": irrelevant_top_one,
        "irrelevant_top1_rate": irrelevant_top_one / total if total else 0.0,
        "latency": _latency_metrics(latencies_ms or []),
    }


async def _timed(awaitable: Awaitable[Any]) -> tuple[Any, float]:
    started_at = perf_counter()
    value = await awaitable
    return value, (perf_counter() - started_at) * 1_000


async def evaluate(
    reranker: Reranker | None = None,
    *,
    rrf_k: int = 60,
) -> dict[str, Any]:
    """Run the isolated benchmark against the configured local infrastructure."""

    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")

    corpus = load_jsonl(CORPUS_PATH)
    cases = load_jsonl(CASES_PATH)
    qdrant_client_manager.init()
    es_client_manager.init()
    embedding_client_manager.init()
    qdrant_client = qdrant_client_manager.client
    es_client = es_client_manager.client
    embeddings = embedding_client_manager.client
    if qdrant_client is None or es_client is None or embeddings is None:
        raise RuntimeError("文档评测依赖的客户端没有初始化")

    vector_repository = DocumentQdrantRepository(
        qdrant_client, EVALUATION_QDRANT_COLLECTION
    )
    keyword_repository = DocumentESRepository(es_client, EVALUATION_ES_INDEX)
    vector_results: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    keyword_results: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    rrf_results: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    reranked_results: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    vector_latencies: list[float] = []
    keyword_latencies: list[float] = []
    rrf_latencies: list[float] = []
    reranked_latencies: list[float] = []
    reranker_reason: str | None = None

    try:
        if await qdrant_client.collection_exists(EVALUATION_QDRANT_COLLECTION):
            await qdrant_client.delete_collection(EVALUATION_QDRANT_COLLECTION)
        if await es_client.indices.exists(index=EVALUATION_ES_INDEX):
            await es_client.indices.delete(index=EVALUATION_ES_INDEX)

        vectors = await embeddings.aembed_documents(
            [document["content"] for document in corpus]
        )
        point_ids = [
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"evaluation:{document['document_id']}"))
            for document in corpus
        ]
        payloads = [
            {
                "user_id": EVALUATION_USER_ID,
                "document_id": document["document_id"],
                "filename": document["filename"],
                "chunk_index": 0,
                "page_number": document["page_number"],
                "content": document["content"],
            }
            for document in corpus
        ]
        await vector_repository.upsert(point_ids, vectors, payloads)
        await keyword_repository.upsert(payloads)

        for case in cases:
            embedding_started = perf_counter()
            query_embedding = await embeddings.aembed_query(case["query"])
            embedding_latency_ms = (perf_counter() - embedding_started) * 1_000
            vector_task = asyncio.create_task(
                _timed(vector_repository.search(EVALUATION_USER_ID, query_embedding, limit=20))
            )
            keyword_task = asyncio.create_task(
                _timed(
                    keyword_repository.search(EVALUATION_USER_ID, case["query"], limit=20)
                )
            )
            (vector_hits, vector_search_ms), (keyword_hits, keyword_search_ms) = (
                await asyncio.gather(vector_task, keyword_task)
            )
            fused = reciprocal_rank_fusion(vector_hits, keyword_hits, k=rrf_k, limit=10)

            vector_results.append((case, vector_hits[:3]))
            keyword_results.append((case, keyword_hits[:3]))
            rrf_results.append((case, fused[:3]))
            vector_latencies.append(embedding_latency_ms + vector_search_ms)
            keyword_latencies.append(keyword_search_ms)
            rrf_latencies.append(
                embedding_latency_ms + max(vector_search_ms, keyword_search_ms)
            )

            if reranker is not None:
                rerank_started = perf_counter()
                try:
                    reranked = await reranker.rank(case["query"], fused)
                except RerankerUnavailable as exc:
                    reranker_reason = str(exc) or "reranker_unavailable"
                    reranker = None
                else:
                    reranked_results.append((case, reranked[:3]))
                    reranked_latencies.append(
                        embedding_latency_ms
                        + max(vector_search_ms, keyword_search_ms)
                        + (perf_counter() - rerank_started) * 1_000
                    )
    finally:
        if await qdrant_client.collection_exists(EVALUATION_QDRANT_COLLECTION):
            await qdrant_client.delete_collection(EVALUATION_QDRANT_COLLECTION)
        if await es_client.indices.exists(index=EVALUATION_ES_INDEX):
            await es_client.indices.delete(index=EVALUATION_ES_INDEX)
        await qdrant_client_manager.close()
        await es_client_manager.close()

    strategies: dict[str, dict[str, Any]] = {
        "vector": score_cases(vector_results, latencies_ms=vector_latencies),
        "bm25": score_cases(keyword_results, latencies_ms=keyword_latencies),
        "rrf": score_cases(rrf_results, latencies_ms=rrf_latencies),
    }
    if reranked_results and len(reranked_results) == len(cases):
        strategies["rrf_reranker"] = {
            "available": True,
            **score_cases(reranked_results, latencies_ms=reranked_latencies),
        }
    else:
        strategies["rrf_reranker"] = {
            "available": False,
            "reason": reranker_reason or "reranker_not_configured",
        }
    return {
        "corpus_size": len(corpus),
        "case_count": len(cases),
        "rrf_k": rrf_k,
        "strategies": strategies,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate vector, BM25, RRF and optional reranked retrieval"
    )
    parser.add_argument(
        "--rrf-k",
        type=int,
        nargs="+",
        default=[60],
        help="RRF k value(s) to compare against the same isolated corpus (default: 60).",
    )
    parser.add_argument(
        "--reranker-url",
        default=os.getenv("RERANKER_URL", "").strip(),
        help="Optional TEI /rerank endpoint; unset keeps the reranker result unavailable.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional path for a machine-readable result report.",
    )
    args = parser.parse_args()
    if any(value <= 0 for value in args.rrf_k):
        parser.error("--rrf-k values must be positive")
    reranker = HttpReranker(args.reranker_url) if args.reranker_url else None
    metrics_by_k = {
        str(value): asyncio.run(evaluate(reranker, rrf_k=value))
        for value in args.rrf_k
    }
    metrics = metrics_by_k[str(args.rrf_k[0])] if len(args.rrf_k) == 1 else {
        "rrf_k_values": args.rrf_k,
        "runs": metrics_by_k,
    }
    report = json.dumps(metrics, ensure_ascii=False, indent=2)
    print(report)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(report + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
