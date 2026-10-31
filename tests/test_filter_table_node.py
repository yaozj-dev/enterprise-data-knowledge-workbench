import unittest
from unittest.mock import patch

from langchain_core.runnables import RunnableLambda

from app.agent.nodes import filter_table as filter_table_node
from app.agent.nodes.filter_table import filter_table


class _CapturingLLM:
    def __init__(self):
        self.prompts = []

class _CapturingRunnable(RunnableLambda):
    def __init__(self, recorder: _CapturingLLM):
        super().__init__(self._invoke)
        self.recorder = recorder

    async def _invoke(self, prompt: str):
        self.recorder.prompts.append(prompt)
        return '{"orders": ["region_id"]}'


class _Runtime:
    def __init__(self):
        self.stream_writer = lambda _event: None


class FilterTableNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_input_is_copied_and_examples_are_limited(self):
        state = {
            "query": "统计各区域销售额",
            "table_infos": [
                {
                    "name": "orders",
                    "role": "fact",
                    "description": "订单事实表",
                    "columns": [
                        {
                            "name": "region_id",
                            "type": "varchar",
                            "role": "dimension",
                            "examples": ["a", "b", "c", "d"],
                            "description": "区域",
                            "alias": ["区域"],
                        }
                    ],
                }
            ],
        }
        recorder = _CapturingLLM()
        fake_llm = _CapturingRunnable(recorder)

        with (
            patch.object(filter_table_node, "json_llm", fake_llm),
            patch.object(filter_table_node, "FILTER_TABLE_MAX_EXAMPLES", 1),
        ):
            result = await filter_table(state, _Runtime())

        prompt_text = recorder.prompts[0].to_string()
        self.assertIn("examples:\n    - a\n    description", prompt_text)
        self.assertNotIn("- b\n", prompt_text)
        self.assertEqual(
            result["table_infos"][0]["columns"][0]["examples"], ["a", "b", "c", "d"]
        )
        self.assertEqual(
            state["table_infos"][0]["columns"][0]["examples"],
            ["a", "b", "c", "d"],
        )

    async def test_filter_input_can_exclude_examples(self):
        state = {
            "query": "统计各区域销售额",
            "table_infos": [
                {
                    "name": "orders",
                    "role": "fact",
                    "description": "订单事实表",
                    "columns": [
                        {
                            "name": "region_id",
                            "type": "varchar",
                            "role": "dimension",
                            "examples": ["a", "b"],
                            "description": "区域",
                            "alias": ["区域"],
                        }
                    ],
                }
            ],
        }
        recorder = _CapturingLLM()
        fake_llm = _CapturingRunnable(recorder)

        with (
            patch.object(filter_table_node, "json_llm", fake_llm),
            patch.object(filter_table_node, "FILTER_TABLE_MAX_EXAMPLES", 0),
        ):
            result = await filter_table(state, _Runtime())

        prompt_text = recorder.prompts[0].to_string()
        self.assertNotIn("- a\n", prompt_text)
        self.assertNotIn("- b\n", prompt_text)
        self.assertEqual(result["table_infos"][0]["columns"][0]["examples"], ["a", "b"])
        self.assertEqual(state["table_infos"][0]["columns"][0]["examples"], ["a", "b"])
