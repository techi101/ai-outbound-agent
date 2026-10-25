import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.config import load_settings
from agent.llm import FallbackLLM, Reply
from agent.store import Store
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


class HostedAppTests(unittest.TestCase):
    def test_recorded_example_opens_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        text = "\n".join(element.value for element in app.markdown)
        self.assertIn("TrueFoundry", text)
        self.assertIn("Peakflo", text)
        self.assertIn("Recorded example", app.info[0].value)
        self.assertTrue(app.button[0].disabled)

    def test_visitors_do_not_share_runs(self):
        first = AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)
        second = AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)
        first_path = str(Path(first.session_state.workspace.name) / "prospects.db")
        second_path = str(Path(second.session_state.workspace.name) / "prospects.db")
        self.assertNotEqual(first_path, second_path)
        Store(first_path).start_run("private-first", "private profile", "groq", "model")
        second.run(timeout=20)
        self.assertEqual(Store(second_path).runs(), [])
        self.assertEqual(len(second.exception), 0)

    def test_live_run_requires_a_key_and_customer_profile(self):
        app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)
        app.text_input(key="key_groq").set_value("example-not-a-real-key").run()
        self.assertTrue(app.button[0].disabled)
        app.text_area[0].set_value("SaaS teams hiring sales engineers").run()
        self.assertFalse(app.button[0].disabled)
        app.text_input(key="key_groq").set_value("").run()
        self.assertTrue(app.button[0].disabled)

    def test_session_credential_does_not_change_environment(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "host-key"}, clear=True):
            settings = load_settings(provider="groq", api_key="visitor-key")
            self.assertEqual(settings.api_key, "visitor-key")
            self.assertEqual(os.environ["GROQ_API_KEY"], "host-key")
            self.assertNotIn("visitor-key", repr(settings))

    def test_fallback_clients_keep_each_visitors_key(self):
        used = []

        class Backend:
            def __init__(self, model, api_key=None):
                self.model = model
                self.key = api_key

            def complete(self, *args):
                used.append((self.model, self.key))
                if self.model == "busy":
                    raise RuntimeError("503 unavailable")
                return Reply(text="ok")

        with patch("agent.llm._BACKENDS", {"groq": Backend}):
            one = FallbackLLM("groq", ["busy", "ready"], api_key="first-key")
            two = FallbackLLM("groq", ["ready"], api_key="second-key")
            self.assertEqual(one.complete("system", []).text, "ok")
            self.assertEqual(two.complete("system", []).text, "ok")
        self.assertEqual(used, [("busy", "first-key"), ("ready", "first-key"), ("ready", "second-key")])

    def test_cli_environment_credentials_still_work(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "cli-key"}, clear=True):
            settings = load_settings()
        self.assertEqual(settings.provider, "groq")
        self.assertIsNone(settings.api_key)


if __name__ == "__main__":
    unittest.main()
