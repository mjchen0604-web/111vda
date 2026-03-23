import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.routes_anthropic import _convert_anthropic_messages_to_input
from chatmock.app import create_app


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

    def test_prefill_is_rejected_for_claude_46_family(self):
        app = create_app()
        client = app.test_client()
        resp = client.post(
            "/v1/messages",
            headers={"X-Oneapi-Request-Id": "req_prefill_123"},
            json={
                "model": "claude-opus-4-6",
                "max_tokens": 16,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "prefill"}]},
                ],
            },
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertEqual(body.get("type"), "error")
        self.assertEqual(body.get("request_id"), "req_prefill_123")
        self.assertEqual(resp.headers.get("request-id"), "req_prefill_123")
        self.assertIn("Prefill is deprecated", body.get("error", {}).get("message", ""))


if __name__ == "__main__":
    unittest.main()
