import unittest

from app.services.sql_policy import (
    SQLPolicyError,
    bounded_read_query,
    normalize_read_query,
)


class SQLPolicyTests(unittest.TestCase):
    def test_allows_analytics_select_and_normalizes_trailing_semicolon(self):
        self.assertEqual(
            normalize_read_query("SELECT region_name FROM dim_region;"),
            "SELECT region_name FROM dim_region",
        )

    def test_rejects_write_multi_statement_and_unknown_table(self):
        for query in (
            "DELETE FROM fact_order",
            "SELECT * FROM fact_order; SELECT * FROM dim_region",
            "SELECT * FROM users",
        ):
            with self.subTest(query=query), self.assertRaises(SQLPolicyError):
                normalize_read_query(query)

    def test_caps_unbounded_result(self):
        self.assertIn("LIMIT 50", bounded_read_query("SELECT * FROM fact_order", 50))


if __name__ == "__main__":
    unittest.main()
