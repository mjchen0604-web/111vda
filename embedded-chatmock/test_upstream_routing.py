import sys
import unittest
from pathlib import Path

from flask import Flask


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.routes_dashboard import _merge_payload_settings
from chatmock.routes_openai import _resolve_web_search_mode
from chatmock.upstream import _normalize_service_tier, normalize_model_name, resolve_upstream_mode


class UpstreamRoutingTests(unittest.TestCase):
    def test_normalize_model_name_strips_fast_and_effort_suffixes(self):
        self.assertEqual(
            normalize_model_name("gpt-5.4-fast-low"),
            "gpt-5.4",
        )
        self.assertEqual(
            normalize_model_name("gpt-5-medium"),
            "gpt-5",
        )

    def test_default_web_search_never_auto_enables(self):
        app = Flask(__name__)
        app.config["DEFAULT_WEB_SEARCH"] = True
        with app.app_context():
            mode = _resolve_web_search_mode({}, [], [])
        self.assertEqual(mode, "disabled")

    def test_fast_service_tier_maps_to_public_priority(self):
        self.assertEqual(_normalize_service_tier("fast"), "priority")
        self.assertEqual(_normalize_service_tier("priority"), "priority")
        self.assertEqual(_normalize_service_tier("flex"), "flex")

    def test_auto_mode_keeps_fast_requests_on_chatgpt_backend(self):
        self.assertEqual(
            resolve_upstream_mode("auto", "gpt-5.4-fast-low", "fast"),
            "chatgpt-backend",
        )

    def test_dashboard_settings_force_web_search_off(self):
        current = {
            "routingStrategy": "round-robin",
            "requestRetry": 0,
            "maxRetryInterval": 5,
            "reasoningEffort": "medium",
            "reasoningSummary": "auto",
            "reasoningCompat": "think-tags",
            "exposeReasoningModels": False,
            "enableWebSearch": True,
            "verbose": False,
            "verboseObfuscation": False,
            "httpProxy": "",
            "httpsProxy": "",
            "allProxy": "",
            "noProxy": "",
            "chatgptAuthAccessToken": "",
            "chatgptAuthAccountId": "",
            "chatgptAuthPlanType": "",
            "uploadReplaceDefault": False,
            "authFiles": [],
        }
        merged = _merge_payload_settings({"enableWebSearch": True}, current)
        self.assertFalse(merged["enableWebSearch"])


if __name__ == "__main__":
    unittest.main()
