import unittest

from app.services.quality_service import AgentRunTrace


class QualityServiceStatusTests(unittest.TestCase):
    def test_done_partial_is_persisted_as_partial(self):
        trace = AgentRunTrace.__new__(AgentRunTrace)
        trace._finished = False
        trace._stored = False
        trace._started = __import__("time").perf_counter()
        trace._first_event_ms = None
        trace._step_started = {}
        trace._step_durations = {}
        trace._step_count = 0
        trace._route = None
        trace._route_strategy = None
        trace._route_confidence = None
        trace._terminal_type = None
        trace._execution_status = None
        trace._error_code = None
        trace._citation_count = 0
        trace._result_row_count = None
        trace.observe({"type": "done", "status": "partial", "execution_status": "partial"})

        self.assertEqual(trace._terminal_type, "partial")
        self.assertEqual(trace._execution_status, "partial")

    def test_done_clarified_is_not_completed(self):
        trace = AgentRunTrace.__new__(AgentRunTrace)
        trace._finished = False
        trace._stored = False
        trace._started = __import__("time").perf_counter()
        trace._first_event_ms = None
        trace._step_started = {}
        trace._step_durations = {}
        trace._step_count = 0
        trace._route = None
        trace._route_strategy = None
        trace._route_confidence = None
        trace._terminal_type = None
        trace._execution_status = None
        trace._error_code = None
        trace._citation_count = 0
        trace._result_row_count = None
        trace.observe({"type": "done", "status": "clarified", "execution_status": "clarified"})

        self.assertEqual(trace._terminal_type, "clarification")


if __name__ == "__main__":
    unittest.main()
