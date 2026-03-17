import sys
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.reasoning import (
    presented_service_tier_name,
    public_model_name,
    public_service_tier_name,
    split_model_alias,
)
from chatmock.upstream import _normalize_service_tier


class PublicAliasTests(unittest.TestCase):
    def test_fast_alias_maps_to_fast_internal(self):
        base, effort, service_tier = split_model_alias("gpt-5.4-fast-high")
        self.assertEqual(base, "gpt-5.4")
        self.assertEqual(effort, "high")
        self.assertEqual(service_tier, "fast")

    def test_public_service_tier_maps_fast_to_priority(self):
        self.assertEqual(public_service_tier_name("fast"), "priority")
        self.assertEqual(public_service_tier_name("priority"), "priority")

    def test_service_tier_normalization_prefers_priority(self):
        self.assertEqual(_normalize_service_tier("priority"), "priority")
        self.assertEqual(_normalize_service_tier("fast"), "priority")

    def test_presented_service_tier_prefers_requested_priority(self):
        self.assertEqual(presented_service_tier_name("priority", "default"), "priority")
        self.assertEqual(presented_service_tier_name("fast", "default"), "priority")

    def test_public_model_name_rewrites_fast_suffix(self):
        self.assertEqual(
            public_model_name("gpt-5.4-fast-xhigh"),
            "gpt-5.4-fast-xhigh",
        )
        self.assertEqual(
            public_model_name("gpt-5.4-fast"),
            "gpt-5.4-fast",
        )


if __name__ == "__main__":
    unittest.main()
