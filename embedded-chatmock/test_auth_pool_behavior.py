import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.utils import _dedupe_candidates_by_account_id, _parse_auth_files_env


class AuthPoolBehaviorTests(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
