"""Scripted backend for tests — replays canned responses in order.

Lets the loop, runner, and prompts be exercised deterministically with no
model, no network, and no spend.
"""

from __future__ import annotations


class ScriptedBackend:
    def __init__(self, responses: list[str], name: str = "scripted"):
        self.name = name
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []  # (system, user) pairs, for assertions
        self.schemas: list[dict | None] = []  # schema per call, for assertions

    def complete(self, system: str, user: str, schema: dict | None = None) -> str:
        self.calls.append((system, user))
        self.schemas.append(schema)
        if not self._responses:
            raise RuntimeError("scripted backend exhausted")
        # repeat the last response if the loop asks more times than scripted
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)
