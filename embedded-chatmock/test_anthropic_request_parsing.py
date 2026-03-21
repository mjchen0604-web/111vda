import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.routes_anthropic import _convert_anthropic_messages_to_input


class AnthropicRequestParsingTests(unittest.TestCase):
    def test_system_role_is_accepted(self):
        items, err = _convert_anthropic_messages_to_input(
            [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": "system prompt"}],
                }
            ]
        )
        self.assertIsNone(err)
        self.assertEqual(items[0]["role"], "user")

    def test_thinking_block_falls_back_to_text(self):
        items, err = _convert_anthropic_messages_to_input(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "draft reasoning"}],
                }
            ]
        )
        self.assertIsNone(err)
        self.assertEqual(items[0]["content"][0]["type"], "output_text")
        self.assertIn("draft reasoning", items[0]["content"][0]["text"])

    def test_document_block_falls_back_to_text_marker(self):
        items, err = _convert_anthropic_messages_to_input(
            [
                {
                    "role": "user",
                    "content": [{"type": "document", "title": "notes.txt"}],
                }
            ]
        )
        self.assertIsNone(err)
        self.assertEqual(items[0]["content"][0]["type"], "input_text")
        self.assertIn("[document:notes.txt]", items[0]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
