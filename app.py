"""Streamlit dashboard.

Two jobs: let someone start a run without touching the CLI, and show what a run
actually produced — including the trace, because a prospect list you cannot
audit is a prospect list you cannot trust.

    streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import streamlit as st

from agent.config import MODELS, load_settings
from agent.loop import run as run_agent
from agent.pricing import describe_cost
from agent.store import Store

st.set_page_config(page_title="AI Outbound Agent", page_icon="◆", layout="wide")

# Each browser session has its own temporary database. Never share live runs
# or a visitor's credentials between people using the public demo.
if "workspace" not in st.session_state:
    st.session_state.workspace = tempfile.TemporaryDirectory(prefix="outbound-")
DB = str(Path(st.session_state.workspace.name) / "prospects.db")
EXAMPLE = Path(__file__).parent / "examples" / "run-2026-09-02.md"


def fmt_signals(raw: str) -> str:
    try:
        return json.dumps(json.loads(raw), indent=2)
    except Exception:
        return raw or "(none)"


store = Store(DB)

# ---------------------------------------------------------------- sidebar

st.sidebar.title("AI Outbound Agent")
st.sidebar.caption("Research prospects. Draft something worth reading.")

provider = st.sidebar.selectbox(
    "Model provider", options=["groq", "gemini", "claude"],
    format_func=lambda value: {"groq": "Groq", "gemini": "Gemini", "claude": "Claude"}[value],
)
api_key = st.sidebar.text_input(
    "Your API key", type="password", key=f"key_{provider}",
    help="Used for your live runs in this browser session. Not saved to the database.",
)
default_model = "gemini-flash-latest" if provider == "gemini" else MODELS[provider]
model = st.sidebar.text_input("Model", value=default_model, key=f"model_{provider}")
ready = bool(api_key.strip() and model.strip())
st.sidebar.caption("No key needed to explore the recorded example below.")

st.sidebar.subheader("New run")
icp = st.sidebar.text_area(
    "Ideal customer profile",
    placeholder="seed-stage Indian SaaS companies hiring their first AI engineer",
    height=110,
)
col_a, col_b = st.sidebar.columns(2)
target = col_a.number_input("Prospects", 1, 10, 2)
floor = col_b.number_input("Min score", 0, 100, 60)

start = st.sidebar.button(
    "Run agent", type="primary", disabled=not (ready and icp.strip())
)

st.sidebar.divider()
runs = store.runs()
labels = {
    r["run_id"]: f"{r['run_id'][4:14]} · {r['prospect_count']} found · {r['icp'][:28]}"
    for r in runs
}
selected = st.sidebar.selectbox(
    "Past runs",
    options=list(labels),
    format_func=lambda k: labels[k],
    index=0 if labels else None,
    placeholder="no runs yet",
)

# ---------------------------------------------------------------- run

if start:
    log = st.empty()
    lines: list[str] = []

    def on_event(kind, payload):
        if kind == "tool":
            detail = (
                payload["args"].get("query")
                or payload["args"].get("url")
                or payload["args"].get("company")
                or ""
            )
            mark = "·" if payload["ok"] else "!"
            lines.append(f"{mark} {payload['tool']}  {str(detail)[:70]}")
        elif kind == "critic":
            lines.append(
                f"  critic {payload['verdict']}  {payload['company']}: "
                f"{payload['reason']}"
            )
        log.code("\n".join(lines[-18:]) or "starting...")

    with st.spinner("Researching. This takes a few minutes."):
        try:
            result = run_agent(
                icp.strip(),
                load_settings(provider=provider, model=model.strip(),
                              api_key=api_key.strip(), db_path=DB,
                              max_prospects=int(target), min_fit_score=int(floor)),
                on_event=on_event,
            )
        except Exception as exc:
            # SDK errors can contain request details. Never render a key.
            message = str(exc).replace(api_key.strip(), "[redacted]")
            st.error(f"Run failed: {message[:800]}")
            st.stop()

    st.success(f"Done. {len(result.prospects)} prospect(s) saved.")
    for frm, to, why in result.model_switches:
        st.info(f"{frm} was unavailable ({why}); switched to {to}.")
    selected = result.run_id

# ---------------------------------------------------------------- report

if not selected:
    st.title("AI Outbound Agent")
    st.write(
        "Describe who you want to reach. Get researched companies, fit scores "
        "and outreach drafts with a review of what makes each one specific."
    )
    st.info(
        "Recorded example · 2 September 2026. These are saved outputs, not a new "
        "search or current hiring information. Add your API key in the sidebar "
        "to research a fresh customer profile."
    )
    st.caption("Drafts only. The app does not send emails or messages.")
    example = EXAMPLE.read_text(encoding="utf-8")
    c1, c2, c3 = st.columns(3)
    c1.metric("Example prospects", 2)
    c2.metric("Research iterations", 20)
    c3.metric("Recorded tokens", "164,990")
    tab_p, tab_t = st.tabs(["Prospects and drafts", "Research trace"])
    with tab_p:
        st.markdown("## Prospect 1" + example.split("## Prospect 1", 1)[1].split("## An earlier run", 1)[0])
    with tab_t:
        st.markdown("## What it did" + example.split("## What it did", 1)[1].split("## Prospect 1", 1)[0])
    st.download_button("Download recorded example", example, "outbound-recorded-example.md")
    st.link_button("View source on GitHub", "https://github.com/techi101/ai-outbound-agent")
    st.stop()

meta = next((r for r in store.runs(200) if r["run_id"] == selected), None)
prospects = store.prospects(selected)
trace = store.trace(selected)

st.title("Prospect run")
st.caption(f"`{selected}` · {meta['icp'] if meta else ''}")

qualified = [p for p in prospects if (p["critic_verdict"] or "") == "pass"]
in_tok = meta["input_tokens"] if meta else 0
out_tok = meta["output_tokens"] if meta else 0
_, cost_label = describe_cost(
    meta["provider"] if meta else "", meta["model"] if meta else "", in_tok, out_tok
)
per = (meta["cost_usd"] / len(qualified)) if meta and qualified else 0.0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Prospects", len(prospects))
c2.metric("Passed critic", len(qualified))
c3.metric("Tool calls", len(trace))
c4.metric("Tokens", f"{in_tok + out_tok:,}")
c5.metric("Cost / qualified", f"${per:.4f}" if per else cost_label.split()[0])

st.divider()

tab_p, tab_t = st.tabs([f"Prospects ({len(prospects)})", f"Trace ({len(trace)})"])

with tab_p:
    if not prospects:
        st.info("This run saved no prospects.")
    for p in prospects:
        verdict = p["critic_verdict"] or "—"
        badge = {"pass": "✓", "fail": "✗"}.get(verdict, "·")
        header = f"{badge}  {p['company']}  ·  fit {p['score']}"
        if p["revisions"]:
            header += f"  ·  {p['revisions']} revision(s)"
        with st.expander(header, expanded=(p is prospects[0])):
            if p["url"]:
                st.write(f"[{p['url']}]({p['url']})")
            if p["reasoning"]:
                st.write(f"**Why this score.** {p['reasoning']}")
            if p["critic_note"]:
                tone = st.success if verdict == "pass" else st.warning
                tone(f"Critic ({verdict}): {p['critic_note']}")

            left, right = st.columns([3, 2])
            with left:
                st.markdown("**Draft outreach**")
                if p["subject"]:
                    st.text_input("Subject", p["subject"], key=f"s{p['id']}")
                    st.text_area("Body", p["body"], height=190, key=f"b{p['id']}")
                    st.caption("Draft only. Read it before anything is sent.")
                else:
                    st.write("No draft passed review for this company.")
            with right:
                st.markdown("**Signals**")
                st.code(fmt_signals(p["signals"]), language="json")

with tab_t:
    st.caption(
        "Every tool call the agent made, in order. This is how you tell a bad "
        "prospect from a bad tool."
    )
    for t in trace:
        ok = bool(t["ok"])
        icon = "·" if ok else "!"
        with st.expander(f"{icon} step {t['step']} — {t['tool']}", expanded=False):
            st.code(fmt_signals(t["arguments"]), language="json")
            st.text((t["result"] or "")[:2500])
