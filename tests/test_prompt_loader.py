import unittest
from unittest.mock import patch

from app.prompt.prompt_loader import load_prompt


class PromptLoaderTests(unittest.TestCase):
    def test_loads_a_packaged_prompt(self):
        content = load_prompt("extend_keywords_for_column_recall")

        self.assertTrue(content.strip())

    def test_missing_prompt_explains_deployment_requirement(self):
        with patch("app.prompt.prompt_loader.Path.read_text", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(FileNotFoundError, "包含 prompts 目录"):
                load_prompt("missing")


if __name__ == "__main__":
    unittest.main()
