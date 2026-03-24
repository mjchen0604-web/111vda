import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.context_compaction import maybe_compact_input_items


def _message(role: str, text: str):
    return {
        "type": "message",
        "role": role,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
    }


class ContextCompactionTests(unittest.TestCase):
    def test_default_is_disabled_without_context_management(self):
        input_items = []
        for idx in range(8):
            role = "user" if idx % 2 == 0 else "assistant"
            input_items.append(_message(role, f"turn-{idx}"))

        compacted_items, compacted_instructions, meta = maybe_compact_input_items(
            {},
            input_items,
            "Base instructions",
        )

        self.assertFalse(meta["applied"])
        self.assertEqual(meta["reason"], "disabled")
        self.assertEqual(compacted_items, input_items)
        self.assertEqual(compacted_instructions, "Base instructions")

    def test_compacts_long_history_and_keeps_recent_items(self):
        payload = {
            "context_management": {
                "max_input_items": 6,
                "preserve_recent_items": 2,
                "max_summary_chars": 1200,
            }
        }
        input_items = []
        for idx in range(8):
            role = "user" if idx % 2 == 0 else "assistant"
            input_items.append(_message(role, f"turn-{idx}"))

        compacted_items, compacted_instructions, meta = maybe_compact_input_items(
            payload,
            input_items,
            "Base instructions",
        )

        self.assertTrue(meta["applied"])
        self.assertEqual(len(compacted_items), 2)
        self.assertIn("turn-6", str(compacted_items))
        self.assertIn("turn-7", str(compacted_items))
        self.assertIsInstance(compacted_instructions, str)
        self.assertIn("[Gateway compacted conversation summary]", compacted_instructions)
        self.assertIn("turn-0", compacted_instructions)

    def test_disable_via_context_management(self):
        payload = {"context_management": {"enabled": False}}
        input_items = [_message("user", "hello")] * 16

        compacted_items, compacted_instructions, meta = maybe_compact_input_items(
            payload,
            input_items,
            "Base instructions",
        )

        self.assertFalse(meta["applied"])
        self.assertEqual(compacted_items, input_items)
        self.assertEqual(compacted_instructions, "Base instructions")

    def test_small_history_does_not_compact(self):
        payload = {"context_management": {"max_input_items": 20}}
        input_items = [_message("user", "hello"), _message("assistant", "world")]

        compacted_items, _, meta = maybe_compact_input_items(
            payload,
            input_items,
            "Base instructions",
        )

        self.assertFalse(meta["applied"])
        self.assertEqual(compacted_items, input_items)

    def test_large_body_compacts_even_when_item_count_is_small(self):
        payload = {
            "context_management": {
                "max_input_items": 20,
                "min_input_items": 12,
                "preserve_recent_items": 2,
                "max_summary_chars": 1200,
            }
        }
        large_text = "A" * 4000
        input_items = [
            _message("user", "hello"),
            _message("assistant", large_text),
            _message("user", "follow-up"),
        ]

        compacted_items, compacted_instructions, meta = maybe_compact_input_items(
            payload,
            input_items,
            "Base instructions",
        )

        self.assertTrue(meta["applied"])
        self.assertEqual(len(compacted_items), 2)
        self.assertIsInstance(compacted_instructions, str)
        self.assertIn("[Gateway compacted conversation summary]", compacted_instructions)
