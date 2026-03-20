import json
import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.routes_openai import _consume_chat_completion_nonstream
from chatmock.utils import sse_translate_chat


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


def _message_done_events(text: str):
    response = {
        "id": "resp_test",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }
    return [
        {
            "type": "response.output_item.done",
            "item": response["output"][0],
            "response": {"id": "resp_test"},
        },
        {
            "type": "response.completed",
            "response": response,
        },
    ]


def _output_text_done_only_events(text: str):
    return [
        {
            "type": "response.output_text.done",
            "item_id": "msg_done",
            "content_index": 0,
            "text": text,
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
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
            },
        },
    ]


def _message_done_events_with_usage(text: str):
    response = {
        "id": "resp_usage",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 45,
            "total_tokens": 165,
            "input_tokens_details": {"cached_tokens": 80},
            "prompt_cache_hit_tokens": 80,
        },
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }
    return [
        {
            "type": "response.output_item.done",
            "item": response["output"][0],
            "response": {"id": "resp_usage"},
        },
        {
            "type": "response.completed",
            "response": response,
        },
    ]


class ResponseEventParsingTests(unittest.TestCase):
    def test_nonstream_recovers_message_text_from_output_item_done(self):
        upstream = DummyUpstream(_message_done_events("Image summary"))
        result = _consume_chat_completion_nonstream(
            upstream,
            requested_model="gpt-5.4",
            model="gpt-5.4",
            created=0,
            reasoning_compat="think-tags",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"]["content"], "Image summary")

    def test_stream_recovers_message_text_without_output_text_delta(self):
        upstream = DummyUpstream(_message_done_events("Image summary"))
        chunks = list(
            sse_translate_chat(
                upstream,
                "gpt-5.4",
                0,
                reasoning_compat="think-tags",
            )
        )
        payloads = []
        for chunk in chunks:
            text = chunk.decode("utf-8")
            if not text.startswith("data: ") or "[DONE]" in text:
                continue
            payloads.append(json.loads(text[len("data: ") :].strip()))

        content_deltas = [
            entry["choices"][0]["delta"].get("content", "")
            for entry in payloads
            if entry.get("choices")
        ]
        self.assertIn("Image summary", "".join(content_deltas))

    def test_stream_recovers_text_from_output_text_done(self):
        upstream = DummyUpstream(_output_text_done_only_events("Only done text"))
        chunks = list(
            sse_translate_chat(
                upstream,
                "gpt-5.4",
                0,
                reasoning_compat="think-tags",
            )
        )
        payloads = []
        for chunk in chunks:
            text = chunk.decode("utf-8")
            if not text.startswith("data: ") or "[DONE]" in text:
                continue
            payloads.append(json.loads(text[len("data: ") :].strip()))

        content_deltas = [
            entry["choices"][0]["delta"].get("content", "")
            for entry in payloads
            if entry.get("choices")
        ]
        self.assertIn("Only done text", "".join(content_deltas))

    def test_stream_without_completed_and_without_text_returns_error(self):
        upstream = DummyUpstream([])
        chunks = list(
            sse_translate_chat(
                upstream,
                "gpt-5.4",
                0,
                reasoning_compat="think-tags",
            )
        )
        payloads = []
        for chunk in chunks:
            text = chunk.decode("utf-8")
            if not text.startswith("data: ") or "[DONE]" in text:
                continue
            payloads.append(json.loads(text[len("data: ") :].strip()))

        self.assertTrue(any("error" in entry for entry in payloads))

    def test_nonstream_keeps_cached_usage_fields(self):
        upstream = DummyUpstream(_message_done_events_with_usage("cached usage"))
        result = _consume_chat_completion_nonstream(
            upstream,
            requested_model="gpt-5.4",
            model="gpt-5.4",
            created=0,
            reasoning_compat="current",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["usage_obj"]["prompt_tokens"], 120)
        self.assertEqual(result["usage_obj"]["completion_tokens"], 45)
        self.assertEqual(result["usage_obj"]["prompt_tokens_details"]["cached_tokens"], 80)
        self.assertEqual(result["usage_obj"]["prompt_cache_hit_tokens"], 80)

    def test_stream_usage_chunk_keeps_cached_usage_fields(self):
        upstream = DummyUpstream(_message_done_events_with_usage("cached usage"))
        chunks = list(
            sse_translate_chat(
                upstream,
                "gpt-5.4",
                0,
                reasoning_compat="current",
                include_usage=True,
            )
        )

        payloads = []
        for chunk in chunks:
            text = chunk.decode("utf-8")
            if not text.startswith("data: ") or "[DONE]" in text:
                continue
            payloads.append(json.loads(text[len("data: ") :].strip()))

        usage_chunks = [entry for entry in payloads if entry.get("usage")]
        self.assertTrue(usage_chunks)
        self.assertEqual(usage_chunks[-1]["usage"]["prompt_tokens"], 120)
        self.assertEqual(usage_chunks[-1]["usage"]["completion_tokens"], 45)
        self.assertEqual(usage_chunks[-1]["usage"]["prompt_tokens_details"]["cached_tokens"], 80)
        self.assertEqual(usage_chunks[-1]["usage"]["prompt_cache_hit_tokens"], 80)


if __name__ == "__main__":
    unittest.main()
