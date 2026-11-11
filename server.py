"""Web app: a small FastAPI server in front of the agent, plus the static UI.

    uvicorn server:app --port 8000        # then open http://localhost:8000

It opens on the recorded example, so nobody needs an API key to see what the
agent produces. A live run is one streaming request: the server runs the
agent against a fresh temporary SQLite database, streams each step back as a
line of JSON, sends the finished prospects, and deletes the database. Nothing
is kept between requests, so visitors never see each other's runs and the app
runs on serverless hosts. The visitor's key goes to that run's model client
only, never to disk, the database or the environment, and is redacted from
errors. Nothing here sends email.
"""

from __future__ import annotations

import json
import queue
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.config import MODELS, load_settings
from agent.loop import run as run_agent

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
EXAMPLE = ROOT / "examples" / "run-2026-09-02.json"
PROVIDERS = {"groq": "Groq", "gemini": "Gemini", "claude": "Claude"}
DEFAULT_MODELS = {**MODELS, "gemini": "gemini-flash-latest"}

app = FastAPI(title="AI Outbound Agent", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------- shaping


def _step_detail(args: dict) -> str:
    return str(args.get("query") or args.get("url") or args.get("company") or "")


def _chips(signals: dict) -> list[str]:
    chips = [s for s in (signals.get("hiring_signals") or []) if s][:2]
    if signals.get("funding_stage"):
        chips.append(str(signals["funding_stage"]))
    return [c if len(c) <= 60 else c[:57] + "..." for c in chips]


def _signals_text(signals: dict, raw: str) -> str:
    parts = [signals.get("one_liner")] + list(signals.get("hiring_signals") or [])
    text = " ".join(str(p).rstrip(".") + "." for p in parts if p)
    return text or raw


def shape_prospect(row: dict) -> dict:
    try:
        signals = json.loads(row.get("signals") or "{}")
        if not isinstance(signals, dict):
            signals = {}
    except ValueError:
        signals = {}
    return {
        "company": row["company"],
        "url": row.get("url") or "",
        "logo": "",
        "score": row.get("score") or 0,
        "reasoning": row.get("reasoning") or "",
        "signals_text": _signals_text(signals, row.get("signals") or ""),
        "signal_chips": _chips(signals),
        "subject": row.get("subject") or "",
        "body": row.get("body") or "",
        "critic_verdict": row.get("critic_verdict") or "",
        "critic_note": row.get("critic_note") or "",
        "revisions": row.get("revisions") or 0,
    }


# ---------------------------------------------------------------- routes


class RunRequest(BaseModel):
    provider: str
    api_key: str = Field(default="", repr=False)
    model: str = ""
    icp: str
    target: int = Field(default=2, ge=1, le=10)
    min_score: int = Field(default=60, ge=0, le=100)


@app.get("/api/example")
def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


@app.get("/api/config")
def config() -> dict:
    return {"providers": PROVIDERS, "default_models": DEFAULT_MODELS}


@app.post("/api/runs")
def start_run(body: RunRequest) -> StreamingResponse:
    """Runs the agent and streams newline-delimited JSON events:
    {"type": "step"}, {"type": "review"}, then {"type": "done"} or {"type": "error"}."""
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Choose Groq, Gemini or Claude.")
    key, icp = body.api_key.strip(), body.icp.strip()
    if not key:
        raise HTTPException(400, "Add your API key to start a live run.")
    if not icp:
        raise HTTPException(400, "Describe your ideal customer first.")
    model = body.model.strip() or DEFAULT_MODELS[body.provider]
    events: queue.Queue = queue.Queue()

    def on_event(kind, payload):
        if kind == "tool":
            events.put({"type": "step", "tool": payload["tool"],
                        "detail": _step_detail(payload.get("args") or {}),
                        "ok": bool(payload.get("ok"))})
        elif kind == "critic":
            events.put({"type": "review", **{k: payload.get(k) for k in ("company", "verdict", "reason", "revisions")}})

    def work():
        folder = tempfile.TemporaryDirectory(prefix="outbound-")
        try:
            result = run_agent(
                icp,
                load_settings(provider=body.provider, model=model, api_key=key,
                              db_path=str(Path(folder.name) / "prospects.db"),
                              max_prospects=body.target, min_fit_score=body.min_score),
                on_event=on_event,
            )
            events.put({
                "type": "done",
                "iterations": result.iterations,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "cost_label": result.cost_label,
                "prospects": [shape_prospect(p) for p in result.prospects],
                "model_switches": [{"from": a, "to": b, "why": c} for a, b, c in result.model_switches],
            })
        except Exception as exc:  # report, never crash the server
            # SDK errors can echo request details. Never return the key.
            events.put({"type": "error", "error": str(exc).replace(key, "[redacted]")[:800]})
        finally:
            try:
                folder.cleanup()
            except OSError:
                pass

    def stream():
        threading.Thread(target=work, daemon=True).start()
        while True:
            try:
                event = events.get(timeout=15)
            except queue.Empty:
                yield json.dumps({"type": "ping"}) + "\n"  # keeps proxies from closing an idle stream
                continue
            yield json.dumps(event) + "\n"
            if event["type"] in ("done", "error"):
                return

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
