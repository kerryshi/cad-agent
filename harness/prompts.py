"""Prompt builders for the two scored stages: spec extraction and codegen.

Both embed the toolchain's spec.py source verbatim — it is the single source
of truth for field semantics, defaults, the coordinate convention, and the
derived geometry (post centers, lid size). The codegen stage never sees
verify.py; the model builds to the spec, not to the checks.
"""

from __future__ import annotations

import json
from pathlib import Path

from toolchain.spec import EnclosureSpec

_SPEC_SOURCE = (Path(__file__).parent.parent / "toolchain" / "spec.py").read_text(
    encoding="utf-8"
)

EXTRACT_SYSTEM = f"""You convert an English enclosure request into a JSON object \
matching the EnclosureSpec pydantic model below. The model's source is authoritative \
for field names, defaults, units (millimetres), the coordinate convention, and cutout \
face semantics.

```python
{_SPEC_SOURCE}
```

Rules:
- Output ONLY the JSON object — no prose, no code fences.
- Include a field only if the request states it or it differs from the default; \
defaults may be omitted.
- Cutouts need a "kind" field: "rect" (center, cz, width, height) or "circle" \
(center, cz, diameter). "center" is y for faces "+x"/"-x" and x for "+y"/"-y".
- Screw sizes are strings: "M2", "M2.5", "M3", "M4".
- Do not invent dimensions the request doesn't state."""


def extract_user(request: str, feedback: str | None = None) -> str:
    msg = f"Request:\n{request}"
    if feedback:
        msg += f"\n\nYour previous JSON was rejected:\n{feedback}\nOutput corrected JSON only."
    return msg


CODEGEN_SYSTEM = f"""You write a self-contained Python script that builds a parametric \
enclosure with the cadquery library.

The enclosure design is defined by the EnclosureSpec model below. Its docstring fixes \
the coordinate convention; its derived properties (cavity_x, post_top_z, lid_size, \
post_centers) define the geometry exactly — replicate them in your script.

```python
{_SPEC_SOURCE}
```

Design summary (all derivable from the source above):
- Body: outer box length x width x height at the origin; walls `wall` thick on all four \
sides, floor `floor` thick, open top. Four corner posts (cylinders, diameter \
`post_diameter`) fused inside the corners from the floor top up to z = height - \
lid_thickness, each with a pilot hole (SCREWS[screw].pilot diameter, `screw_depth` \
deep) drilled down from the post top. Standoffs are cylinders on the floor at their \
(x, y) centers, `height` tall, outer diameter od(), with a pilot hole through their \
full height. Cutouts pierce the stated wall completely.
- Lid: a separate flat plate, lid_size() footprint, from z = height - lid_thickness to \
z = height, positioned at x = wall + fit_clearance, y = wall + fit_clearance \
(assembled coordinates, NOT at the origin), with a clearance hole \
(SCREWS[screw].clearance diameter) through it at each post center.

Hard requirements for the script:
- Reads the spec as a plain dict: `spec = json.load(open("spec.json"))` (cwd). Missing \
keys take the defaults from the model above.
- Writes exactly two files to the cwd: "body.step" and "lid.step" \
(cq.exporters.export accepts a Shape).
- Imports: only cadquery and the Python standard library. Never import or reference \
the `toolchain` package — the sandbox rejects it.
- Deterministic, no network, no user input.
- Make boolean cut tools overshoot the faces they pierce by ~1 mm so cuts are never \
surface-coincident.

Output ONLY the Python script (a single code block or bare code)."""


def codegen_user(spec: EnclosureSpec, feedback: str | None = None) -> str:
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
    # take the first fenced block; drop a language tag on its first line
    block = parts[1]
    first_newline = block.find("\n")
    if first_newline != -1 and " " not in block[:first_newline].strip():
        block = block[first_newline + 1 :]
    return block.strip()


def parse_spec_response(text: str) -> EnclosureSpec:
    raw = strip_code_fences(text)
    # tolerate leading prose by finding the first brace
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    return EnclosureSpec.model_validate(json.loads(raw[start:]))
