"""Prompt builders for the two scored stages, parameterized by part family.

Both stages embed the family's spec source verbatim — the single source of
truth for field semantics, defaults, coordinate conventions, and derived
geometry. The codegen stage never sees a verifier; the model builds to the
spec, not to the checks.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pydantic

from toolchain.families import Family

_TOOLCHAIN = Path(__file__).parent.parent / "toolchain"


@lru_cache
def _sources(family: Family) -> str:
    return "\n\n".join(
        f"```python\n{(_TOOLCHAIN / f).read_text(encoding='utf-8')}\n```"
        for f in family.spec_sources
    )


def extract_system(family: Family) -> str:
    return f"""You convert an English {family.name} request into a JSON object \
matching the {family.spec_cls.__name__} pydantic model below. The model's source is \
authoritative for field names, defaults, units (millimetres), and conventions.

{_sources(family)}

Rules:
- Output ONLY the JSON object — no prose, no code fences.
- Include a field only if the request states it or it differs from the default; \
defaults may be omitted.
- {family.extract_notes}
- Do not invent dimensions the request doesn't state."""


def extract_user(request: str, feedback: str | None = None) -> str:
    msg = f"Request:\n{request}"
    if feedback:
        msg += f"\n\nYour previous JSON was rejected:\n{feedback}\nOutput corrected JSON only."
    return msg


def codegen_system(family: Family) -> str:
    files = " and ".join(f'"{f}"' for f in family.output_files)
    return f"""You write a self-contained Python script that builds a parametric \
{family.name} part with the cadquery library.

The part is defined by the model below. Its docstring fixes the coordinate \
convention; its derived properties define the geometry exactly — replicate them \
in your script.

{_sources(family)}

Design summary (all derivable from the source above):
{family.design_summary}

Hard requirements for the script:
- Reads the spec as a plain dict: `spec = json.load(open("spec.json"))` (cwd). Missing \
keys take the defaults from the model above.
- Writes exactly these file(s) to the cwd: {files} (cq.exporters.export accepts a Shape).
- Imports: only cadquery and the Python standard library. Never import or reference \
the `toolchain` package — the sandbox rejects it.
- Deterministic, no network, no user input.
- Make boolean cut tools overshoot the faces they pierce by ~1 mm so cuts are never \
surface-coincident.

Output ONLY the Python script (a single code block or bare code)."""


def codegen_user(spec: pydantic.BaseModel, feedback: str | None = None) -> str:
    msg = f"spec.json will contain:\n{spec.model_dump_json(indent=2)}\n\nWrite the script."
    if feedback:
        msg += (
            f"\n\nYour previous script FAILED verification:\n{feedback}\n"
            "Write a corrected, complete script (not a diff)."
        )
    return msg


def strip_code_fences(text: str) -> str:
    """Extract code/JSON from a possibly-fenced model response."""
    text = text.strip()
    if "```" not in text:
        return text
    parts = text.split("```")
    block = parts[1]
    first_newline = block.find("\n")
    if first_newline != -1 and " " not in block[:first_newline].strip():
        block = block[first_newline + 1 :]
    return block.strip()


def parse_spec_response(text: str, family: Family) -> pydantic.BaseModel:
    raw = strip_code_fences(text)
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    return family.spec_cls.model_validate(json.loads(raw[start:]))
