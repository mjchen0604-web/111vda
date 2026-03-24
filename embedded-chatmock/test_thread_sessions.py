import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.thread_sessions import clear_thread_session, resolve_thread_session_state, save_thread_session


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class ResolveThreadSessionStateTests(unittest.TestCase):
    def tearDown(self):
        clear_thread_session("conv-123")
        clear_thread_session("sess-456")
        clear_thread_session("sess-current-only")

    def test_metadata_conversation_id_creates_session(self):
        payload = {"metadata": {"conversation_id": "conv-123"}}
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            }
        ]

        session = resolve_thread_session_state(
            payload=payload,
            input_items=input_items,
            headers=Headers(),
        )

        self.assertIsNotNone(session)
        self.assertEqual(session["session_key"], "conv-123")
        self.assertEqual(session["thread_mode"], "start")

    def test_header_session_id_creates_session(self):
        payload = {}
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            }
        ]

        session = resolve_thread_session_state(
            payload=payload,
            input_items=input_items,
            headers=Headers({"x-session-id": "sess-456"}),
        )

        self.assertIsNotNone(session)
        self.assertEqual(session["session_key"], "sess-456")

    def test_existing_session_resumes_even_when_client_only_sends_current_turn(self):
        save_thread_session(
            "sess-current-only",
            thread_id="resp_prev",
            candidate_label="acc01/auth.json",
            candidate_url="/tmp/accounts/acc01/auth.json",
            input_items=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "remember secret"}],
                }
            ],
        )

        session = resolve_thread_session_state(
            payload={"session_id": "sess-current-only"},
            input_items=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "what was the secret?"}],
                }
            ],
            headers=Headers(),
        )

        self.assertIsNotNone(session)
        self.assertEqual(session["thread_mode"], "resume")
        self.assertEqual(session["thread_id"], "resp_prev")
        self.assertEqual(len(session["turn_input_items"]), 1)
        self.assertEqual(session["turn_input_items"][0]["content"][0]["text"], "what was the secret?")


if __name__ == "__main__":
    unittest.main()
