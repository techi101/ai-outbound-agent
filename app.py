"""Streamlit dashboard.

Two jobs: let someone start a run without touching the CLI, and show what a run
actually produced — including the trace, because a prospect list you cannot
audit is a prospect list you cannot trust.

    streamlit run app.py
"""

from __future__ import annotations

import json

import streamlit as st

from agent.config import MODELS, load_settings
from agent.loop import run as run_agent
from agent.pricing import describe_cost
from agent.store import Store

st.set_page_config(page_title="AI Outbound Agent", page_icon="◆", layout="wide")

DB = "prospects.db"


def fmt_signals(raw: str) -> str:
    try:
        return json.dumps(json.loads(raw), indent=2)
    except Exception:
        return raw or "(none)"


store = Store(DB)

# ---------------------------------------------------------------- sidebar

st.sidebar.title("AI Outbound Agent")

try:
    settings = load_settings(db_path=DB)
    st.sidebar.caption(f"provider **{settings.provider}** · model `{settings.model}`")
    ready = True
except RuntimeError as exc:
    st.sidebar.error(str(exc))
    settings, ready = None, False

st.sidebar.subheader("New run")
icp = st.sidebar.text_area(
    "Ideal customer profile",
    placeholder="seed-stage Indian SaaS companies hiring their first AI engineer",
    height=110,
)
col_a, col_b = st.sidebar.columns(2)
target = col_a.number_input("Prospects", 1, 25, 5)
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
                load_settings(db_path=DB, max_prospects=int(target),
                              min_fit_score=int(floor)),
                on_event=on_event,
            )
        except RuntimeError as exc:
            st.error(f"Run failed: {exc}")
            st.stop()

    st.success(f"Done. {len(result.prospects)} prospect(s) saved.")
    for frm, to, why in result.model_switches:
        st.info(f"{frm} was unavailable ({why}); switched to {to}.")
    selected = result.run_id

# ---------------------------------------------------------------- report

if not selected:
    st.title("No runs yet")
    st.write(
        "Describe an ideal customer in the sidebar and hit **Run agent**. "
        "The agent searches, reads company pages, scores fit, drafts outreach, "
        "and reviews its own drafts before saving them."
    )
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
