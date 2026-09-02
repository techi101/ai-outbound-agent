# AI Outbound Agent

An agentic prospecting and outreach engine. You describe an ideal customer in a
sentence and it finds matching companies, researches each one, scores fit, and
drafts personalised outreach — then tells you what each qualified lead cost.

> **Status: in active development.** The design below is settled and the agent
> loop is being built now. This README describes the architecture; code lands
> incrementally.

## Why

Outbound prospecting is four jobs stacked on each other: find companies, work out
who to contact, research enough to say something specific, then write the email.
I did all four by hand for two years running corporate sponsorship outreach at
NSUT's incubator. It is slow, and the research step is the part that decides
whether the email gets a reply.

Most "AI outreach" tools generate mail-merge with a first name slotted in. The
interesting problem is not generation, it is *research quality* — and knowing
when a draft is too generic to send.

## How it works

Rather than a fixed pipeline, the model runs an agent loop and chooses which tool
to call next based on what it has learned so far. Six tools:

| Tool | Does |
|---|---|
| `search_companies` | Finds candidate companies for the described profile |
| `fetch_company_page` | Pulls a site or careers page |
| `extract_signals` | Funding stage, team size, stack, hiring signals |
| `score_fit` | 0–100 with written reasoning |
| `draft_outreach` | Personalised email grounded in the extracted signals |
| `save_prospect` | Persists to SQLite |

Two design decisions that matter more than the tool list:

**A critic pass.** A second model call scores every draft against the customer
profile and asks one question: would this email still make sense if you swapped
in a different company's name? If yes, it fails and gets regenerated. Generation
is easy; rejecting your own weak output is the part that makes it usable.

**Cost telemetry.** Token usage is logged per prospect, so the dashboard reports
**cost per qualified prospect** rather than a total spend number. That is the
figure that decides whether a channel is worth running.

## Deliberate constraints

- **Drafts only, no auto-send.** A human approves before anything leaves. Volume
  without a review gate is how domains get burned.
- **No LinkedIn scraping.** Sources are company sites, public job boards, and
  product directories. Scraped profile data gets accounts banned and is not worth
  the risk.
- **Provider-agnostic.** The model is a config value, so the same loop runs on
  Claude, Groq, or Gemini.

## Stack

Python · Claude tool-calling API · SQLite · Streamlit

## Roadmap

- [ ] Tool definitions and the agent loop
- [ ] Critic pass and regeneration
- [ ] SQLite persistence and per-prospect token logging
- [ ] Streamlit dashboard: prospects, scores, drafts, cost per qualified lead
- [ ] Worked example run committed to `examples/`
