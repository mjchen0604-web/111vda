import sys
import unittest
from pathlib import Path

from flask import Flask


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.routes_dashboard import _merge_payload_settings
from chatmock.routes_openai import _instructions_for_model, _resolve_bridge_instructions, _resolve_web_search_mode
from chatmock.upstream_errors import extract_retry_after_unlock_ts
from chatmock.upstream import (
    _build_invalid_request_retry_payloads,
    _is_generic_invalid_request,
    _minimize_responses_payload,
    normalize_model_name,
    resolve_upstream_mode,
)


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

    def test_invalid_request_fallback_payloads_strip_optional_fields_progressively(self):
        payload = {
            "model": "gpt-5.4",
            "previous_response_id": "resp_123",
            "include": ["reasoning.encrypted_content", "other"],
            "service_tier": "priority",
            "parallel_tool_calls": True,
            "tool_choice": {"type": "function", "name": "tool_1"},
        }
        variants = _build_invalid_request_retry_payloads(payload)
        self.assertGreaterEqual(len(variants), 4)
        self.assertNotIn("previous_response_id", variants[0])
        self.assertEqual(variants[1].get("include"), ["other"])
        self.assertNotIn("service_tier", variants[2])
        self.assertFalse(variants[3].get("parallel_tool_calls"))
        self.assertEqual(variants[-1].get("tool_choice"), "auto")

    def test_generic_invalid_request_detector_accepts_sanitized_400(self):
        info = {
            "raw_status": 400,
            "raw_code": "",
            "raw_message": "Invalid request",
            "raw_body": {"error": {"message": "Invalid request", "type": "invalid_request_error", "param": "", "code": None}},
        }
        self.assertTrue(_is_generic_invalid_request(info))

    def test_extract_retry_after_unlock_ts_ignores_upgrade_suffix(self):
        info = {
            "raw_status": 429,
            "raw_message": (
                "You have reached your included credits usage limit. "
                "Try again at Mar 21, 2026 9:00 AM or upgrade to Plus to continue using Codex."
            ),
            "raw_body": None,
        }
        unlock_ts = extract_retry_after_unlock_ts(info)
        self.assertIsNotNone(unlock_ts)

    def test_minimize_responses_payload_drops_unused_defaults(self):
        payload = {
            "model": "gpt-5.4",
            "instructions": "",
            "tools": [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": "sess_123",
        }
        minimized = _minimize_responses_payload(payload)
        self.assertEqual(minimized, {"model": "gpt-5.4", "store": False, "prompt_cache_key": "sess_123"})

    def test_responses_extra_payload_does_not_passthrough_store(self):
        from chatmock.routes_openai import _build_responses_extra_payload

        extra = _build_responses_extra_payload(
            {
                "store": True,
                "prompt_cache_key": "sess_123",
                "temperature": 0.2,
            }
        )
        self.assertNotIn("store", extra)
        self.assertEqual(extra["prompt_cache_key"], "sess_123")
        self.assertEqual(extra["temperature"], 0.2)


if __name__ == "__main__":
    unittest.main()
