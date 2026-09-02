"""Configuration and provider selection.

The provider is a config value, not a hardcoded import, so the same agent loop
runs on Claude, Groq, or Gemini. Whichever key is present wins unless you set
LLM_PROVIDER explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Model per provider. Change these in one place.
MODELS = {
    "claude": "claude-opus-5",
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-3.5-flash",
}

# If the primary model is overloaded or retired, try these in order. A provider
# returning 503 should cost seconds, not stall a run.
FALLBACKS = {
    "claude": ["claude-sonnet-5", "claude-haiku-4-5"],
    "groq": ["llama-3.1-8b-instant"],
    "gemini": ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-flash-latest"],
}

# The env var each provider authenticates with.
KEY_VARS = {
    "claude": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}

# Preference order when the provider is not set explicitly.
PREFERENCE = ["claude", "groq", "gemini"]


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    max_iterations: int = 25
    max_prospects: int = 10
    min_fit_score: int = 60
    db_path: str = "prospects.db"


def _detect_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit:
        if explicit not in MODELS:
            raise ValueError(
                f"LLM_PROVIDER={explicit!r} is not one of {sorted(MODELS)}"
            )
        if not os.getenv(KEY_VARS[explicit]):
            raise RuntimeError(
                f"LLM_PROVIDER is {explicit} but {KEY_VARS[explicit]} is not set."
            )
        return explicit

    for name in PREFERENCE:
        if os.getenv(KEY_VARS[name]):
            return name

    raise RuntimeError(
        "No API key found. Set one of: "
        + ", ".join(KEY_VARS[p] for p in PREFERENCE)
        + " (copy .env.example to .env and fill one in)."
    )


def load_settings(**overrides) -> Settings:
    provider = overrides.pop("provider", None) or _detect_provider()
    model = overrides.pop("model", None) or os.getenv("LLM_MODEL") or MODELS[provider]
    return Settings(provider=provider, model=model, **overrides)
