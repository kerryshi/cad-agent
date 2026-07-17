"""Swappable model backends — the peer-agent contract.

A backend is anything that maps (system, user) -> text. The loop, prompts,
runner, and verify layer are backend-agnostic; comparing backends on the same
golden set is the point of the hybrid design.
"""

from __future__ import annotations

from typing import Protocol


class Backend(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


def get_backend(kind: str, model: str | None = None) -> Backend:
    if kind == "anthropic":
        from harness.backends.anthropic_backend import AnthropicBackend

        return AnthropicBackend(model=model) if model else AnthropicBackend()
    if kind == "ollama":
        from harness.backends.ollama_backend import OllamaBackend

        return OllamaBackend(model=model) if model else OllamaBackend()
    raise ValueError(f"unknown backend kind: {kind}")
