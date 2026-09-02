"""SQLite persistence for runs, prospects, and the trace of tool calls.

The trace is not decoration. When an agent produces a bad prospect you need to
see which tool it called, with what, and what came back, or you are debugging
by guesswork.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    icp           TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    started_at    REAL NOT NULL,
    finished_at   REAL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0,
    iterations    INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS prospects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(run_id),
    company    TEXT NOT NULL,
    url        TEXT,
    signals    TEXT,
    score      INTEGER,
    reasoning  TEXT,
    subject    TEXT,
    body       TEXT,
    critic_verdict TEXT,
    critic_note    TEXT,
    revisions  INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS trace (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(run_id),
    step       INTEGER NOT NULL,
    tool       TEXT NOT NULL,
    arguments  TEXT,
    result     TEXT,
    ok         INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prospects_run ON prospects(run_id);
CREATE INDEX IF NOT EXISTS idx_trace_run ON trace(run_id);
"""

MAX_STORED_RESULT = 4000


class Store:
    def __init__(self, path: str = "prospects.db"):
        self.path = path
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- runs ---------------------------------------------------------------

    def start_run(self, run_id: str, icp: str, provider: str, model: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs "
                "(run_id, icp, provider, model, started_at) VALUES (?,?,?,?,?)",
                (run_id, icp, provider, model, time.time()),
            )
            conn.commit()

    def finish_run(
        self,
        run_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        iterations: int,
        status: str = "done",
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?, input_tokens=?, output_tokens=?, "
                "cost_usd=?, iterations=?, status=? WHERE run_id=?",
                (
                    time.time(),
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    iterations,
                    status,
                    run_id,
                ),
            )
            conn.commit()

    def runs(self, limit: int = 25) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT r.*, "
                "(SELECT COUNT(*) FROM prospects p WHERE p.run_id=r.run_id) "
                "AS prospect_count "
                "FROM runs r ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- prospects ----------------------------------------------------------

    def save_prospect(self, run_id: str, company: str, **fields) -> int:
        cols = {
            "url": "",
            "signals": "",
            "score": 0,
            "reasoning": "",
            "subject": "",
            "body": "",
            "critic_verdict": None,
            "critic_note": None,
            "revisions": 0,
        }
        cols.update({k: v for k, v in fields.items() if k in cols})
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO prospects (run_id, company, url, signals, score, "
                "reasoning, subject, body, critic_verdict, critic_note, "
                "revisions, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    company,
                    cols["url"],
                    cols["signals"],
                    cols["score"],
                    cols["reasoning"],
                    cols["subject"],
                    cols["body"],
                    cols["critic_verdict"],
                    cols["critic_note"],
                    cols["revisions"],
                    time.time(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def annotate_prospect(
        self, company: str, run_id: str, verdict: str, note: str, revisions: int
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE prospects SET critic_verdict=?, critic_note=?, revisions=? "
                "WHERE run_id=? AND company=?",
                (verdict, note, revisions, run_id, company),
            )
            conn.commit()

    def prospects(self, run_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM prospects"
        args: tuple = ()
        if run_id:
            sql += " WHERE run_id=?"
            args = (run_id,)
        sql += " ORDER BY score DESC, created_at DESC"
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # -- trace --------------------------------------------------------------

    def log_step(
        self, run_id: str, step: int, tool: str, arguments: dict, result: str, ok: bool
    ) -> None:
        trimmed = (result or "")[:MAX_STORED_RESULT]
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO trace (run_id, step, tool, arguments, result, ok, "
                "created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    step,
                    tool,
                    json.dumps(arguments, default=str)[:MAX_STORED_RESULT],
                    trimmed,
                    1 if ok else 0,
                    time.time(),
                ),
            )
            conn.commit()

    def trace(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM trace WHERE run_id=? ORDER BY step, id", (run_id,)
                ).fetchall()
            ]
