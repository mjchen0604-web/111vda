import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.utils import (
    ManagedAuthUpstream,
    _clear_invalid_auth_candidate,
    _dedupe_candidates_by_account_id,
    _remove_label_state,
    _parse_auth_files_env,
    _preferred_chatgpt_auth_candidate_for_session,
    bind_chatgpt_auth_session,
    clear_chatgpt_auth_session_binding,
    get_chatgpt_auth_session_binding,
    handle_chatgpt_candidate_failure,
    is_auth_candidate_blocked,
    probe_chatgpt_auth_candidates_and_quarantine_invalid,
    remove_chatgpt_auth_candidate,
)


class AuthPoolBehaviorTests(unittest.TestCase):
    def tearDown(self):
        clear_chatgpt_auth_session_binding("sess-sticky")
        for label in ("acc01/auth.json", "acc02/auth.json"):
            _remove_label_state(label)
            _clear_invalid_auth_candidate(label=label)

    def test_same_account_id_with_different_sources_is_not_deduped(self):
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
        self.assertEqual(len(deduped), 2)

    def test_parse_auth_files_env_discovers_new_auth_files_under_same_root(self):
        original_auth_files = os.environ.get("CHATGPT_LOCAL_AUTH_FILES")
        original_data_dir = os.environ.get("CHATMOCK_DATA_DIR")
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

    def test_rate_limited_candidate_only_cools_down_current_credential(self):
        candidate1 = {"label": "acc01/auth.json", "account_id": "same-account"}
        candidate2 = {"label": "acc02/auth.json", "account_id": "same-account"}
        info = {
            "raw_status": 429,
            "raw_message": "Rate limit exceeded",
            "raw_code": "rate_limit_exceeded",
        }
        handle_chatgpt_candidate_failure(candidate1, info)
        self.assertTrue(is_auth_candidate_blocked(candidate1))
        self.assertFalse(is_auth_candidate_blocked(candidate2))

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

if __name__ == "__main__":
    unittest.main()
