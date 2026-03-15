import sys
import unittest
from pathlib import Path

from flask import Flask


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.routes_openai import _resolve_web_search_mode
from chatmock.upstream import normalize_model_name


class UpstreamRoutingTests(unittest.TestCase):
    def test_normalize_model_name_preserves_fast_and_effort_suffixes(self):
        self.assertEqual(
            normalize_model_name("gpt-5.4-fast-low"),
            "gpt-5.4-fast-low",
        )

    def test_default_web_search_does_not_override_custom_tools(self):
        app = Flask(__name__)
        app.config["DEFAULT_WEB_SEARCH"] = True
        with app.app_context():
            mode = _resolve_web_search_mode(
                {},
                [{"type": "function", "name": "custom_search"}],
                [],
            )
        self.assertEqual(mode, "disabled")

    def test_default_web_search_enables_only_when_no_tools_present(self):
        app = Flask(__name__)
        app.config["DEFAULT_WEB_SEARCH"] = True
        with app.app_context():
            mode = _resolve_web_search_mode({}, [], [])
        self.assertEqual(mode, "live")


if __name__ == "__main__":
    unittest.main()
