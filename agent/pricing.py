"""Token pricing, so the dashboard can report money instead of token counts.

Rates are USD per million tokens and are list prices at the time of writing.
They drift, so treat the output as an estimate and check your provider's
console for a bill. A free tier costs zero, which is why FREE_TIER exists: it
is honest about a run that cost nothing rather than quoting a list price the
run never incurred.
"""

from __future__ import annotations

# model id -> (input $/1M, output $/1M)
RATES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # Groq (list price; the free tier bills nothing)
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    # Google (list price; AI Studio free tier bills nothing)
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.6-flash": (0.30, 2.50),
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-3.7-flash": (0.30, 2.50),
    "gemini-flash-latest": (0.30, 2.50),
}

# Providers whose keys are commonly free-tier. Set BILLED_PROVIDERS in the env
# to override if you are on a paid plan and want real numbers.
FREE_TIER = {"groq", "gemini"}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    rate = RATES.get(model)
    if rate is None:
        return 0.0
    return (input_tokens / 1_000_000) * rate[0] + (
        output_tokens / 1_000_000
    ) * rate[1]


def describe_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> tuple[float, str]:
    """Returns (usd, human string). Free tiers report 0 and say so."""
    listed = cost_usd(model, input_tokens, output_tokens)
    if provider in FREE_TIER:
        return 0.0, f"$0.00 (free tier; would list at ${listed:.4f})"
    return listed, f"${listed:.4f}"
