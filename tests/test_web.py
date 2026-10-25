"""The web UI's JSON API and static files."""
import os
import urllib.request
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Base  # noqa: E402
from pgadmin_bridge import config  # noqa: E402
from pgadmin_bridge.web import api  # noqa: E402


class WebTest(Base):
    def test_web_ui_state_save_and_test(self):
        state = self.http("/api/state")
        self.assertEqual(state["config"]["password"], api.MASK)
        self.assertIn({"instance": "WebTest", "server": "main", "unique": True}, state["servers"])
        cfg = dict(state["config"], timeout=5)
        saved = self.http("/api/config", cfg)
        self.assertEqual(saved["config"]["timeout"], 5)
        self.assertEqual(config.load()["password"], "pw")
        self.assertEqual(config.load()["instances"][0]["password"], "secret")
        r = self.http("/api/test", dict(cfg["instances"][0], password="wrong"))
        self.assertIn("login rejected", r["error"])
        self.assertEqual(self.http("/api/test", cfg["instances"][0])["servers"], ["main", "other"])
        self.assertIn("<title>pgadmin-bridge</title>", self.http("/"))

    def test_rename_keeps_masked_password(self):
        state = self.http("/api/state")
        inst = dict(state["config"]["instances"][0], name="renamed", was=state["config"]["instances"][0]["name"])
        try:
            self.assertNotIn("error", self.http("/api/config", dict(state["config"], instances=[inst])))
            self.assertEqual(config.load()["instances"][0]["password"], "secret")
        finally:
            self.http("/api/config", state["config"] | {"instances": [dict(inst, name=inst["was"], was="renamed")]})

    def test_static_assets_have_module_friendly_types(self):
        for path, ctype in [("/js/main.js", "javascript"), ("/css/base.css", "text/css"), ("/favicon.svg", "image/svg+xml")]:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.ui_port}{path}") as r:
                self.assertIn(ctype, r.headers["Content-Type"])

    def test_static_files_stay_inside_static_dir(self):
        self.assertEqual(self.http("/../api.py"), "not found")
        self.assertEqual(self.http("/%2e%2e/api.py"), "not found")


if __name__ == "__main__":
    unittest.main()
