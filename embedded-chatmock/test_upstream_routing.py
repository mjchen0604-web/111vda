import sys
import unittest
from pathlib import Path

from flask import Flask


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.routes_dashboard import _merge_payload_settings
from chatmock.routes_openai import _instructions_for_model, _resolve_bridge_instructions, _resolve_web_search_mode
from chatmock.upstream import normalize_model_name, resolve_upstream_mode


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

    def test_fast_and_flex_models_still_use_chatgpt_backend(self):
        self.assertEqual(resolve_upstream_mode("auto", "gpt-5.4-fast-low", None), "chatgpt-backend")
        self.assertEqual(resolve_upstream_mode("auto", "gpt-5.4", "flex"), "chatgpt-backend")

    def test_gpt54_family_uses_base_instructions(self):
        app = Flask(__name__)
        app.config["BASE_INSTRUCTIONS"] = "base-template"
        app.config["GPT5_CODEX_INSTRUCTIONS"] = "codex-template"
        with app.app_context():
            self.assertEqual(_instructions_for_model("gpt-5.4"), "base-template")
            self.assertEqual(_instructions_for_model("gpt-5.4-fast"), "base-template")
            self.assertEqual(_instructions_for_model("gpt-5-codex"), "codex-template")

    def test_native_prompt_mode_can_produce_empty_template(self):
        app = Flask(__name__)
        app.config["BASE_INSTRUCTIONS"] = "base-template"
        app.config["GPT5_CODEX_INSTRUCTIONS"] = "codex-template"
        with app.app_context():
            self.assertEqual(_resolve_bridge_instructions("gpt-5.4", {}), "base-template")
            self.assertEqual(_resolve_bridge_instructions("gpt-5.4", {"prompt_mode": "native"}), "")

    def test_dashboard_settings_force_web_search_off(self):
        current = {
            "routingStrategy": "round-robin",
            "requestRetry": 0,
            "maxRetryInterval": 5,
            "reasoningEffort": "medium",
            "reasoningSummary": "auto",
            "reasoningCompat": "current",
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
