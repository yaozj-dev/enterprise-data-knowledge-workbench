import unittest

from app.scripts.verify_readonly_dw_access import grants_are_read_only


class ReadonlyDWVerificationTests(unittest.TestCase):
    def test_select_only_grants_are_accepted(self):
        grants = [
            "GRANT USAGE ON *.* TO `agent_readonly`@`%`",
            "GRANT SELECT ON `dw`.* TO `agent_readonly`@`%`",
        ]

        self.assertTrue(grants_are_read_only(grants, "dw"))

    def test_write_or_all_privileges_are_rejected(self):
        self.assertFalse(
            grants_are_read_only(
                ["GRANT SELECT, INSERT ON `dw`.* TO `agent_readonly`@`%`"], "dw"
            )
        )
        self.assertFalse(
            grants_are_read_only(
                ["GRANT ALL PRIVILEGES ON `dw`.* TO `agent_readonly`@`%`"], "dw"
            )
        )
