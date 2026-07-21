"""Anthropic API backend.

Default model is Haiku per the project decision (2026-07-16): golden-set runs
default to cheap models to protect budget; pass model="claude-opus-4-8" for
comparison runs. Credentials resolve via the SDK chain (ANTHROPIC_API_KEY,
ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile) — construct with no key.
"""

from __future__ import annotations

import anthropic

DEFAULT_MODEL = "claude-haiku-4-5"


class AnthropicBackend:
    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 8000):
        self.name = f"anthropic:{model}"
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    def complete(self, system: str, user: str, schema: dict | None = None) -> str:
        # schema unused — same rationale as claude_code.py
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in response.content if b.type == "text")
