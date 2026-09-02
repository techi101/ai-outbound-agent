"""The six tools the agent chooses between.

Each tool is a plain function taking (ctx, **kwargs) and returning a string the
model reads back. TOOL_SPECS holds the provider-neutral schemas.

Three of these delegate to a focused LLM call of their own (extraction, scoring,
drafting). That is deliberate: the orchestrating model decides *when* to score a
company, and a narrow prompt with one job does the scoring better than a long
conversation trying to hold six jobs at once.
"""

from __future__ import annotations

import json
import re
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .llm import LLM, Usage
from .store import Store

USER_AGENT = "ai-outbound-agent/0.1 (+https://github.com/techi101/ai-outbound-agent)"
FETCH_TIMEOUT = 12
MAX_PAGE_CHARS = 12000


@dataclass
class ToolContext:
    llm: LLM
    store: Store
    icp: str
    run_id: str
    usage: Usage = field(default_factory=Usage)
    pages_fetched: int = 0
    saved: int = 0

    def sub_call(self, system: str, prompt: str) -> str:
        """A focused, tool-less LLM call. Usage rolls into the run total."""
        reply = self.llm.complete(system, [{"role": "user", "content": prompt}])
        self.usage = self.usage + reply.usage
        return reply.text.strip()


# --------------------------------------------------------------------------
# 1. search_companies
# --------------------------------------------------------------------------

