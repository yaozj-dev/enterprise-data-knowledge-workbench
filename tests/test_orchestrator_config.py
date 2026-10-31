import os
import unittest
from unittest.mock import patch

from app.conf.app_config import get_orchestrator_mode


class OrchestratorConfigTests(unittest.TestCase):
    def test_unknown_mode_falls_back_to_supervisor(self):
        with patch.dict(os.environ, {"AGENT_ORCHESTRATOR_MODE": "unsafe-value"}):
            self.assertEqual(get_orchestrator_mode(), "supervisor")

    def test_legacy_mode_is_explicitly_supported(self):
        with patch.dict(os.environ, {"AGENT_ORCHESTRATOR_MODE": "legacy"}):
            self.assertEqual(get_orchestrator_mode(), "legacy")


if __name__ == "__main__":
    unittest.main()
