"""Sandboxed execution of agent-generated build scripts.

The floor from the plan review (blocker #3): generated code never runs
in-process. It runs in a subprocess with an isolated interpreter (-I), a
temp working directory, and a wall-clock timeout — OCCT booleans can segfault
or hang, and that must not take the loop down. This is crash containment and
a security floor, not a full sandbox (cooperative threat model, documented).

A static gate rejects code that references the toolchain package — the
codegen actor must not be able to call the reference builder or the verifier
(no side doors).
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pydantic

SCRIPT_NAME = "gen_build.py"
FORBIDDEN = re.compile(r"\btoolchain\b")


@dataclass
class RunResult:
    ok: bool
    detail: str
    stdout: str = ""
    stderr: str = ""


def run_codegen(code: str, spec: pydantic.BaseModel, workdir: Path,
                timeout: float = 120.0) -> RunResult:
    """Write spec.json + the generated script into workdir and execute it."""
    if FORBIDDEN.search(code):
        return RunResult(False, "rejected: generated code references the toolchain package")

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    (workdir / SCRIPT_NAME).write_text(code, encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, "-I", SCRIPT_NAME],
            cwd=str(workdir), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return RunResult(False, f"timed out after {timeout}s",
                         stdout=str(e.stdout or ""), stderr=str(e.stderr or ""))

    if proc.returncode != 0:
        return RunResult(False, f"script exited {proc.returncode}",
                         stdout=proc.stdout, stderr=proc.stderr)
    return RunResult(True, "script completed", stdout=proc.stdout, stderr=proc.stderr)
