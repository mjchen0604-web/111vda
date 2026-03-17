import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.codex_app_server import normalize_service_tier_for_codex
from chatmock.reasoning import parse_fast_mode, public_model_name, public_service_tier_name, split_model_alias
from chatmock.upstream import _normalize_service_tier


class PublicAliasTests(unittest.TestCase):
    def test_lightning_alias_maps_to_fast_internal(self):
        base, effort, service_tier = split_model_alias("gpt-5.4-lightning-high")
        self.assertEqual(base, "gpt-5.4")
        self.assertEqual(effort, "high")
        self.assertEqual(service_tier, "fast")

    def test_public_service_tier_keeps_fast_name(self):
        self.assertEqual(public_service_tier_name("fast"), "fast")
        self.assertEqual(public_service_tier_name("priority"), "priority")

    def test_service_tier_priority_no_longer_maps_back_to_fast(self):
        self.assertEqual(_normalize_service_tier("priority"), "priority")
        self.assertEqual(normalize_service_tier_for_codex("priority"), "fast")

    def test_parse_fast_mode(self):
        self.assertTrue(parse_fast_mode(True))
        self.assertTrue(parse_fast_mode("fast"))
        self.assertTrue(parse_fast_mode("true"))
        self.assertFalse(parse_fast_mode(False))
        self.assertFalse(parse_fast_mode("off"))
        self.assertIsNone(parse_fast_mode("maybe"))

    def test_public_model_name_rewrites_fast_suffix(self):
        self.assertEqual(
            public_model_name("gpt-5.4-fast-xhigh"),
            "gpt-5.4-fast-xhigh",
        )
        self.assertEqual(
            public_model_name("gpt-5.4-lightning"),
            "gpt-5.4-fast",
        )


if __name__ == "__main__":
    unittest.main()
