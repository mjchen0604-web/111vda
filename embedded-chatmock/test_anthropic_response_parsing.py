import json
import sys
import unittest
from pathlib import Path

from flask import Flask


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.routes_anthropic import _anthropic_stream
from chatmock.upstream_errors import build_anthropic_error_response


class DummyUpstream:
    def __init__(self, events):
        self._events = events
        self.chatmock_source = "upstream"
        self.status_code = 200
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for event in self._events:
            line = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield line if decode_unicode else line.encode("utf-8")

    def close(self):
        self.closed = True


class AnthropicResponseParsingTests(unittest.TestCase):
    def test_stream_recovers_text_from_output_text_done(self):
        upstream = DummyUpstream(
            [
                {
                    "type": "response.output_text.done",
                    "text": "Only done text",
                    "response": {"id": "resp_test"},
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_test",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Only done text"}],
                            }
                        ],
                    },
                },
            ]
        )

        chunks = list(_anthropic_stream(upstream, "gpt-5.4", False))
        body = "".join(chunks)
        self.assertIn('event: content_block_start', body)
        self.assertIn('event: content_block_delta', body)
        self.assertIn('Only done text', body)

    def test_stream_recovers_text_from_output_item_done_message(self):
        upstream = DummyUpstream(
            [
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Message item text"}],
                    },
                    "response": {"id": "resp_test"},
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_test",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Message item text"}],
                            }
                        ],
                    },
                },
            ]
        )

        chunks = list(_anthropic_stream(upstream, "gpt-5.4", False))
        body = "".join(chunks)
        self.assertIn('Message item text', body)

    def test_anthropic_error_response_includes_request_id(self):
        app = Flask(__name__)
        with app.test_request_context(headers={"X-Oneapi-Request-Id": "req_err_456"}):
            resp = build_anthropic_error_response(
                {
                    "raw_status": 401,
                    "raw_message": "Account unavailable",
                    "raw_code": "invalid_api_key",
                }
            )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.headers.get("request-id"), "req_err_456")
        body = resp.get_json()
        self.assertEqual(body.get("request_id"), "req_err_456")
        self.assertEqual(body.get("type"), "error")
        self.assertEqual(body.get("error"), {"type": "authentication_error", "message": "Account unavailable"})


if __name__ == "__main__":
    unittest.main()
