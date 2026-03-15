import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.thread_sessions import resolve_thread_session_state


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class ResolveThreadSessionStateTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
