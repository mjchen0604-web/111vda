import json
import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.app import create_app
from chatmock.conversation_history import clear_conversation_history
from chatmock.responses_session import should_skip_compaction_for_thread_resume
import chatmock.routes_openai as routes_openai
from chatmock.thread_sessions import clear_thread_session
from chatmock.upstream_errors import normalized_error_payload


class DummyUpstream:
    status_code = 200
    chatmock_source = "test"

    def __init__(self, response_payload):
        self._lines = [
            f"data: {json.dumps({'type': 'response.completed', 'response': response_payload}, ensure_ascii=False)}".encode(
                "utf-8"
            )
        ]

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            if decode_unicode:
                yield line.decode("utf-8", errors="ignore")
            else:
                yield line

    def close(self):
        return None

    def mark_success(self):
        return None

    def mark_failure(self, *args, **kwargs):
        return None


class DummyFailedUpstream:
    status_code = 200
    chatmock_source = "test"

    def __init__(self):
        self._lines = [
            (
                "data: "
                + json.dumps(
                    {
                        "type": "response.failed",
                        "response": {
                            "error": {
                                "message": "previous_response_not_found",
                                "code": "previous_response_not_found",
                                "type": "invalid_request_error",
                            }
                        },
                    },
                    ensure_ascii=False,
                )
            ).encode("utf-8")
        ]

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            if decode_unicode:
                yield line.decode("utf-8", errors="ignore")
            else:
                yield line

    def close(self):
        return None

    def mark_success(self):
        return None

    def mark_failure(self, *args, **kwargs):
        return None


