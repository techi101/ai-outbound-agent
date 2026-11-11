"""Web app: a small FastAPI server in front of the agent, plus the static UI.

    uvicorn server:app --port 8000        # then open http://localhost:8000

It opens on the recorded example, so nobody needs an API key to see what the
agent produces. A live run uses the visitor's own key for that run only: the
key is handed to the model client and never written to disk, the database or
the environment. Each browser gets its own temporary SQLite database, so one
visitor never sees another's runs. Nothing here sends email.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.config import MODELS, load_settings
from agent.loop import run as run_agent

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
EXAMPLE = ROOT / "examples" / "run-2026-09-02.json"
COOKIE = "outbound_sid"
PROVIDERS = {"groq": "Groq", "gemini": "Gemini", "claude": "Claude"}
DEFAULT_MODELS = {**MODELS, "gemini": "gemini-flash-latest"}


@dataclass
class LiveRun:
    icp: str
    target: int
    min_score: int
    provider: str
    model: str
    status: str = "running"  # running | done | failed
    steps: list = field(default_factory=list)
    reviews: list = field(default_factory=list)
    error: str = ""
    result: dict | None = None


@dataclass
class Workspace:
    folder: tempfile.TemporaryDirectory
    runs: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def db(self) -> str:
        return str(Path(self.folder.name) / "prospects.db")


WORKSPACES: dict[str, Workspace] = {}
_workspaces_lock = threading.Lock()

app = FastAPI(title="AI Outbound Agent", docs_url=None, redoc_url=None)


def workspace(request: Request, response: Response) -> Workspace:
    sid = request.cookies.get(COOKIE)
    with _workspaces_lock:
        if not sid or sid not in WORKSPACES:
            sid = secrets.token_urlsafe(24)
            WORKSPACES[sid] = Workspace(tempfile.TemporaryDirectory(prefix="outbound-"))
            response.set_cookie(COOKIE, sid, httponly=True, samesite="lax")
        return WORKSPACES[sid]


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


def _run_view(run_id: str, live: LiveRun) -> dict:
    view = {
        "source": "live",
        "run_id": run_id,
        "status": live.status,
        "error": live.error,
        "icp": live.icp,
        "target": live.target,
        "min_score": live.min_score,
        "provider": live.provider,
        "model": live.model,
        "steps": list(live.steps),
        "reviews": list(live.reviews),
        "prospects": [],
        "model_switches": [],
        "skipped": [],
    }
    if live.result:
        view.update(live.result)
    return view


def _execute(ws: Workspace, run_id: str, live: LiveRun, api_key: str) -> None:
    def on_event(kind, payload):
        if kind == "tool":
            live.steps.append({
                "tool": payload["tool"],
                "detail": _step_detail(payload.get("args") or {}),
                "ok": bool(payload.get("ok")),
            })
        elif kind == "critic":
            live.reviews.append({k: payload.get(k) for k in ("company", "verdict", "reason", "revisions")})

    try:
        result = run_agent(
            live.icp,
            load_settings(provider=live.provider, model=live.model, api_key=api_key,
                          db_path=ws.db, max_prospects=live.target,
                          min_fit_score=live.min_score),
            on_event=on_event,
        )
        live.result = {
            "agent_run_id": result.run_id,
            "iterations": result.iterations,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cost_label": result.cost_label,
            "prospects": [shape_prospect(p) for p in result.prospects],
            "model_switches": [{"from": a, "to": b, "why": c} for a, b, c in result.model_switches],
        }
        live.status = "done"
    except Exception as exc:  # report, never crash the server
        # SDK errors can echo request details. Never return the key.
        live.error = str(exc).replace(api_key, "[redacted]")[:800] if api_key else str(exc)[:800]
        live.status = "failed"


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
def start_run(body: RunRequest, request: Request, response: Response) -> dict:
    ws = workspace(request, response)
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Choose Groq, Gemini or Claude.")
    key, icp = body.api_key.strip(), body.icp.strip()
    if not key:
        raise HTTPException(400, "Add your API key to start a live run.")
    if not icp:
        raise HTTPException(400, "Describe your ideal customer first.")
    with ws.lock:
        if any(r.status == "running" for r in ws.runs.values()):
            raise HTTPException(409, "A run is already in progress in this browser.")
        run_id = secrets.token_hex(6)
        live = LiveRun(icp=icp, target=body.target, min_score=body.min_score,
                       provider=body.provider,
                       model=body.model.strip() or DEFAULT_MODELS[body.provider])
        ws.runs[run_id] = live
    threading.Thread(target=_execute, args=(ws, run_id, live, key), daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/runs")
def list_runs(request: Request, response: Response) -> list[dict]:
    ws = workspace(request, response)
    return [
        {"run_id": rid, "icp": r.icp, "status": r.status, "prospects": len((r.result or {}).get("prospects", []))}
        for rid, r in reversed(list(ws.runs.items()))
    ]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, request: Request, response: Response) -> dict:
    ws = workspace(request, response)
    live = ws.runs.get(run_id)
    if live is None:
        raise HTTPException(404, "No such run in this browser session.")
    return _run_view(run_id, live)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
