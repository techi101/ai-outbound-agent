"""Command line entry point.

    python run.py "seed-stage Indian SaaS companies hiring their first AI engineer"
    python run.py --icp-file icp.txt --target 5 --provider gemini
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from agent.config import MODELS, load_settings
from agent.loop import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find, research, score and draft outreach to prospects."
    )
    parser.add_argument("icp", nargs="?", help="Ideal customer profile, in a sentence.")
    parser.add_argument("--icp-file", help="Read the profile from a file instead.")
    parser.add_argument("--target", type=int, default=10, help="Prospects to find.")
    parser.add_argument("--min-score", type=int, default=60, help="Fit score floor.")
    parser.add_argument("--provider", choices=sorted(MODELS), help="Override provider.")
    parser.add_argument("--model", help="Override model id.")
    parser.add_argument("--db", default="prospects.db", help="SQLite path.")
    parser.add_argument("--quiet", action="store_true", help="Only print the summary.")
    args = parser.parse_args(argv)

    if args.icp_file:
        icp = open(args.icp_file, encoding="utf-8").read().strip()
    elif args.icp:
        icp = args.icp.strip()
    else:
        parser.error("give an ICP as an argument or use --icp-file")

    settings = load_settings(
        provider=args.provider,
        model=args.model,
        max_prospects=args.target,
        min_fit_score=args.min_score,
        db_path=args.db,
    )

    print(f"provider: {settings.provider}   model: {settings.model}")
    print(f"target:   {settings.max_prospects} prospects, fit score >= {settings.min_fit_score}")
    print(f"icp:      {icp}\n")

    def on_event(kind, payload):
        if args.quiet:
            return
        if kind == "tool":
            mark = " " if payload["ok"] else "!"
            detail = payload["args"].get("query") or payload["args"].get(
                "url"
            ) or payload["args"].get("company") or ""
            print(f"  {mark} {payload['tool']:<20} {str(detail)[:60]}")
        elif kind == "critic":
            v = payload["verdict"].upper()
            extra = f" after {payload['revisions']} revision(s)" if payload["revisions"] else ""
            print(f"  critic {v:<5} {payload['company']}{extra}: {payload['reason']}")

    try:
        result = run(icp, settings, on_event=on_event)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print("\n" + "=" * 72)
    print(f"run {result.run_id}")
    print(f"iterations {result.iterations}   "
          f"tokens {result.usage.input_tokens} in / {result.usage.output_tokens} out")
    print(f"cost {result.cost_label}")
    for frm, to, why in result.model_switches:
        print(f"note: {frm} was unavailable ({why}); switched to {to}")
    if result.prospects:
        print(f"cost per qualified prospect ${result.cost_per_prospect:.4f}")
    print("=" * 72)

    if result.summary:
        print("\n" + textwrap.fill(result.summary, 72) + "\n")

    for p in result.prospects:
        flag = {"pass": "OK  ", "fail": "WEAK"}.get(p["critic_verdict"] or "", "--  ")
        print(f"{flag} [{p['score']:>3}] {p['company']}")
        if p["url"]:
            print(f"          {p['url']}")
        if p["reasoning"]:
            print(textwrap.fill(p["reasoning"], 68, initial_indent="          ",
                                subsequent_indent="          "))
        if p["subject"]:
            print(f"          subject: {p['subject']}")
        print()

    print(f"{len(result.prospects)} prospect(s) in {settings.db_path}")
    print("Drafts are drafts. Read them before anything is sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
