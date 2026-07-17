"""The agent loop: extract -> codegen -> run sandboxed -> verify -> feed back.

The two stages are separately runnable and separately scored (plan review
finding #5): extraction noise must not contaminate the codegen pass-rate.
The codegen stage never writes its own checks — verify derives from the spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pydantic

from harness import prompts
from harness.backends import Backend
from harness.runner import run_codegen
from toolchain.spec import EnclosureSpec
from toolchain.verify import verify


@dataclass
class ExtractResult:
    ok: bool
    spec: EnclosureSpec | None = None
    attempts: int = 0
    error: str = ""


@dataclass
class CodegenResult:
    ok: bool
    iterations: int = 0
    failures_last: list[str] = field(default_factory=list)
    detail: str = ""


def extract_spec(backend: Backend, request: str, max_attempts: int = 2) -> ExtractResult:
    feedback = None
    for attempt in range(1, max_attempts + 1):
        text = backend.complete(prompts.EXTRACT_SYSTEM, prompts.extract_user(request, feedback))
        try:
            return ExtractResult(ok=True, spec=prompts.parse_spec_response(text), attempts=attempt)
        except (ValueError, pydantic.ValidationError) as e:  # JSONDecodeError is a ValueError
            feedback = str(e)[:2000]
    return ExtractResult(ok=False, attempts=max_attempts, error=feedback or "unknown")


def generate_part(backend: Backend, spec: EnclosureSpec, workroot: Path,
                  max_iterations: int = 3, timeout: float = 120.0) -> CodegenResult:
    feedback = None
    for i in range(1, max_iterations + 1):
        text = backend.complete(prompts.CODEGEN_SYSTEM, prompts.codegen_user(spec, feedback))
        code = prompts.strip_code_fences(text)
        workdir = Path(workroot) / f"iter{i}"
        run = run_codegen(code, spec, workdir, timeout=timeout)
        if not run.ok:
            feedback = f"{run.detail}\nstderr (tail):\n{run.stderr[-2000:]}"
            continue
        report = verify(spec, workdir)
        if report.ok:
            return CodegenResult(ok=True, iterations=i, detail=f"verified in {workdir}")
        failures = [f"{c.name}: {c.detail}" for c in report.failures()]
        feedback = "verification failures:\n" + "\n".join(failures)
    return CodegenResult(
        ok=False, iterations=max_iterations,
        failures_last=(feedback or "").splitlines(), detail="max iterations reached",
    )
