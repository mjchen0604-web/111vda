import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.utils import (
    ManagedAuthUpstream,
    _clear_invalid_auth_candidate,
    _candidate_codex_pressure_score,
    _dedupe_candidates_by_account_id,
    _remove_account_state,
    _remove_label_state,
    _preferred_chatgpt_auth_candidate_for_hint,
    _state_for_label,
    _parse_auth_files_env,
    _preferred_chatgpt_auth_candidate_for_session,
    bind_chatgpt_auth_session,
    clear_chatgpt_auth_session_binding,
    get_chatgpt_auth_session_binding,
    handle_chatgpt_candidate_failure,
    is_auth_candidate_blocked,
    mark_chatgpt_auth_result,
    probe_chatgpt_auth_candidates_and_quarantine_invalid,
    remove_chatgpt_auth_candidate,
    update_chatgpt_candidate_rate_limits,
)
from chatmock.connection_slots import (
    acquire_chatgpt_connection_slot,
    clear_chatgpt_connection_slots,
    get_chatgpt_connection_slot_state,
    release_chatgpt_connection_slot,
)


class AuthPoolBehaviorTests(unittest.TestCase):
    def tearDown(self):
        clear_chatgpt_auth_session_binding("sess-sticky")
        clear_chatgpt_connection_slots()
        for label in ("acc01/auth.json", "acc02/auth.json"):
            _remove_label_state(label)
            _clear_invalid_auth_candidate(label=label)
        _remove_account_state("same-account")

    def test_same_account_id_with_different_sources_is_deduped(self):
        candidates = [
            {
                "label": "acc01/auth.json",
                "account_id": "same-account",
                "source_path": "/tmp/accounts/acc01/auth.json",
            },
            {
                "label": "acc02/auth.json",
                "account_id": "same-account",
                "source_path": "/tmp/accounts/acc02/auth.json",
            },
        ]
        deduped = _dedupe_candidates_by_account_id(candidates)
        self.assertEqual(len(deduped), 1)

    def test_parse_auth_files_env_discovers_new_auth_files_under_same_root(self):
        original_auth_files = os.environ.get("CHATGPT_LOCAL_AUTH_FILES")
        original_data_dir = os.environ.get("CHATMOCK_DATA_DIR")
        original_configured = os.environ.get("CHATGPT_LOCAL_AUTH_FILES_CONFIGURED")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                accounts_root = Path(temp_dir) / "accounts"
                for label in ("acc01", "acc02"):
                    auth_dir = accounts_root / label
                    auth_dir.mkdir(parents=True, exist_ok=True)
                    (auth_dir / "auth.json").write_text(json.dumps({"tokens": {"account_id": label}}), encoding="utf-8")

                os.environ["CHATMOCK_DATA_DIR"] = temp_dir
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = str(accounts_root / "acc01" / "auth.json")

                paths = _parse_auth_files_env()
                self.assertIn(str(accounts_root / "acc01" / "auth.json"), paths)
                self.assertIn(str(accounts_root / "acc02" / "auth.json"), paths)
        finally:
            if original_auth_files is None:
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
            else:
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = original_auth_files
            if original_data_dir is None:
                os.environ.pop("CHATMOCK_DATA_DIR", None)
            else:
                os.environ["CHATMOCK_DATA_DIR"] = original_data_dir
            if original_configured is None:
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES_CONFIGURED", None)
            else:
                os.environ["CHATGPT_LOCAL_AUTH_FILES_CONFIGURED"] = original_configured

    def test_parse_auth_files_env_respects_explicit_config_without_rediscovery(self):
        original_auth_files = os.environ.get("CHATGPT_LOCAL_AUTH_FILES")
        original_data_dir = os.environ.get("CHATMOCK_DATA_DIR")
        original_configured = os.environ.get("CHATGPT_LOCAL_AUTH_FILES_CONFIGURED")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                accounts_root = Path(temp_dir) / "accounts"
                for label in ("acc01", "acc02"):
                    auth_dir = accounts_root / label
                    auth_dir.mkdir(parents=True, exist_ok=True)
                    (auth_dir / "auth.json").write_text(json.dumps({"tokens": {"account_id": label}}), encoding="utf-8")

                os.environ["CHATMOCK_DATA_DIR"] = temp_dir
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = str(accounts_root / "acc01" / "auth.json")
                os.environ["CHATGPT_LOCAL_AUTH_FILES_CONFIGURED"] = "1"

                paths = _parse_auth_files_env()
                self.assertEqual(paths, [str(accounts_root / "acc01" / "auth.json")])
        finally:
            if original_auth_files is None:
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
            else:
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = original_auth_files
            if original_data_dir is None:
                os.environ.pop("CHATMOCK_DATA_DIR", None)
            else:
                os.environ["CHATMOCK_DATA_DIR"] = original_data_dir
            if original_configured is None:
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES_CONFIGURED", None)
            else:
                os.environ["CHATGPT_LOCAL_AUTH_FILES_CONFIGURED"] = original_configured

    def test_session_sticky_prefers_bound_candidate(self):
        candidates = [
            {"label": "acc01/auth.json", "account_id": "same-account", "source_path": "/tmp/accounts/acc01/auth.json"},
            {"label": "acc02/auth.json", "account_id": "same-account", "source_path": "/tmp/accounts/acc02/auth.json"},
        ]
        bind_chatgpt_auth_session("sess-sticky", candidates[1])
        preferred = _preferred_chatgpt_auth_candidate_for_session(candidates, "sess-sticky")
        self.assertIsNotNone(preferred)
        self.assertEqual(preferred["label"], "acc02/auth.json")

    def test_managed_upstream_success_binds_session(self):
        class DummyUpstream:
            status_code = 200

            def close(self):
                return None

        candidate = {
            "label": "acc01/auth.json",
            "account_id": "acc01",
            "source_path": "/tmp/accounts/acc01/auth.json",
        }
        upstream = ManagedAuthUpstream(DummyUpstream(), candidate, session_id="sess-sticky")
        upstream.mark_success()
        binding = get_chatgpt_auth_session_binding("sess-sticky")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["label"], "acc01/auth.json")

    def test_connection_slot_reused_for_same_session_and_candidate(self):
        candidate = {
            "label": "acc01/auth.json",
            "account_id": "acc01",
            "source_path": "/tmp/accounts/acc01/auth.json",
        }
        slot1, session1 = acquire_chatgpt_connection_slot(candidate, "sess-sticky")
        release_chatgpt_connection_slot(slot1)
        slot2, session2 = acquire_chatgpt_connection_slot(candidate, "sess-sticky")
        release_chatgpt_connection_slot(slot2)
        self.assertEqual(slot1, slot2)
        self.assertIs(session1, session2)
        self.assertIsInstance(session1, requests.Session)

    def test_connection_slot_separates_different_sessions(self):
        candidate = {
            "label": "acc01/auth.json",
            "account_id": "acc01",
            "source_path": "/tmp/accounts/acc01/auth.json",
        }
        slot1, _ = acquire_chatgpt_connection_slot(candidate, "sess-one")
        release_chatgpt_connection_slot(slot1)
        slot2, _ = acquire_chatgpt_connection_slot(candidate, "sess-two")
        release_chatgpt_connection_slot(slot2)
        state = get_chatgpt_connection_slot_state()
        self.assertNotEqual(slot1, slot2)
        self.assertIn(slot1, state)
        self.assertIn(slot2, state)

    def test_thread_hint_prefers_matching_candidate(self):
        candidates = [
            {"label": "acc01/auth.json", "account_id": "same-account", "source_path": "/tmp/accounts/acc01/auth.json"},
            {"label": "acc02/auth.json", "account_id": "same-account", "source_path": "/tmp/accounts/acc02/auth.json"},
        ]
        preferred = _preferred_chatgpt_auth_candidate_for_hint(
            candidates,
            "acc02/auth.json",
            None,
        )
        self.assertIsNotNone(preferred)
        self.assertEqual(preferred["label"], "acc02/auth.json")

    def test_rate_limited_candidate_cools_down_same_account_duplicates(self):
        candidate1 = {"label": "acc01/auth.json", "account_id": "same-account"}
        candidate2 = {"label": "acc02/auth.json", "account_id": "same-account"}
        info = {
            "raw_status": 429,
            "raw_message": "Rate limit exceeded",
            "raw_code": "rate_limit_exceeded",
        }
        handle_chatgpt_candidate_failure(candidate1, info)
        self.assertTrue(is_auth_candidate_blocked(candidate1))
        self.assertTrue(is_auth_candidate_blocked(candidate2))

    def test_codex_limit_headers_block_only_exhausted_candidate(self):
        candidate1 = {"label": "acc01/auth.json", "account_id": "same-account"}
        candidate2 = {"label": "acc02/auth.json", "account_id": "same-account"}
        update_chatgpt_candidate_rate_limits(
            "acc01/auth.json",
            primary_used_percent=100.0,
            primary_window_minutes=10080,
            primary_resets_in_seconds=3600,
        )
        update_chatgpt_candidate_rate_limits(
            "acc02/auth.json",
            primary_used_percent=20.0,
            primary_window_minutes=10080,
            primary_resets_in_seconds=3600,
        )
        self.assertTrue(is_auth_candidate_blocked(candidate1))
        self.assertFalse(is_auth_candidate_blocked(candidate2))

    def test_codex_pressure_score_prefers_lower_usage_candidate(self):
        candidate1 = {"label": "acc01/auth.json", "account_id": "same-account"}
        candidate2 = {"label": "acc02/auth.json", "account_id": "same-account"}
        update_chatgpt_candidate_rate_limits(
            "acc01/auth.json",
            primary_used_percent=92.0,
            secondary_used_percent=88.0,
        )
        update_chatgpt_candidate_rate_limits(
            "acc02/auth.json",
            primary_used_percent=10.0,
            secondary_used_percent=15.0,
        )
        self.assertGreater(_candidate_codex_pressure_score(candidate1), _candidate_codex_pressure_score(candidate2))

    def test_remove_candidate_only_removes_targeted_auth_file(self):
        original_auth_files = os.environ.get("CHATGPT_LOCAL_AUTH_FILES")
        original_data_dir = os.environ.get("CHATMOCK_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                accounts_root = Path(temp_dir) / "accounts"
                auth1 = accounts_root / "acc01" / "auth.json"
                auth2 = accounts_root / "acc02" / "auth.json"
                auth1.parent.mkdir(parents=True, exist_ok=True)
                auth2.parent.mkdir(parents=True, exist_ok=True)
                payload = {"tokens": {"account_id": "same-account"}}
                auth1.write_text(json.dumps(payload), encoding="utf-8")
                auth2.write_text(json.dumps(payload), encoding="utf-8")
                os.environ["CHATMOCK_DATA_DIR"] = temp_dir
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = f"{auth1},{auth2}"

                removed = remove_chatgpt_auth_candidate(
                    {
                        "label": "acc01/auth.json",
                        "account_id": "same-account",
                        "source_kind": "auth_file",
                        "source_path": str(auth1),
                    },
                    reason="test",
                )
                self.assertTrue(removed)
                self.assertFalse(auth1.exists())
                self.assertTrue(auth2.exists())
                self.assertEqual(os.environ.get("CHATGPT_LOCAL_AUTH_FILES"), str(auth2))
        finally:
            if original_auth_files is None:
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
            else:
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = original_auth_files
            if original_data_dir is None:
                os.environ.pop("CHATMOCK_DATA_DIR", None)
            else:
                os.environ["CHATMOCK_DATA_DIR"] = original_data_dir

    def test_remove_default_auth_clears_env_and_persisted_settings(self):
        original_access = os.environ.get("CHATMOCK_CODEX_ACCESS_TOKEN")
        original_account = os.environ.get("CHATMOCK_CODEX_ACCOUNT_ID")
        original_plan = os.environ.get("CHATMOCK_CODEX_PLAN_TYPE")
        original_settings_path = os.environ.get("CHATMOCK_DASHBOARD_SETTINGS_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                settings_path = Path(temp_dir) / "dashboard-settings.json"
                settings_path.write_text(
                    json.dumps(
                        {
                            "chatgptAuthAccessToken": "token-123",
                            "chatgptAuthAccountId": "acct-123",
                            "chatgptAuthPlanType": "team",
                        }
                    ),
                    encoding="utf-8",
                )
                os.environ["CHATMOCK_DASHBOARD_SETTINGS_PATH"] = str(settings_path)
                os.environ["CHATMOCK_CODEX_ACCESS_TOKEN"] = "token-123"
                os.environ["CHATMOCK_CODEX_ACCOUNT_ID"] = "acct-123"
                os.environ["CHATMOCK_CODEX_PLAN_TYPE"] = "team"

                removed = remove_chatgpt_auth_candidate(
                    {
                        "label": "default",
                        "account_id": "acct-123",
                        "source_kind": "default_auth",
                        "source_path": "",
                    },
                    reason="invalid_api_key",
                )
                self.assertTrue(removed)
                self.assertFalse(os.environ.get("CHATMOCK_CODEX_ACCESS_TOKEN"))
                self.assertFalse(os.environ.get("CHATMOCK_CODEX_ACCOUNT_ID"))
                self.assertFalse(os.environ.get("CHATMOCK_CODEX_PLAN_TYPE"))

                saved = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertEqual(saved.get("chatgptAuthAccessToken"), "")
                self.assertEqual(saved.get("chatgptAuthAccountId"), "")
                self.assertEqual(saved.get("chatgptAuthPlanType"), "")
        finally:
            if original_access is None:
                os.environ.pop("CHATMOCK_CODEX_ACCESS_TOKEN", None)
            else:
                os.environ["CHATMOCK_CODEX_ACCESS_TOKEN"] = original_access
            if original_account is None:
                os.environ.pop("CHATMOCK_CODEX_ACCOUNT_ID", None)
            else:
                os.environ["CHATMOCK_CODEX_ACCOUNT_ID"] = original_account
            if original_plan is None:
                os.environ.pop("CHATMOCK_CODEX_PLAN_TYPE", None)
            else:
                os.environ["CHATMOCK_CODEX_PLAN_TYPE"] = original_plan
            if original_settings_path is None:
                os.environ.pop("CHATMOCK_DASHBOARD_SETTINGS_PATH", None)
            else:
                os.environ["CHATMOCK_DASHBOARD_SETTINGS_PATH"] = original_settings_path

    def test_probe_quarantines_only_invalid_credential(self):
        original_auth_files = os.environ.get("CHATGPT_LOCAL_AUTH_FILES")
        original_data_dir = os.environ.get("CHATMOCK_DATA_DIR")
        original_settings_path = os.environ.get("CHATMOCK_DASHBOARD_SETTINGS_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                accounts_root = Path(temp_dir) / "accounts"
                settings_path = Path(temp_dir) / "dashboard-settings.json"
                auth1 = accounts_root / "acc01" / "auth.json"
                auth2 = accounts_root / "acc02" / "auth.json"
                auth1.parent.mkdir(parents=True, exist_ok=True)
                auth2.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "tokens": {
                        "account_id": "same-account",
                        "access_token": "token-value",
                    }
                }
                auth1.write_text(json.dumps(payload), encoding="utf-8")
                auth2.write_text(json.dumps(payload), encoding="utf-8")
                os.environ["CHATMOCK_DATA_DIR"] = temp_dir
                os.environ["CHATMOCK_DASHBOARD_SETTINGS_PATH"] = str(settings_path)
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = f"{auth1},{auth2}"

                def fake_probe(candidate):
                    if candidate.get("source_path") == str(auth1):
                        return {
                            "raw_status": 401,
                            "raw_message": "Account unavailable",
                            "raw_code": "invalid_api_key",
                        }
                    return {
                        "raw_status": 200,
                        "raw_message": "ok",
                    }

                with patch("chatmock.utils._probe_chatgpt_candidate", side_effect=fake_probe):
                    result = probe_chatgpt_auth_candidates_and_quarantine_invalid()

                self.assertEqual(result["quarantined"], 1)
                active_auths = os.environ.get("CHATGPT_LOCAL_AUTH_FILES", "")
                self.assertNotIn(str(auth1), active_auths)
                self.assertIn(str(auth2), active_auths)
        finally:
            if original_auth_files is None:
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
            else:
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = original_auth_files
            if original_data_dir is None:
                os.environ.pop("CHATMOCK_DATA_DIR", None)
            else:
                os.environ["CHATMOCK_DATA_DIR"] = original_data_dir
            if original_settings_path is None:
                os.environ.pop("CHATMOCK_DASHBOARD_SETTINGS_PATH", None)
            else:
                os.environ["CHATMOCK_DASHBOARD_SETTINGS_PATH"] = original_settings_path

    def test_probe_survives_single_credential_exception(self):
        original_auth_files = os.environ.get("CHATGPT_LOCAL_AUTH_FILES")
        original_data_dir = os.environ.get("CHATMOCK_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                accounts_root = Path(temp_dir) / "accounts"
                auth1 = accounts_root / "acc01" / "auth.json"
                auth2 = accounts_root / "acc02" / "auth.json"
                auth1.parent.mkdir(parents=True, exist_ok=True)
                auth2.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "tokens": {
                        "account_id": "same-account",
                        "access_token": "token-value",
                    }
                }
                auth1.write_text(json.dumps(payload), encoding="utf-8")
                auth2.write_text(json.dumps(payload), encoding="utf-8")
                os.environ["CHATMOCK_DATA_DIR"] = temp_dir
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = f"{auth1},{auth2}"

                def fake_probe(candidate):
                    if candidate.get("source_path") == str(auth1):
                        raise RuntimeError("boom")
                    return {"raw_status": 200, "raw_message": "ok"}

                with patch("chatmock.utils._probe_chatgpt_candidate", side_effect=fake_probe):
                    result = probe_chatgpt_auth_candidates_and_quarantine_invalid()

                self.assertEqual(result["scanned"], 2)
                self.assertTrue(any(item["classification"] == "probe_internal_error" for item in result["details"]))
                self.assertTrue(any(item["classification"] == "ready" for item in result["details"]))
        finally:
            if original_auth_files is None:
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
            else:
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = original_auth_files
            if original_data_dir is None:
                os.environ.pop("CHATMOCK_DATA_DIR", None)
            else:
                os.environ["CHATMOCK_DATA_DIR"] = original_data_dir

    def test_success_writeback_normalizes_non_2xx_status_to_ready_200(self):
        mark_chatgpt_auth_result(
            "acc01/auth.json",
            success=True,
            status_code=402,
            raw_code="something_old",
            raw_message="old error",
        )
        state = _state_for_label("acc01/auth.json")
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["last_status"], 200)
        self.assertEqual(state["last_classification"], "ready")
        self.assertEqual(state["last_raw_code"], "")
        self.assertEqual(state["last_raw_message"], "")

if __name__ == "__main__":
    unittest.main()
