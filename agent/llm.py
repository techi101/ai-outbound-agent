"""Provider abstraction.

One interface, three backends. Each backend takes the same tool schema, calls
its own SDK, and normalises the reply into a Reply object so the agent loop
never learns which provider it is talking to.

Tool schema passed in (provider-neutral):

    {"name": str, "description": str, "parameters": <json schema object>}
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
        )


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLM:
    """Base interface. Subclasses implement complete()."""

    def __init__(self, model: str):
        self.model = model

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> Reply:
        raise NotImplementedError

    # Message helpers. The loop builds messages in this neutral shape:
    #   {"role": "user"|"assistant", "content": str}
    #   {"role": "tool", "tool_call_id": str, "name": str, "content": str}
    # Each backend converts as needed.


class ClaudeLLM(LLM):
    def __init__(self, model: str):
        super().__init__(model)
        import anthropic

        self._client = anthropic.Anthropic()

    def _to_anthropic(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                }
                if m.get("is_error"):
                    block["is_error"] = True
                # Consecutive tool results belong in one user message.
                if out and out[-1]["role"] == "user" and isinstance(
                    out[-1]["content"], list
                ):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant" and m.get("blocks") is not None:
                out.append({"role": "assistant", "content": m["blocks"]})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    def complete(self, system, messages, tools=None) -> Reply:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": self._to_anthropic(messages),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
        }
        if tools:
            kwargs["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tools
            ]

        resp = self._client.messages.create(**kwargs)

        text_parts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )

        return Reply(
            text="\n".join(text_parts),
            tool_calls=calls,
            usage=Usage(resp.usage.input_tokens, resp.usage.output_tokens),
            raw=resp,
        )


class _OpenAIStyleLLM(LLM):
    """Shared logic for OpenAI-compatible chat APIs (Groq is one)."""

    def _client_call(self, **kwargs):
        raise NotImplementedError

    def _to_openai(self, system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m["tool_call_id"],
                        "content": m["content"],
                    }
                )
            elif m["role"] == "assistant" and m.get("openai_message"):
                out.append(m["openai_message"])
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    def complete(self, system, messages, tools=None) -> Reply:
        kwargs: dict = {
            "model": self.model,
            "messages": self._to_openai(system, messages),
            "max_tokens": 8000,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ]
            kwargs["tool_choice"] = "auto"

        resp = self._client_call(**kwargs)
        choice = resp.choices[0].message

        calls = []
        for tc in choice.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        u = resp.usage
        return Reply(
            text=choice.content or "",
            tool_calls=calls,
            usage=Usage(
                getattr(u, "prompt_tokens", 0) or 0,
                getattr(u, "completion_tokens", 0) or 0,
            ),
            raw=resp,
        )


class GroqLLM(_OpenAIStyleLLM):
    def __init__(self, model: str):
        super().__init__(model)
        from groq import Groq

        self._client = Groq()

    def _client_call(self, **kwargs):
        return self._client.chat.completions.create(**kwargs)


class GeminiLLM(LLM):
    def __init__(self, model: str):
        super().__init__(model)
        import os

        from google import genai

        self._genai = genai
        self._client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    def _to_gemini(self, messages: list[dict]) -> list[dict]:
        contents: list[dict] = []
        for m in messages:
            if m["role"] == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "function_response": {
                                    "name": m["name"],
                                    "response": {"result": m["content"]},
                                }
                            }
                        ],
                    }
                )
            elif m["role"] == "assistant" and m.get("gemini_parts"):
                contents.append({"role": "model", "parts": m["gemini_parts"]})
            else:
                role = "model" if m["role"] == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m["content"]}]})
        return contents

    def complete(self, system, messages, tools=None) -> Reply:
        cfg: dict = {"system_instruction": system}
        if tools:
            cfg["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["parameters"],
                        }
                        for t in tools
                    ]
                }
            ]

        resp = self._client.models.generate_content(
            model=self.model,
            contents=self._to_gemini(messages),
            config=cfg,
        )

        text_parts, calls, raw_parts = [], [], []
        candidate = (resp.candidates or [None])[0]
        for part in (candidate.content.parts if candidate else []) or []:
            raw_parts.append(part)
            if getattr(part, "text", None):
                text_parts.append(part.text)
            fc = getattr(part, "function_call", None)
            if fc:
                calls.append(
                    ToolCall(
                        id=fc.name,  # Gemini has no call id; the name is the handle
                        name=fc.name,
                        arguments=dict(fc.args or {}),
                    )
                )

        meta = getattr(resp, "usage_metadata", None)
        return Reply(
            text="\n".join(text_parts),
            tool_calls=calls,
            usage=Usage(
                getattr(meta, "prompt_token_count", 0) or 0,
                getattr(meta, "candidates_token_count", 0) or 0,
            ),
            raw=raw_parts,
        )


_BACKENDS = {"claude": ClaudeLLM, "groq": GroqLLM, "gemini": GeminiLLM}

# Substrings that mean "this model is not going to answer, try another one".
# Matched against the exception text because each SDK raises its own type.
_RETRYABLE = (
    "503",
    "unavailable",
    "high demand",
    "overloaded",
    "429",
    "rate limit",
    "resource_exhausted",
    "no longer available",
    "404",
    "not_found",
    "internal error",
    "500",
)


def _is_retryable(exc: Exception) -> bool:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return any(marker in text for marker in _RETRYABLE)


class FallbackLLM(LLM):
    """Tries a list of models in order, moving on when one is unavailable.

    A free-tier provider returns 503 "high demand" often enough that a single
    hardcoded model makes the agent look broken when it is not. The SDK's own
    retry loop makes this worse by retrying the same overloaded model in
    silence, so the chain is handled here where it can be reported.
    """

    # A free tier can have every model busy at the same moment, so exhausting
    # the chain once is not the same as a real failure. Sweep it again after a
    # wait before giving up.
    SWEEPS = 4
    BACKOFF_SECONDS = (0, 15, 45, 90)

    def __init__(self, provider: str, models: list[str]):
        if not models:
            raise ValueError("FallbackLLM needs at least one model")
        super().__init__(models[0])
        self.provider = provider
        self.models = models
        self.active = models[0]
        self.switches: list[tuple[str, str, str]] = []  # (from, to, why)
        self.waits = 0
        self._cache: dict[str, LLM] = {}

    def _backend(self, model: str) -> LLM:
        if model not in self._cache:
            self._cache[model] = _BACKENDS[self.provider](model)
        return self._cache[model]

    def complete(self, system, messages, tools=None) -> Reply:
        # Start from the model that worked last time, not the head of the list.
        order = [self.active] + [m for m in self.models if m != self.active]
        last: Exception | None = None

        for sweep in range(self.SWEEPS):
            if sweep:
                delay = self.BACKOFF_SECONDS[min(sweep, len(self.BACKOFF_SECONDS) - 1)]
                self.waits += 1
                time.sleep(delay)

            for model in order:
                try:
                    reply = self._backend(model).complete(system, messages, tools)
                except Exception as exc:
                    last = exc
                    if not _is_retryable(exc):
                        raise
                    continue

                if model != self.active:
                    reason = last.__class__.__name__ if last else "unavailable"
                    self.switches.append((self.active, model, reason))
                    self.active = model
                    self.model = model
                return reply

        raise RuntimeError(
            f"Every {self.provider} model failed across {self.SWEEPS} attempts. "
            f"Tried {', '.join(order)}. Last error: {last}"
        ) from last


def build_llm(provider: str, model: str, fallbacks: list[str] | None = None) -> LLM:
    if provider not in _BACKENDS:
        raise ValueError(f"Unknown provider {provider!r}")
    chain = [model] + [m for m in (fallbacks or []) if m != model]
    if len(chain) == 1:
        return _BACKENDS[provider](model)
    return FallbackLLM(provider, chain)
