import json
import os
from pathlib import Path
import re
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from agent.config import load_settings
from agent.llm import FallbackLLM, Reply, Usage
from agent.loop import RunResult


ROOT = Path(__file__).resolve().parents[1]
RECORDED_MD = (ROOT / "examples" / "run-2026-09-02.md").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Markdown quote markers and bold removed, whitespace collapsed."""
    text = re.sub(r"^> ?", "", text, flags=re.M).replace("**", "")
    return re.sub(r"\s+", " ", text)


def _wait(client, run_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        view = client.get(f"/api/runs/{run_id}").json()
        if view["status"] != "running":
            return view
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def fake_agent(icp, settings, on_event=None):
    on_event("tool", {"tool": "search_companies", "args": {"query": "q"}, "ok": True})
    on_event("critic", {"company": "Acme", "verdict": "pass", "reason": "specific", "revisions": 1})
    return RunResult(
        run_id="run_x", usage=Usage(10, 2), cost_usd=0.0,
        cost_label="$0.00 (free tier; would list at $0.0001)",
        iterations=3, summary="", model_switches=[("a", "b", "ServerError")],
        prospects=[{
            "company": "Acme", "url": "https://acme.test/", "score": 77, "reasoning": "r",
            "signals": json.dumps({"one_liner": "Makes anvils", "hiring_signals": ["Hiring ML engineer"],
                                   "funding_stage": "Seed"}),
            "subject": "s", "body": "b", "critic_verdict": "pass", "critic_note": "n", "revisions": 1,
        }],
    )


class RecordedExampleTests(unittest.TestCase):
    def test_example_opens_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            client = TestClient(server.app)
            page = client.get("/")
            data = client.get("/api/example").json()
            assets = {a: client.get("/" + a).status_code
                      for a in ("app.js", "app.css", "logos/truefoundry.png", "logos/peakflo.svg")}
        self.assertEqual(page.status_code, 200)
        self.assertIn("Outbound Agent", page.text)
        self.assertIn("Nothing is sent", page.text)
        self.assertEqual(data["recorded_label"], "Recorded example, 2 Sep 2026")
        self.assertEqual([p["company"] for p in data["prospects"]], ["TrueFoundry", "Peakflo"])
        self.assertEqual([p["score"] for p in data["prospects"]], [95, 80])
        self.assertEqual(set(assets.values()), {200}, assets)

    def test_every_example_value_comes_from_the_recorded_run(self):
        """The UI must not show numbers or text the run did not produce."""
        data = json.loads((ROOT / "examples" / "run-2026-09-02.json").read_text(encoding="utf-8"))
        source = _flat(RECORDED_MD)
        self.assertIn(f"iterations {data['iterations']}", source)
        self.assertIn(f"--target {data['target']} --min-score {data['min_score']}", source)
        self.assertIn(f"tokens {data['input_tokens']:,} in / {data['output_tokens']:,} out", source)
        self.assertIn(f"cost {data['cost_label']}", source)
        self.assertIn(f"cost per qualified prospect {data['cost_per_qualified_label']}", source)
        self.assertIn(data["icp"], source)
        self.assertIn(data["model"], source)
        for step in data["steps"]:
            self.assertIn(f"{step['tool']} {step['detail']}", source)
            if "note" in step:
                self.assertIn(step["note"], source)
        for sw in data["model_switches"]:
            self.assertIn(f"{sw['from']} was unavailable ({sw['why']}); switched to {sw['to']}", source)
        for skipped in data["skipped"]:
            self.assertIn(skipped["company"], source)
            self.assertIn(skipped["why"], source)
        for p in data["prospects"]:
            self.assertIn(f"{p['company']}, fit {p['score']}", source)
            self.assertIn(p["url"], source)
            for field in ("signals_text", "subject", "critic_note"):
                self.assertIn(_flat(p[field]), source, field)
            for paragraph in p["body"].split("\n\n"):
                self.assertIn(_flat(paragraph), source)
            for chip in p["signal_chips"]:
                self.assertIn(chip, p["signals_text"])
            self.assertEqual(p["critic_verdict"], "pass")
            self.assertEqual(p["revisions"], 0)  # "passed review on the first attempt"


class LiveRunTests(unittest.TestCase):
    def test_live_run_requires_a_key_and_customer_profile(self):
        client = TestClient(server.app)
        no_key = client.post("/api/runs", json={"provider": "groq", "api_key": " ", "icp": "SaaS teams"})
        no_icp = client.post("/api/runs", json={"provider": "groq", "api_key": "k", "icp": "  "})
        bad = client.post("/api/runs", json={"provider": "openai", "api_key": "k", "icp": "SaaS"})
        self.assertEqual((no_key.status_code, no_icp.status_code, bad.status_code), (400, 400, 400))
        self.assertEqual(client.get("/api/runs").json(), [])

    def test_live_run_streams_steps_and_never_returns_the_key(self):
        with patch.object(server, "run_agent", fake_agent):
            client = TestClient(server.app)
            started = client.post("/api/runs", json={"provider": "groq", "api_key": "secret-visitor-key",
                                                     "icp": "anvil makers", "target": 1, "min_score": 50})
            self.assertEqual(started.status_code, 200)
            view = _wait(client, started.json()["run_id"])
        self.assertEqual(view["status"], "done")
        self.assertEqual(view["steps"], [{"tool": "search_companies", "detail": "q", "ok": True}])
        self.assertEqual(view["model_switches"], [{"from": "a", "to": "b", "why": "ServerError"}])
        p = view["prospects"][0]
        self.assertEqual((p["company"], p["score"], p["revisions"]), ("Acme", 77, 1))
        self.assertEqual(p["signal_chips"], ["Hiring ML engineer", "Seed"])
        self.assertNotIn("secret-visitor-key", json.dumps(view))

    def test_failed_run_redacts_the_key(self):
        def boom(icp, settings, on_event=None):
            raise RuntimeError(f"401 bad key {settings.api_key}")

        with patch.object(server, "run_agent", boom):
            client = TestClient(server.app)
            rid = client.post("/api/runs", json={"provider": "groq", "api_key": "leaky-key", "icp": "x"}).json()["run_id"]
            view = _wait(client, rid)
        self.assertEqual(view["status"], "failed")
        self.assertIn("[redacted]", view["error"])
        self.assertNotIn("leaky-key", json.dumps(view))

    def test_visitors_do_not_share_runs(self):
        with patch.object(server, "run_agent", fake_agent):
            first, second = TestClient(server.app), TestClient(server.app)
            rid = first.post("/api/runs", json={"provider": "groq", "api_key": "k", "icp": "private profile"}).json()["run_id"]
            _wait(first, rid)
            self.assertEqual(len(first.get("/api/runs").json()), 1)
            self.assertEqual(second.get("/api/runs").json(), [])
            self.assertEqual(second.get(f"/api/runs/{rid}").status_code, 404)


class CredentialTests(unittest.TestCase):
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
