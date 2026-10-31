import unittest

from app.services.evidence_verifier import verify_report_programmatically


class EvidenceVerifierTests(unittest.TestCase):
    def test_citation_numbers_are_not_treated_as_business_metrics(self):
        result = verify_report_programmatically(
            "制度要求先审批。[1]",
            result=[{"销售额": 100}],
            citations=[{"index": 1, "filename": "policy.md"}],
            data_succeeded=True,
            knowledge_succeeded=True,
        )

        self.assertEqual(result.decision, "pass")

    def test_unknown_citation_is_blocked(self):
        result = verify_report_programmatically(
            "制度要求先审批。[2]",
            result=None,
            citations=[{"index": 1, "filename": "policy.md"}],
            data_succeeded=False,
            knowledge_succeeded=True,
        )

        self.assertEqual(result.decision, "block")

    def test_unverified_numeric_claim_requests_one_repair(self):
        result = verify_report_programmatically(
            "销售额为 999。",
            result=[{"销售额": 100}],
            citations=[],
            data_succeeded=True,
            knowledge_succeeded=False,
        )

        self.assertEqual(result.decision, "revise")

    def test_document_numbers_are_valid_composite_evidence(self):
        result = verify_report_programmatically(
            "1月销售额为109030.5，制度要求15分钟内响应。[1]",
            result=[{"月份": 1, "销售额": 109030.5}],
            citations=[{"index": 1, "filename": "policy.md"}],
            data_succeeded=True,
            knowledge_succeeded=True,
            document_evidence="制度要求15分钟内响应。",
        )

        self.assertEqual(result.decision, "pass")

    def test_close_but_wrong_decimal_claim_requests_repair(self):
        result = verify_report_programmatically(
            "1月销售额为109030.6。",
            result=[{"月份": 1, "销售额": 109030.5}],
            citations=[],
            data_succeeded=True,
            knowledge_succeeded=False,
        )

        self.assertEqual(result.decision, "revise")

    def test_markdown_list_numbers_and_chinese_policy_numbers_are_evidence(self):
        result = verify_report_programmatically(
            "审核应关注：\n1. 合法发票。\n2. 行程单。\n3. 出差结束后五个工作日内报销。[1]",
            result=[{"销售额": 41099.5}],
            citations=[{"index": 1, "filename": "travel-policy.md"}],
            data_succeeded=True,
            knowledge_succeeded=True,
            document_evidence="出差结束后五个工作日内完成费用报销。",
        )

        self.assertEqual(result.decision, "pass")


if __name__ == "__main__":
    unittest.main()