class ResponsesRouteTests(unittest.TestCase):
    def setUp(self):
        clear_thread_session("sess-chain")
        clear_thread_session("sess-retry")
        clear_conversation_history("sess-chain")
        clear_conversation_history("sess-retry")
        clear_conversation_history("sess-history")
        self.app = create_app()
        self.client = self.app.test_client()

    def test_v1_responses_nonstream_returns_response_payload(self):
        captured = {}

        def stub(model, input_items, *, instructions=None, extra_payload=None, **kwargs):
            captured["model"] = model
            captured["input_items"] = input_items
            captured["instructions"] = instructions
            captured["extra_payload"] = extra_payload
            captured["reasoning_param"] = kwargs.get("reasoning_param")
            captured["service_tier"] = kwargs.get("service_tier")
            return (
                DummyUpstream(
                    {
                        "id": "resp_test",
                        "object": "response",
                        "created_at": 123,
                        "status": "completed",
                        "model": model,
                        "service_tier": "fast",
                        "system_fingerprint": "fp_secret",
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ),
                None,
            )

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "previous_response_id": "resp_prev",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        }
                    ],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["id"], "resp_test")
        self.assertEqual(body["model"], "gpt-5.4")
        self.assertNotIn("service_tier", body)
        self.assertNotIn("system_fingerprint", body)
        self.assertEqual(captured["extra_payload"]["previous_response_id"], "resp_prev")

    def test_v1_responses_stream_sanitizes_client_visible_metadata(self):
        def stub(model, input_items, *, instructions=None, extra_payload=None, **kwargs):
            return (
                DummyUpstream(
                    {
                        "id": "resp_stream",
                        "object": "response",
                        "created_at": 123,
                        "status": "completed",
                        "model": model,
                        "service_tier": "fast",
                        "system_fingerprint": "fp_secret",
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ),
                None,
            )

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4-fast",
                    "stream": True,
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        }
                    ],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        body = b"".join(resp.response).decode("utf-8", errors="ignore")
        self.assertIn('"model": "gpt-5.4-fast"', body)
        self.assertNotIn('"service_tier"', body)
        self.assertNotIn("system_fingerprint", body)

    def test_v1_responses_defaults_to_stream_when_stream_omitted(self):
        def stub(model, input_items, *, instructions=None, extra_payload=None, **kwargs):
            return (
                DummyUpstream(
                    {
                        "id": "resp_default_stream",
                        "object": "response",
                        "created_at": 123,
                        "status": "completed",
                        "model": model,
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ),
                None,
            )

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        }
                    ],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "text/event-stream")

    def test_v1_responses_infers_reasoning_and_fast_from_model_alias(self):
        captured = {}

        def stub(model, input_items, *, instructions=None, extra_payload=None, **kwargs):
            captured["model"] = model
            captured["reasoning_param"] = kwargs.get("reasoning_param")
            captured["service_tier"] = kwargs.get("service_tier")
            captured["extra_payload"] = dict(extra_payload or {})
            return (
                DummyUpstream(
                    {
                        "id": "resp_alias",
                        "object": "response",
                        "created_at": 123,
                        "status": "completed",
                        "model": model,
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ),
                None,
            )

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4-fast-xhigh",
                    "stream": False,
                    "max_output_tokens": 16,
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        }
                    ],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["model"], "gpt-5.4-fast-xhigh")
        self.assertNotIn("service_tier", body)
        self.assertEqual(captured["model"], "gpt-5.4")
        self.assertEqual(captured["service_tier"], "priority")
        self.assertEqual(captured["reasoning_param"], {"effort": "xhigh", "summary": "auto"})
        self.assertNotIn("max_output_tokens", captured["extra_payload"])

    def test_v1_responses_cleans_cherry_style_history_before_upstream(self):
        captured = {}

        def stub(model, input_items, *, instructions=None, extra_payload=None, **kwargs):
            captured["model"] = model
            captured["input_items"] = input_items
            captured["extra_payload"] = dict(extra_payload or {})
            captured["service_tier"] = kwargs.get("service_tier")
            return (
                DummyUpstream(
                    {
                        "id": "resp_cherry",
                        "object": "response",
                        "created_at": 123,
                        "status": "completed",
                        "model": model,
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ),
                None,
            )

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4-fast-xhigh",
                    "stream": False,
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": "今日国际形势如何"}],
                        },
                        {
                            "type": "reasoning",
                            "id": "rs_123",
                            "encrypted_content": None,
                            "summary": [],
                        },
                        {
                            "type": "function_call",
                            "call_id": "call_123",
                            "name": "builtin_web_search",
                            "arguments": "{\"q\":\"news\"}",
                            "id": "fc_123",
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "call_123",
                            "output": [
                                {"type": "input_text", "text": "search result"},
                            ],
                        },
                    ],
                    "temperature": "[undefined]",
                    "top_p": "[undefined]",
                    "conversation": "[undefined]",
                    "previous_response_id": "[undefined]",
                    "prompt_cache_key": "[undefined]",
                    "service_tier": "[undefined]",
                    "instructions": "[undefined]",
                    "store": False,
                    "include": ["reasoning.encrypted_content"],
                    "tools": [
                        {
                            "type": "function",
                            "name": "builtin_web_search",
                            "parameters": {"type": "object"},
                        }
                    ],
                    "tool_choice": "auto",
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["model"], "gpt-5.4")
        self.assertEqual(captured["service_tier"], "priority")
        self.assertEqual(len(captured["input_items"]), 2)
        self.assertEqual(captured["input_items"][0]["type"], "message")
        self.assertEqual(captured["input_items"][0]["role"], "user")
        self.assertEqual(captured["input_items"][0]["content"][0]["type"], "input_text")
        self.assertEqual(captured["input_items"][1]["type"], "message")
        self.assertIn("[tool_result:call_123]", captured["input_items"][1]["content"][0]["text"])
        self.assertNotIn("previous_response_id", captured["extra_payload"])
        self.assertNotIn("conversation", captured["extra_payload"])
        self.assertNotIn("prompt_cache_key", captured["extra_payload"])
        self.assertNotIn("instructions", captured["extra_payload"])

    def test_v1_responses_compact_returns_local_summary(self):
        resp = self.client.post(
            "/v1/responses/compact",
            json={
                "model": "gpt-5.4",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hello"}],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "world"}],
                    },
                ],
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["object"], "response.compaction")
        self.assertEqual(body["output"][0]["type"], "summary_text")

    def test_v1_responses_keeps_full_history_by_default(self):
        calls = []

        def stub(model, input_items, *, instructions=None, extra_payload=None, **kwargs):
            calls.append(
                {
                    "model": model,
                    "input_items": input_items,
                    "extra_payload": dict(extra_payload or {}),
                }
            )
            response_id = "resp_first" if len(calls) == 1 else "resp_second"
            return (
                DummyUpstream(
                    {
                        "id": response_id,
                        "object": "response",
                        "created_at": 123,
                        "status": "completed",
                        "model": model,
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ),
                None,
            )

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            first = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-chain",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        }
                    ],
                },
            )
            second = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-chain",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        },
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "previous answer"}],
                        },
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "new turn"}],
                        },
                    ],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])
        self.assertEqual(len(calls[1]["input_items"]), 3)

    def test_v1_responses_replays_history_when_client_only_sends_current_turn(self):
        calls = []

        def stub(model, input_items, *, instructions=None, extra_payload=None, **kwargs):
            calls.append(
                {
                    "model": model,
                    "input_items": input_items,
                    "extra_payload": dict(extra_payload or {}),
                }
            )
            response_id = "resp_hist_1" if len(calls) == 1 else "resp_hist_2"
            output = [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "remembered"}],
                }
            ]
            return (
                DummyUpstream(
                    {
                        "id": response_id,
                        "object": "response",
                        "created_at": 123,
                        "status": "completed",
                        "model": model,
                        "output": output,
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ),
                None,
            )

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-history",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "remember secret"}],
                        }
                    ],
                },
            )
            second = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-history",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "what was the secret?"}],
                        }
                    ],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])
        self.assertEqual(len(calls[1]["input_items"]), 3)
        self.assertEqual(calls[1]["input_items"][0]["content"][0]["text"], "remember secret")
        self.assertEqual(calls[1]["input_items"][1]["content"][0]["text"], "remembered")
        self.assertEqual(calls[1]["input_items"][2]["content"][0]["text"], "what was the secret?")

    def test_thread_resume_skips_compaction(self):
        payload = {
            "session_id": "sess-chain",
            "context_management": {"max_input_items": 2},
            "previous_response_id": "resp_prev",
        }
        self.assertTrue(
            should_skip_compaction_for_thread_resume(
                payload,
                {"thread_id": "resp_prev"},
            )
        )

    def test_v1_responses_retries_once_without_previous_response_id(self):
        calls = []

        def stub(model, input_items, *, instructions=None, extra_payload=None, **kwargs):
            calls.append(
                {
                    "model": model,
                    "input_items": input_items,
                    "extra_payload": dict(extra_payload or {}),
                }
            )
            if len(calls) == 1:
                return DummyFailedUpstream(), None
            return (
                DummyUpstream(
                    {
                        "id": "resp_retry",
                        "object": "response",
                        "created_at": 123,
                        "status": "completed",
                        "model": model,
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ),
                None,
            )

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-retry",
                    "previous_response_id": "resp_missing",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        }
                    ],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["id"], "resp_retry")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["extra_payload"].get("previous_response_id"), "resp_missing")
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])

    def test_v1_responses_shallow_mode_does_not_resume_thread_state(self):
        calls = []
        app = create_app()
        app.config["SHALLOW_GRAFT_MODE"] = True
        client = app.test_client()

        def stub(model, input_items, *, instructions=None, extra_payload=None, **kwargs):
            calls.append(
                {
                    "model": model,
                    "input_items": input_items,
                    "extra_payload": dict(extra_payload or {}),
                }
            )
            response_id = "resp_shallow_1" if len(calls) == 1 else "resp_shallow_2"
            return (
                DummyUpstream(
                    {
                        "id": response_id,
                        "object": "response",
                        "created_at": 123,
                        "status": "completed",
                        "model": model,
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ),
                None,
            )

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-chain",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        }
                    ],
                },
            )
            client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-chain",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "hello"}],
                        },
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "previous answer"}],
                        },
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "new turn"}],
                        },
                    ],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(len(calls), 2)
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])
        self.assertEqual(len(calls[1]["input_items"]), 3)

    def test_client_metadata_minimization_sanitizes_error_payload(self):
        with self.app.app_context():
            payload = normalized_error_payload(
                {
                    "raw_status": 500,
                    "raw_message": "codex app-server previous_response_not_found",
                    "raw_code": "previous_response_not_found",
                }
            )

        self.assertEqual(payload["message"], "The server had an error while processing your request.")
        self.assertIsNone(payload["code"])


if __name__ == "__main__":
    unittest.main()
