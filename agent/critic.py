"""The critic pass.

Generating an email is easy. Deciding your own draft is not good enough is the
part that makes the output usable, so it gets its own model call with one
question: would this email still make sense if you swapped in a different
company's name? If yes, it is not personalised, it is a template.

The critic never rewrites. It judges and explains, and the drafting prompt gets
another turn with the explanation attached. Keeping judge and author separate
stops the model from grading its own homework in the same breath.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .tools import ToolContext, _first_json, draft_outreach

CRITIC_SYSTEM = """You review cold outreach drafts and reject the generic ones.

Apply this test: if you replaced the company name with any other company in the
same industry, would the email still read as sensible? If yes, it FAILS. A pass
requires at least one claim that is true of this company and no other.

Also fail a draft that:
- opens with flattery or an industry truism
- describes the sender more than the recipient
- asks more than one thing
- runs over 120 words
- states a fact that is not in the signals

Return ONLY JSON:
{"verdict": "pass" | "fail", "reason": "one sentence", "fix": "what to change, or null"}"""


@dataclass
class Verdict:
    passed: bool
    reason: str
    fix: str | None

    @property
    def label(self) -> str:
        return "pass" if self.passed else "fail"


def review(ctx: ToolContext, company: str, signals: str, subject: str, body: str) -> Verdict:
    raw = ctx.sub_call(
        CRITIC_SYSTEM,
        f"Ideal customer profile:\n{ctx.icp}\n\n"
        f"Company: {company}\n"
        f"Signals available to the writer:\n{signals}\n\n"
        f"Draft subject: {subject}\nDraft body:\n{body}",
    )
    parsed = _first_json(raw)
    if not parsed:
        # A critic we cannot parse must not silently approve.
        return Verdict(False, "Critic returned unparseable output.", None)

    data = json.loads(parsed)
    verdict = str(data.get("verdict", "fail")).lower().strip()
    return Verdict(
        passed=verdict == "pass",
        reason=str(data.get("reason") or "").strip(),
        fix=(data.get("fix") or None),
    )


def draft_until_good(
    ctx: ToolContext, company: str, signals: str, max_attempts: int = 3
) -> tuple[dict, Verdict, int]:
    """Draft, review, redraft. Returns (draft, final verdict, revisions)."""
    note = ""
    last: dict = {}
    verdict = Verdict(False, "No attempt made.", None)

    for attempt in range(1, max_attempts + 1):
        payload = signals if not note else f"{signals}\n\nCritic feedback to fix:\n{note}"
        raw = draft_outreach(ctx, company, payload)
        parsed = _first_json(raw)
        last = json.loads(parsed) if parsed else {"subject": None, "body": None}

        if last.get("blocked"):
            return last, Verdict(False, str(last["blocked"]), None), attempt - 1
        if not (last.get("subject") and last.get("body")):
            note = "The draft was empty or malformed. Return valid JSON."
            continue

        verdict = review(ctx, company, signals, last["subject"], last["body"])
        if verdict.passed:
            return last, verdict, attempt - 1
        note = f"{verdict.reason} {verdict.fix or ''}".strip()

    return last, verdict, max_attempts - 1
