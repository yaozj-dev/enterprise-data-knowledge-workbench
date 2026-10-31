import asyncio
import unittest

from app.scripts.evaluate_document_retrieval import evaluate, score_cases


class DocumentEvaluationTests(unittest.TestCase):
    def test_scores_top_three_retrieval_separately_from_top_one_citation(self):
        metrics = score_cases(
            [
                (
                    {"expected_document_id": "policy-a", "expected_page_number": 1},
                    [
                        {"document_id": "policy-b", "page_number": 1},
                        {"document_id": "policy-a", "page_number": 1},
                    ],
                ),
                (
                    {"expected_document_id": "policy-b", "expected_page_number": 2},
                    [{"document_id": "policy-b", "page_number": 2}],
                ),
            ]
        )

        self.assertEqual(metrics["total"], 2)
        self.assertEqual(metrics["retrieval_hits"], 2)
        self.assertEqual(metrics["citation_hits"], 1)
        self.assertEqual(metrics["retrieval_hit_rate"], 1.0)
        self.assertEqual(metrics["citation_accuracy"], 0.5)
        self.assertEqual(metrics["mrr"], 0.75)
        self.assertEqual(metrics["irrelevant_top1_rate"], 0.5)

    def test_scores_latency_and_rejects_misaligned_latency_input(self):
        results = [
            (
                {"expected_document_id": "policy-a", "expected_page_number": 1},
                [{"document_id": "policy-a", "page_number": 1}],
            ),
            (
                {"expected_document_id": "policy-b", "expected_page_number": 1},
                [{"document_id": "policy-b", "page_number": 1}],
            ),
        ]
        metrics = score_cases(results, latencies_ms=[11.0, 25.0])

        self.assertEqual(metrics["latency"]["count"], 2)
        self.assertEqual(metrics["latency"]["p50_ms"], 11.0)
        self.assertEqual(metrics["latency"]["p95_ms"], 25.0)
        with self.assertRaises(ValueError):
            score_cases(results, latencies_ms=[11.0])

    def test_evaluate_rejects_non_positive_rrf_k_before_connecting(self):
        with self.assertRaises(ValueError):
            asyncio.run(evaluate(rrf_k=0))


if __name__ == "__main__":
    unittest.main()
