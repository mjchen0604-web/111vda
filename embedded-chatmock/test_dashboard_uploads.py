import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.app import create_app
from chatmock.utils import _clear_invalid_auth_candidate, _mark_invalid_auth_candidate


class DashboardUploadTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        _clear_invalid_auth_candidate(label="default", account_id="bad-account")

    def test_upload_same_account_id_updates_existing_file(self):
        original_auth_files = os.environ.get("CHATGPT_LOCAL_AUTH_FILES")
        original_data_dir = os.environ.get("CHATMOCK_DATA_DIR")
        original_settings_path = os.environ.get("CHATMOCK_DASHBOARD_SETTINGS_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                accounts_root = Path(temp_dir) / "accounts"
                settings_path = Path(temp_dir) / "dashboard-settings.json"
                auth1 = accounts_root / "acc01" / "auth.json"
                auth1.parent.mkdir(parents=True, exist_ok=True)
                auth1.write_text(
                    json.dumps({"tokens": {"account_id": "same-account", "access_token": "old-token"}}),
                    encoding="utf-8",
                )
                settings_path.write_text(
                    json.dumps({"authFiles": [str(auth1)]}),
                    encoding="utf-8",
                )

                os.environ["CHATMOCK_DATA_DIR"] = temp_dir
                os.environ["CHATMOCK_DASHBOARD_SETTINGS_PATH"] = str(settings_path)
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = str(auth1)

                payload = io.BytesIO(
                    json.dumps({"tokens": {"account_id": "same-account", "access_token": "new-token"}}).encode("utf-8")
                )
                resp = self.client.post(
                    "/api/actions/upload_auths",
                    data={"files": (payload, "auth.json")},
                    content_type="multipart/form-data",
                )

                self.assertEqual(resp.status_code, 200)
                body = resp.get_json()
                self.assertEqual(body["created"], 0)
                self.assertEqual(body["updated"], 1)
                self.assertTrue(auth1.exists())
                saved = json.loads(auth1.read_text(encoding="utf-8"))
                self.assertEqual(saved["tokens"]["access_token"], "new-token")
                self.assertFalse((accounts_root / "acc02" / "auth.json").exists())
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

    def test_upload_rejects_known_invalid_account_id(self):
        original_auth_files = os.environ.get("CHATGPT_LOCAL_AUTH_FILES")
        original_data_dir = os.environ.get("CHATMOCK_DATA_DIR")
        original_settings_path = os.environ.get("CHATMOCK_DASHBOARD_SETTINGS_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                settings_path = Path(temp_dir) / "dashboard-settings.json"
                settings_path.write_text(json.dumps({}), encoding="utf-8")
                os.environ["CHATMOCK_DATA_DIR"] = temp_dir
                os.environ["CHATMOCK_DASHBOARD_SETTINGS_PATH"] = str(settings_path)
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)

                _mark_invalid_auth_candidate(label="default", account_id="bad-account")

                payload = io.BytesIO(
                    json.dumps({"tokens": {"account_id": "bad-account", "access_token": "bad-token"}}).encode("utf-8")
                )
                resp = self.client.post(
                    "/api/actions/upload_auths",
                    data={"files": (payload, "bad.json")},
                    content_type="multipart/form-data",
                )

                self.assertEqual(resp.status_code, 400)
                body = resp.get_json()
                self.assertIn("all files failed", body.get("error", ""))
                self.assertTrue(any("marked invalid" in detail for detail in body.get("details", [])))
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
