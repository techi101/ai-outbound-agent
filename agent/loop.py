"""The agent loop.

This is the part that makes it an agent rather than a pipeline. There is no
fixed order of operations: the model reads what it has learned so far and picks
the next tool itself. A promising company gets three fetches; a dead end gets
abandoned after one.

The loop's own job is small and boring on purpose: dispatch the calls the model
asks for, feed results back, keep a trace, and stop.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

from .config import FALLBACKS, Settings, load_settings
from .critic import draft_until_good
from .llm import Usage, build_llm
from .pricing import describe_cost
from .store import Store
from .tools import HANDLERS, TOOL_SPECS, ToolContext

SYSTEM = """You are a prospecting agent. You are given an ideal customer profile
and you return researched, scored prospects with outreach drafts.

Work company by company rather than doing one stage for everybody at once. For
each candidate:

1. search_companies to find candidates. Search more than once, from different
   angles, before settling.
2. fetch_company_page on the company's own site. The about or careers page
   usually carries more signal than the landing page.
3. extract_signals on the text you fetched.
4. score_fit. If the score is below {min_score}, drop the company and move on.
   Do not draft for a company you would not contact.
5. For companies that clear the bar, call save_prospect with the signals, the
   score, and the reasoning. Leave subject and body empty; drafting and review
   happen after you finish.

Rules:
- Never invent a fact. If the page does not say it, it is not a signal.
- Do not fetch LinkedIn. The tool refuses it anyway.
- A company you cannot fetch is a company you cannot score. Move on.
- Stop when you have {target} saved prospects or you run out of candidates, then
  reply with a short plain-text summary of what you found and what you skipped.

You have {target} prospects to find. Be efficient with tool calls."""


@dataclass
class RunResult:
    run_id: str
    prospects: list[dict]
    usage: Usage
    cost_usd: float
    cost_label: str
    iterations: int
    summary: str
    model_switches: list = field(default_factory=list)

    @property
    def cost_per_prospect(self) -> float:
        qualified = len(self.prospects) or 1
        return self.cost_usd / qualified


def run(
    icp: str,
    settings: Settings | None = None,
    on_event=None,
) -> RunResult:
    """Run the agent against an ICP description.

    on_event(kind, payload) is called for progress: "tool", "text", "critic".
    """
    settings = settings or load_settings()
    llm = build_llm(
        settings.provider, settings.model, FALLBACKS.get(settings.provider)
    )
    store = Store(settings.db_path)
    run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    store.start_run(run_id, icp, settings.provider, settings.model)
    ctx = ToolContext(llm=llm, store=store, icp=icp, run_id=run_id)

    def emit(kind: str, payload):
        if on_event:
            on_event(kind, payload)

    system = SYSTEM.format(target=settings.max_prospects, min_score=settings.min_fit_score)
    messages: list[dict] = [
        {"role": "user", "content": f"Ideal customer profile:\n{icp}"}
    ]

    total = Usage()
    step = 0
    summary = ""

    for iteration in range(1, settings.max_iterations + 1):
        step = iteration
        reply = llm.complete(system, messages, TOOL_SPECS)
        total = total + reply.usage

        if reply.text:
            emit("text", reply.text)

        if not reply.wants_tools:
            summary = reply.text
            break

        # Record the assistant turn in whatever shape the provider needs back.
        messages.append(_assistant_turn(settings.provider, reply))

        for call in reply.tool_calls:
            handler = HANDLERS.get(call.name)
            if handler is None:
                result, ok = f"ERROR: no such tool {call.name!r}", False
            else:
                try:
                    result, ok = handler(ctx, **call.arguments), True
                except TypeError as exc:
                    result, ok = f"ERROR: bad arguments ({exc})", False
                except Exception as exc:  # a tool failing must not kill the run
                    result, ok = f"ERROR: {exc.__class__.__name__}: {exc}", False

            emit("tool", {"tool": call.name, "args": call.arguments, "ok": ok})
            store.log_step(run_id, step, call.name, call.arguments, result, ok)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": result if ok else result,
                    "is_error": not ok,
                }
            )

        if ctx.saved >= settings.max_prospects:
            summary = f"Reached the target of {settings.max_prospects} prospects."
            break
    else:
        summary = f"Stopped at the {settings.max_iterations}-iteration ceiling."

    # Drafting and review happen after research, so the critic sees a settled
    # set of signals rather than a half-finished one.
    for row in store.prospects(run_id):
        if row["score"] < settings.min_fit_score:
            continue
        draft, verdict, revisions = draft_until_good(ctx, row["company"], row["signals"] or "")
        store.annotate_prospect(
            company=row["company"],
            run_id=run_id,
            verdict=verdict.label,
            note=verdict.reason,
            revisions=revisions,
        )
        if draft.get("subject") and draft.get("body"):
            _update_draft(store, run_id, row["company"], draft)
        emit(
            "critic",
            {
                "company": row["company"],
                "verdict": verdict.label,
                "reason": verdict.reason,
                "revisions": revisions,
            },
        )

    total = total + ctx.usage
    served_by = getattr(llm, "active", settings.model)
    cost, label = describe_cost(
        settings.provider, served_by, total.input_tokens, total.output_tokens
    )
    store.finish_run(
        run_id, total.input_tokens, total.output_tokens, cost, step, "done"
    )

    return RunResult(
        run_id=run_id,
        prospects=store.prospects(run_id),
        usage=total,
        cost_usd=cost,
        cost_label=label,
        iterations=step,
        summary=summary,
        model_switches=getattr(llm, "switches", []),
    )


def _assistant_turn(provider: str, reply) -> dict:
    """Providers want their own assistant turn echoed back verbatim."""
    if provider == "claude":
        return {"role": "assistant", "content": "", "blocks": reply.raw.content}
    if provider == "groq":
        return {
            "role": "assistant",
            "content": reply.text,
            "openai_message": reply.raw.choices[0].message,
        }
    if provider == "gemini":
        return {"role": "assistant", "content": reply.text, "gemini_parts": reply.raw}
    return {"role": "assistant", "content": reply.text}


def _update_draft(store: Store, run_id: str, company: str, draft: dict) -> None:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(store.path)) as conn:
        conn.execute(
            "UPDATE prospects SET subject=?, body=? WHERE run_id=? AND company=?",
            (draft.get("subject", ""), draft.get("body", ""), run_id, company),
        )
        conn.commit()


__all__ = ["run", "RunResult"]