def search_companies(ctx: ToolContext, query: str, limit: int = 8) -> str:
    try:
        from ddgs import DDGS
    except ImportError:  # older package name
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            return (
                "ERROR: no search backend installed. Run `pip install ddgs` "
                "and try again."
            )

    limit = max(1, min(int(limit or 8), 15))
    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=limit))
    except Exception as exc:  # network, rate limit, layout change
        return f"ERROR: search failed ({exc.__class__.__name__}: {exc})"

    if not hits:
        return "No results. Try a broader query."

    lines = []
    for h in hits:
        title = (h.get("title") or "").strip()
        url = (h.get("href") or h.get("url") or "").strip()
        blurb = re.sub(r"\s+", " ", (h.get("body") or "")).strip()[:220]
        lines.append(f"- {title}\n  {url}\n  {blurb}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 2. fetch_company_page
# --------------------------------------------------------------------------

def _robots_allow(url: str) -> bool:
    try:
        parts = urlparse(url)
        rp = robotparser.RobotFileParser()
        rp.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        # No reachable robots.txt is not a prohibition.
        return True


def fetch_company_page(ctx: ToolContext, url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    host = urlparse(url).netloc.lower()
    if "linkedin.com" in host:
        return (
            "REFUSED: this tool does not fetch LinkedIn. Use the company's own "
            "site, careers page, or a public directory instead."
        )

    if not _robots_allow(url):
        return f"REFUSED: {host} disallows this path in robots.txt."

    try:
        resp = requests.get(
            url, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return f"ERROR: could not fetch {url} ({exc.__class__.__name__})"

    ctx.pages_fetched += 1

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))

    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + "\n\n[truncated]"
    return text or "(page had no readable text)"


# --------------------------------------------------------------------------
# 3. extract_signals
# --------------------------------------------------------------------------

_EXTRACT_SYSTEM = """You pull hard facts about a company out of raw page text.

Return ONLY a JSON object with these keys, using null where the page does not
say. Never guess, never infer from the company name, never fill a field from
general knowledge.

{
  "company": string,
  "one_liner": string or null,
  "funding_stage": string or null,
  "team_size": string or null,
  "tech_stack": [string],
  "hiring_signals": [string],
  "recent_news": [string]
}"""


def extract_signals(ctx: ToolContext, company: str, page_text: str) -> str:
    if not (page_text or "").strip():
        return "ERROR: page_text was empty. Fetch a page first."

    raw = ctx.sub_call(
        _EXTRACT_SYSTEM,
        f"Company: {company}\n\nPage text:\n{page_text[:MAX_PAGE_CHARS]}",
    )
    return _first_json(raw) or raw


# --------------------------------------------------------------------------
# 4. score_fit
# --------------------------------------------------------------------------

_SCORE_SYSTEM = """You score how well a company matches an ideal customer profile.

Be strict. A high score requires evidence in the signals, not a plausible
story. Missing information lowers the score; it never raises it.

Return ONLY JSON:
{"score": 0-100, "reasoning": "two sentences citing the specific signals",
 "missing": ["what you would need to be more confident"]}"""


def score_fit(ctx: ToolContext, company: str, signals: str) -> str:
    raw = ctx.sub_call(
        _SCORE_SYSTEM,
        f"Ideal customer profile:\n{ctx.icp}\n\n"
        f"Company: {company}\nSignals:\n{signals}",
    )
    return _first_json(raw) or raw


# --------------------------------------------------------------------------
# 5. draft_outreach
# --------------------------------------------------------------------------

_DRAFT_SYSTEM = """You write short cold outreach emails that get replies.

Rules:
- Under 120 words.
- Open with something specific and verifiable about THIS company, drawn from the
  signals. Never open with flattery or a generic industry observation.
- One clear ask. No multi-part questions.
- Plain sentences. No buzzwords, no em dashes, no exclamation marks.
- If the signals are too thin to say anything specific, say so instead of
  padding: return {"subject": null, "body": null, "blocked": "why"}.

Return ONLY JSON: {"subject": string, "body": string, "blocked": null}"""


def draft_outreach(ctx: ToolContext, company: str, signals: str) -> str:
    raw = ctx.sub_call(
        _DRAFT_SYSTEM,
        f"Ideal customer profile (who we are selling to):\n{ctx.icp}\n\n"
        f"Company: {company}\nSignals:\n{signals}",
    )
    return _first_json(raw) or raw


# --------------------------------------------------------------------------
# 6. save_prospect
# --------------------------------------------------------------------------

def save_prospect(
    ctx: ToolContext,
    company: str,
    url: str = "",
    signals: str = "",
    score: int = 0,
    reasoning: str = "",
    subject: str = "",
    body: str = "",
) -> str:
    ctx.store.save_prospect(
        run_id=ctx.run_id,
        company=company,
        url=url,
        signals=signals,
        score=int(score or 0),
        reasoning=reasoning,
        subject=subject,
        body=body,
    )
    ctx.saved += 1
    return f"Saved {company} (score {score}). {ctx.saved} prospect(s) stored so far."


# --------------------------------------------------------------------------

def _first_json(text: str) -> str | None:
    """Pull the first JSON object out of a reply, fences and prose included."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        if start == -1:
            return None
        depth, end = 0, None
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            return None
        candidate = text[start:end]
    try:
        return json.dumps(json.loads(candidate))
    except json.JSONDecodeError:
        return None


HANDLERS = {
    "search_companies": search_companies,
    "fetch_company_page": fetch_company_page,
    "extract_signals": extract_signals,
    "score_fit": score_fit,
    "draft_outreach": draft_outreach,
    "save_prospect": save_prospect,
}

TOOL_SPECS = [
    {
        "name": "search_companies",
        "description": (
            "Search the web for companies matching a description. Returns titles, "
            "URLs, and blurbs. Use specific queries; run it more than once with "
            "different angles rather than one broad query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "limit": {
                    "type": "integer",
                    "description": "Results to return, 1 to 15. Default 8.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_company_page",
        "description": (
            "Fetch a page and return its readable text. Use it on a company's own "
            "site, about page, or careers page. Refuses LinkedIn and respects "
            "robots.txt."
        ),
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Page URL."}},
            "required": ["url"],
        },
    },
    {
        "name": "extract_signals",
        "description": (
            "Pull structured facts (funding stage, team size, stack, hiring "
            "signals, recent news) out of page text you already fetched."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "page_text": {
                    "type": "string",
                    "description": "Text returned by fetch_company_page.",
                },
            },
            "required": ["company", "page_text"],
        },
    },
    {
        "name": "score_fit",
        "description": (
            "Score 0-100 how well a company matches the ideal customer profile, "
            "with reasoning. Call this before drafting anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "signals": {
                    "type": "string",
                    "description": "JSON from extract_signals.",
                },
            },
            "required": ["company", "signals"],
        },
    },
    {
        "name": "draft_outreach",
        "description": (
            "Write a short, specific outreach email grounded in the signals. Only "
            "for companies that scored well."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "signals": {
                    "type": "string",
                    "description": "JSON from extract_signals.",
                },
            },
            "required": ["company", "signals"],
        },
    },
    {
        "name": "save_prospect",
        "description": (
            "Store a finished prospect: the company, its signals, its score, and "
            "the approved draft. Call this once per company, last."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "url": {"type": "string"},
                "signals": {"type": "string"},
                "score": {"type": "integer"},
                "reasoning": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["company", "score"],
        },
    },
]
