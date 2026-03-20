import json
import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.session import canonicalize_prefix, ensure_session_id


def _sample_input(text: str = "hello") -> list[dict]:
    return [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }
    ]


class SessionPrefixTests(unittest.TestCase):
    def test_canonicalize_prefix_includes_tools_and_model(self):
        tools = [
            {
                "type": "function",
                "name": "lookup_weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ]
        prefix = json.loads(
            canonicalize_prefix(
                "Base instructions",
                _sample_input(),
                model="gpt-5.4",
                tools=tools,
                tool_choice={"type": "function", "function": {"name": "lookup_weather"}},
                parallel_tool_calls=True,
            )
        )

        self.assertEqual(prefix["model"], "gpt-5.4")
        self.assertEqual(prefix["instructions"], "Base instructions")
        self.assertEqual(prefix["tools"][0]["name"], "lookup_weather")
        self.assertEqual(prefix["tool_choice"]["function"]["name"], "lookup_weather")
        self.assertTrue(prefix["parallel_tool_calls"])

    def test_canonicalize_prefix_ignores_dynamic_compaction_summary(self):
        base = "Base instructions"
        compacted = (
            "Base instructions\n\n"
            "[Gateway compacted conversation summary]\n"
            "Compacted earlier input items: 8 of 12.\n"
            "Treat the summary below as background context.\n"
            "USER: old content"
        )

        plain_prefix = canonicalize_prefix(base, _sample_input("hello"), model="gpt-5.4")
        compacted_prefix = canonicalize_prefix(compacted, _sample_input("hello"), model="gpt-5.4")

        self.assertEqual(plain_prefix, compacted_prefix)

    def test_ensure_session_id_reuses_same_full_public_prefix(self):
        tools = [{"type": "function", "name": "lookup_weather", "parameters": {"type": "object"}}]
        sid_one = ensure_session_id(
            "Base instructions",
            _sample_input("hello"),
            model="gpt-5.4",
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=False,
        )
        sid_two = ensure_session_id(
            "Base instructions\n\n[Gateway compacted conversation summary]\nUSER: old content",
            _sample_input("hello"),
            model="gpt-5.4",
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=False,
        )

        self.assertEqual(sid_one, sid_two)


if __name__ == "__main__":
    unittest.main()
