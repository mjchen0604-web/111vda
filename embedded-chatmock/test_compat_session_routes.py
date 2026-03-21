import json
import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.app import create_app
import chatmock.routes_anthropic as routes_anthropic
import chatmock.routes_ollama as routes_ollama
import chatmock.routes_openai as routes_openai
from chatmock.thread_sessions import clear_thread_session


class DummyUpstream:
    status_code = 200
    chatmock_source = "test"

    def __init__(self, response_id: str):
        self._lines = [
            f"data: {json.dumps({'type': 'response.completed', 'response': {'id': response_id, 'usage': {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3}, 'output': []}}, ensure_ascii=False)}".encode(
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


class DummyHTTPInvalidRequestUpstream:
    status_code = 400
    reason = "Bad Request"
    chatmock_source = "chatgpt-backend"

    def __init__(self):
        body = {
            "error": {
                "message": "Invalid request",
                "type": "invalid_request_error",
                "param": "",
                "code": None,
            }
        }
        self.content = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.text = self.content.decode("utf-8")

    def close(self):
        return None


class CompatSessionRouteTests(unittest.TestCase):
    def setUp(self):
        clear_thread_session("sess-chat")
        clear_thread_session("sess-anthropic")
        clear_thread_session("sess-ollama")
        self.app = create_app()
        self.client = self.app.test_client()

    def test_fast_compat_routes_hide_service_tier(self):
        def stub(model, input_items, **kwargs):
            return DummyUpstream("resp_priority"), None

        original_openai = routes_openai.start_upstream_request
        original_anthropic = routes_anthropic.start_upstream_request
        original_ollama = routes_ollama.start_upstream_request
        routes_openai.start_upstream_request = stub
        routes_anthropic.start_upstream_request = stub
        routes_ollama.start_upstream_request = stub
        try:
            openai_resp = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-5.4-fast",
                    "stream": False,
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            anthropic_resp = self.client.post(
                "/v1/messages",
                json={
                    "model": "gpt-5.4-fast",
                    "stream": False,
                    "max_tokens": 8,
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                },
            )
            ollama_resp = self.client.post(
                "/api/chat",
                json={
                    "model": "gpt-5.4-fast",
                    "stream": False,
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        finally:
            routes_openai.start_upstream_request = original_openai
            routes_anthropic.start_upstream_request = original_anthropic
            routes_ollama.start_upstream_request = original_ollama

        self.assertEqual(openai_resp.status_code, 200)
        self.assertEqual(anthropic_resp.status_code, 200)
        self.assertEqual(ollama_resp.status_code, 200)
        self.assertNotIn("service_tier", openai_resp.get_json())
        self.assertNotIn("service_tier", anthropic_resp.get_json())
        self.assertNotIn("service_tier", ollama_resp.get_json())

    def test_ollama_fast_preserves_requested_model_alias(self):
        def stub(model, input_items, **kwargs):
            return DummyUpstream("resp_ollama_fast"), None

        original = routes_ollama.start_upstream_request
        routes_ollama.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/api/chat",
                json={
                    "model": "gpt-5.4-fast",
                    "stream": False,
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        finally:
            routes_ollama.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json().get("model"), "gpt-5.4-fast")

    def test_chat_completions_does_not_resume_previous_response_id_by_default(self):
        calls = []

        def stub(model, input_items, **kwargs):
            calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
            return DummyUpstream("resp_chat_1" if len(calls) == 1 else "resp_chat_2"), None

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-chat",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-chat",
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "prev"},
                        {"role": "user", "content": "next"},
                    ],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])
        self.assertEqual(len(calls[1]["input_items"]), 3)

    def test_anthropic_messages_does_not_resume_previous_response_id_by_default(self):
        calls = []

        def stub(model, input_items, **kwargs):
            calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
            return DummyUpstream("resp_anthropic_1" if len(calls) == 1 else "resp_anthropic_2"), None

        original = routes_anthropic.start_upstream_request
        routes_anthropic.start_upstream_request = stub
        try:
            self.client.post(
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet",
                    "stream": False,
                    "session_id": "sess-anthropic",
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                },
            )
            self.client.post(
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet",
                    "stream": False,
                    "session_id": "sess-anthropic",
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                        {"role": "assistant", "content": [{"type": "text", "text": "prev"}]},
                        {"role": "user", "content": [{"type": "text", "text": "next"}]},
                    ],
                },
            )
        finally:
            routes_anthropic.start_upstream_request = original

        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])
        self.assertEqual(len(calls[1]["input_items"]), 3)

    def test_ollama_chat_does_not_resume_previous_response_id_by_default(self):
        calls = []

        def stub(model, input_items, **kwargs):
            calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
            return DummyUpstream("resp_ollama_1" if len(calls) == 1 else "resp_ollama_2"), None

        original = routes_ollama.start_upstream_request
        routes_ollama.start_upstream_request = stub
        try:
            self.client.post(
                "/api/chat",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-ollama",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            self.client.post(
                "/api/chat",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-ollama",
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "prev"},
                        {"role": "user", "content": "next"},
                    ],
                },
            )
        finally:
            routes_ollama.start_upstream_request = original

        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])
        self.assertEqual(len(calls[1]["input_items"]), 3)

    def test_anthropic_stream_retries_without_previous_response_id(self):
        calls = []

        def stub(model, input_items, **kwargs):
            calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
            if len(calls) == 1:
                return DummyFailedUpstream(), None
            return DummyUpstream("resp_anthropic_stream"), None

        original = routes_anthropic.start_upstream_request
        routes_anthropic.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet",
                    "stream": True,
                    "previous_response_id": "resp_missing",
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                },
            )
            body = resp.get_data(as_text=True)
        finally:
            routes_anthropic.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["extra_payload"].get("previous_response_id"), "resp_missing")
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])
        self.assertNotIn("event: error", body)

    def test_responses_nonstream_retries_without_previous_response_id_on_generic_invalid_request(self):
        calls = []

        def stub(model, input_items, **kwargs):
            calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
            if len(calls) == 1:
                return DummyHTTPInvalidRequestUpstream(), None
            return DummyUpstream("resp_responses_retry"), None

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "previous_response_id": "resp_invalid",
                    "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["extra_payload"].get("previous_response_id"), "resp_invalid")
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])

    def test_anthropic_nonstream_retries_without_previous_response_id_on_generic_invalid_request(self):
        calls = []

        def stub(model, input_items, **kwargs):
            calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
            if len(calls) == 1:
                return DummyHTTPInvalidRequestUpstream(), None
            return DummyUpstream("resp_messages_retry"), None

        original = routes_anthropic.start_upstream_request
        routes_anthropic.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet",
                    "stream": False,
                    "previous_response_id": "resp_invalid",
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                },
            )
        finally:
            routes_anthropic.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["extra_payload"].get("previous_response_id"), "resp_invalid")
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])

    def test_chat_completions_nonstream_retries_without_previous_response_id_on_generic_invalid_request(self):
        calls = []

        def stub(model, input_items, **kwargs):
            calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
            if len(calls) == 1:
                return DummyHTTPInvalidRequestUpstream(), None
            return DummyUpstream("resp_chat_retry"), None

        original = routes_openai.start_upstream_request
        routes_openai.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "previous_response_id": "resp_invalid",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        finally:
            routes_openai.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["extra_payload"].get("previous_response_id"), "resp_invalid")
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])

    def test_ollama_nonstream_retries_without_previous_response_id_on_generic_invalid_request(self):
        calls = []

        def stub(model, input_items, **kwargs):
            calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
            if len(calls) == 1:
                return DummyHTTPInvalidRequestUpstream(), None
            return DummyUpstream("resp_ollama_retry"), None

        original = routes_ollama.start_upstream_request
        routes_ollama.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/api/chat",
                json={
                    "model": "gpt-5.4",
                    "stream": False,
                    "previous_response_id": "resp_invalid",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        finally:
            routes_ollama.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["extra_payload"].get("previous_response_id"), "resp_invalid")
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])

    def test_ollama_stream_retries_without_previous_response_id(self):
        calls = []

        def stub(model, input_items, **kwargs):
            calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
            if len(calls) == 1:
                return DummyFailedUpstream(), None
            return DummyUpstream("resp_ollama_stream"), None

        original = routes_ollama.start_upstream_request
        routes_ollama.start_upstream_request = stub
        try:
            resp = self.client.post(
                "/api/chat",
                json={
                    "model": "gpt-5.4",
                    "stream": True,
                    "previous_response_id": "resp_missing",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            body = resp.get_data(as_text=True)
        finally:
            routes_ollama.start_upstream_request = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["extra_payload"].get("previous_response_id"), "resp_missing")
        self.assertNotIn("previous_response_id", calls[1]["extra_payload"])
        self.assertNotIn('"error"', body)

    def test_shallow_mode_compat_routes_do_not_resume_previous_response(self):
        app = create_app()
        app.config["SHALLOW_GRAFT_MODE"] = True
        client = app.test_client()

        cases = [
            (
                routes_openai,
                "/v1/chat/completions",
                {
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-chat",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                {
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-chat",
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "prev"},
                        {"role": "user", "content": "next"},
                    ],
                },
            ),
            (
                routes_anthropic,
                "/v1/messages",
                {
                    "model": "claude-3-5-sonnet",
                    "stream": False,
                    "session_id": "sess-anthropic",
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
                },
                {
                    "model": "claude-3-5-sonnet",
                    "stream": False,
                    "session_id": "sess-anthropic",
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                        {"role": "assistant", "content": [{"type": "text", "text": "prev"}]},
                        {"role": "user", "content": [{"type": "text", "text": "next"}]},
                    ],
                },
            ),
            (
                routes_ollama,
                "/api/chat",
                {
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-ollama",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                {
                    "model": "gpt-5.4",
                    "stream": False,
                    "session_id": "sess-ollama",
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "prev"},
                        {"role": "user", "content": "next"},
                    ],
                },
            ),
        ]

        for module, path, first_payload, second_payload in cases:
            calls = []

            def stub(model, input_items, **kwargs):
                calls.append({"input_items": input_items, "extra_payload": dict(kwargs.get("extra_payload") or {})})
                return DummyUpstream("resp_shallow_1" if len(calls) == 1 else "resp_shallow_2"), None

            original = module.start_upstream_request
            module.start_upstream_request = stub
            try:
                client.post(path, json=first_payload)
                client.post(path, json=second_payload)
            finally:
                module.start_upstream_request = original

            self.assertEqual(len(calls), 2)
            self.assertNotIn("previous_response_id", calls[1]["extra_payload"])
            self.assertEqual(len(calls[1]["input_items"]), 3)


if __name__ == "__main__":
    unittest.main()
