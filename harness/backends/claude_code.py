"""Claude Code headless backend — the peer-agent adapter for the Max plan.

Runs `claude -p` per completion: no API key, billed as plan usage. The user
prompt goes via stdin (avoids Windows command-line length limits and quoting);
the system prompt rides --append-system-prompt. Model aliases are Claude
Code's (sonnet | opus | haiku).
"""

from __future__ import annotations

import shutil
import subprocess

DEFAULT_MODEL = "sonnet"


class ClaudeCodeBackend:
    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 600.0):
        self.name = f"claude-code:{model}"
        self.model = model
        self.timeout = timeout
        self._exe = shutil.which("claude")
        if not self._exe:
            raise RuntimeError("claude CLI not found on PATH")

    def complete(self, system: str, user: str, schema: dict | None = None) -> str:
        # schema unused: no decode-level constraint channel in `claude -p`, and
        # the prompt already embeds the pydantic source it restates
        proc = subprocess.run(
            [self._exe, "-p", "--output-format", "text",
             "--model", self.model, "--append-system-prompt", system],
            input=user, capture_output=True, text=True, encoding="utf-8",
            timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr[:500]}")
        return proc.stdout
