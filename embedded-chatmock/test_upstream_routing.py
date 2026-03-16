import sys
import unittest
from pathlib import Path

from flask import Flask


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.routes_dashboard import _merge_payload_settings
from chatmock.routes_openai import _resolve_web_search_mode
from chatmock.upstream import normalize_model_name


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
