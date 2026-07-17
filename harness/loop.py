"""The agent loop: extract -> codegen -> run sandboxed -> verify -> feed back.

Family-agnostic: the part family (spec model, prompts, output contract,
verifier) is dispatched through toolchain.families. The two stages are
separately runnable and separately scored; the codegen stage never writes its
own checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pydantic

from harness import prompts
from harness.backends import Backend
from harness.runner import run_codegen
from toolchain.families import ENCLOSURE, Family


@dataclass
class ExtractResult:
    ok: bool
    spec: pydantic.BaseModel | None = None
    attempts: int = 0
    error: str = ""


@dataclass
class CodegenResult:
    ok: bool
    iterations: int = 0
    failures_last: list[str] = field(default_factory=list)
    detail: str = ""


def extract_spec(backend: Backend, request: str, family: Family = ENCLOSURE,
                 max_attempts: int = 2) -> ExtractResult:
    feedback = None
    system = prompts.extract_system(family)
    for attempt in range(1, max_attempts + 1):
        text = backend.complete(system, prompts.extract_user(request, feedback))
        try:
            return ExtractResult(
                ok=True, spec=prompts.parse_spec_response(text, family), attempts=attempt)
        except (ValueError, pydantic.ValidationError) as e:  # JSONDecodeError is a ValueError
            feedback = str(e)[:2000]
    return ExtractResult(ok=False, attempts=max_attempts, error=feedback or "unknown")


def generate_part(backend: Backend, spec: pydantic.BaseModel, workroot: Path,
                  family: Family = ENCLOSURE, max_iterations: int = 3,
                  timeout: float = 120.0) -> CodegenResult:
    feedback = None
    system = prompts.codegen_system(family)
    for i in range(1, max_iterations + 1):
        text = backend.complete(system, prompts.codegen_user(spec, feedback))
        code = prompts.strip_code_fences(text)
        workdir = Path(workroot) / f"iter{i}"
        run = run_codegen(code, spec, workdir, timeout=timeout)
        if not run.ok:
            feedback = f"{run.detail}\nstderr (tail):\n{run.stderr[-2000:]}"
            continue
        report = family.verify(spec, workdir)
        if report.ok:
            return CodegenResult(ok=True, iterations=i, detail=f"verified in {workdir}")
        failures = [f"{c.name}: {c.detail}" for c in report.failures()]
        feedback = "verification failures:\n" + "\n".join(failures)
    return CodegenResult(
        ok=False, iterations=max_iterations,
        failures_last=(feedback or "").splitlines(), detail="max iterations reached",
    )
