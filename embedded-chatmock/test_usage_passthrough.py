import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.usage_passthrough import (
    normalize_usage_dict,
    to_anthropic_usage,
    to_chat_usage,
    to_responses_usage,
)


class UsagePassthroughTests(unittest.TestCase):
    def test_normalize_usage_preserves_cached_fields(self):
        raw_usage = {
            "input_tokens": 120,
            "output_tokens": 45,
            "total_tokens": 165,
            "input_tokens_details": {
                "cached_tokens": 80,
                "cached_creation_tokens": 16,
            },
            "prompt_cache_hit_tokens": 80,
        }

        normalized = normalize_usage_dict(raw_usage)

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["input_tokens"], 120)
        self.assertEqual(normalized["output_tokens"], 45)
        self.assertEqual(normalized["total_tokens"], 165)
        self.assertEqual(normalized["input_tokens_details"]["cached_tokens"], 80)
        self.assertEqual(normalized["input_tokens_details"]["cached_creation_tokens"], 16)
        self.assertEqual(normalized["prompt_cache_hit_tokens"], 80)

    def test_to_chat_usage_keeps_cached_fields(self):
        payload = to_chat_usage(
            {
                "input_tokens": 120,
                "output_tokens": 45,
                "total_tokens": 165,
                "input_tokens_details": {"cached_tokens": 80},
                "prompt_cache_hit_tokens": 80,
            }
        )

        self.assertEqual(payload["prompt_tokens"], 120)
        self.assertEqual(payload["completion_tokens"], 45)
        self.assertEqual(payload["prompt_tokens_details"]["cached_tokens"], 80)
        self.assertEqual(payload["input_tokens_details"]["cached_tokens"], 80)
        self.assertEqual(payload["prompt_cache_hit_tokens"], 80)

    def test_to_responses_usage_keeps_cached_fields(self):
        payload = to_responses_usage(
            {
                "input_tokens": 120,
                "output_tokens": 45,
                "total_tokens": 165,
                "input_tokens_details": {"cached_tokens": 80},
                "prompt_cache_hit_tokens": 80,
            }
        )

        self.assertEqual(payload["input_tokens"], 120)
        self.assertEqual(payload["output_tokens"], 45)
        self.assertEqual(payload["input_tokens_details"]["cached_tokens"], 80)
        self.assertEqual(payload["prompt_cache_hit_tokens"], 80)

    def test_to_anthropic_usage_maps_cache_read_and_creation(self):
        payload = to_anthropic_usage(
            {
                "input_tokens": 120,
                "output_tokens": 45,
                "input_tokens_details": {
                    "cached_tokens": 80,
                    "cached_creation_tokens": 16,
                },
            }
        )

        self.assertEqual(payload["input_tokens"], 120)
        self.assertEqual(payload["output_tokens"], 45)
        self.assertEqual(payload["cache_read_input_tokens"], 80)
        self.assertEqual(payload["cache_creation_input_tokens"], 16)


if __name__ == "__main__":
    unittest.main()
