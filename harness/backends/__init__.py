"""Swappable model backends — the peer-agent contract.

A backend is anything that maps (system, user) -> text. The loop, prompts,
runner, and verify layer are backend-agnostic; comparing backends on the same
golden set is the point of the hybrid design.

`schema` is an optional JSON schema for the expected reply. Backends that
support constrained decoding (ollama) enforce it at the decoder; the rest
ignore it — it restates the pydantic source already embedded in the prompt,
so ignoring it changes nothing about what the model is asked for.
"""

from __future__ import annotations

from typing import Protocol


class Backend(Protocol):
    name: str

    def complete(self, system: str, user: str, schema: dict | None = None) -> str: ...


def get_backend(kind: str, model: str | None = None) -> Backend:
    if kind == "anthropic":
        from harness.backends.anthropic_backend import AnthropicBackend

        return AnthropicBackend(model=model) if model else AnthropicBackend()
    if kind == "ollama":
        from harness.backends.ollama_backend import OllamaBackend

        return OllamaBackend(model=model) if model else OllamaBackend()
    if kind == "claude-code":
        from harness.backends.claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend(model=model) if model else ClaudeCodeBackend()
    raise ValueError(f"unknown backend kind: {kind}")
