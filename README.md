# cad-agent

An agent that turns an English request into a verified, 3D-printable part —
and can't grade its own homework.

**Pipeline:** English → frozen spec (pydantic) → agent-written CadQuery code
(sandboxed subprocess) → deterministic verification against the spec →
headless slice check (OrcaSlicer, Bambu P2S profiles) → print.

**Part families:** parametric project **enclosures** (open-top body + inset
screw-down lid on corner posts) and flat **drone frame** plates (X-quad:
wheelbase, FC/motor mount patterns, prop-collision validation). Families are
registered in `toolchain/families.py`; the harness is family-agnostic.

**The point:** LLMs write plausible CAD code with wrong geometry. The fix here
is structural, not prompt-side — the codegen agent never writes its own
checks. Verifiers derive every assertion from the frozen spec (spec-anchored
probe points, bounding boxes, boolean interference, mesh watertightness), and
each gate ships with refuse-first evidence: sabotaged parts (thin walls,
missing posts, misplaced motor holes…) must be refused by the specific check
that owns the defect.

**Benchmark** (same tasks, same sandbox, same verifier — swappable backends):

| backend | enclosure extract | enclosure codegen | frame extract | frame codegen |
|---|---|---|---|---|
| ollama:llama3.1:8b | 5/8 | 0/8 | — | — |
| claude-code:sonnet | 8/8 | 8/8 (all iter=1) | 4/4 | 4/4 (all iter=1) |

The `claude-code` backend drives headless `claude -p` on a Claude
subscription — no API key. An `anthropic` API backend and an `ollama` backend
plug into the same protocol. Results JSON in `results/`.

**Layout:**
- `toolchain/` — deterministic, zero-LLM: specs + reference builders +
  verifiers per family, shared check machinery, offscreen renderer (VTK),
  slice check (OrcaSlicer CLI)
- `harness/` — backends, family-aware prompts (they embed the spec source
  verbatim), sandboxed runner, extract/codegen loop, golden benchmark CLI
- `golden/` — benchmark task files; `results/` — pass-rate tables
- `printleg/` — Bambu P2S control spike (parallel track, pending)

**Dev:** Python 3.12 (`py -3.12 -m venv .venv`), `pip install cadquery
pydantic pytest trimesh anthropic`, `python -m pytest tests/`. Golden runs:
`python -m harness.golden --backend claude-code --model sonnet
[--tasks golden/frame_tasks.json]`.

Status, evidence, and open work: `STATUS.md`.
