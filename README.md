# cad-agent

An agent that turns an English request into a verified, 3D-printable part —
and can't grade its own homework.

**Status:** parked 2026-07-31 after an end-to-end proof: a drone-frame plate went from an
English request through spec extraction, generated CadQuery, spec-derived verification and an
OrcaSlicer slice check to a physical print on a Bambu P2S. 80-test pytest suite (needs
OrcaSlicer installed). Full log: `STATUS.md`.

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

**Division of labour — local extraction, frontier codegen (measured, not
assumed):** extraction (English → spec JSON) is where local models earn a
real role — schema-constrained decoding (Ollama `format` + temperature 0)
guarantees the shape, and an 8B gets the values right. CadQuery codegen is
where the frontier gap lives. The production mode routes each stage to the
cheapest model that aces it:
`--backend claude-code --model sonnet --extract-backend ollama`.

**Benchmark** (same tasks, same sandbox, same verifier — swappable backends):

| backend | enclosure extract | enclosure codegen | frame extract | frame codegen |
|---|---|---|---|---|
| ollama:llama3.1:8b — freeform | 5/8 | 0/8 | — | — |
| ollama:llama3.1:8b — schema-constrained | **8/8** | — | **5/5** | — |
| ollama:qwen3:4b — schema-constrained | 6/8 | — | 5/5 | — |
| ollama:qwen3-coder:30b — schema-constrained | 8/8 | 0/8 | 5/5 | 0/5 |
| claude-code:sonnet | 8/8 | 8/8 (all iter=1) | 5/5 | 5/5 (all iter=1) |
| **hybrid: llama3.1:8b extract + sonnet codegen** | **8/8** | **8/8 (all iter=1)** | **5/5** | **5/5 (all iter=1)** |

Same model, same tasks: llama3.1:8b went **5/8 → 8/8** when the spec's JSON
schema moved from prose-only to the decoder — the decode config was worth
more than any model swap. The fairness column is the thesis in one row:
`qwen3-coder:30b`, the strongest local code model this GPU can run, extracts
**13/13** and closes **0/13** CadQuery parts at the 3-iteration cap — the
split isn't a compromise, it's what the measurements say. The cautionary column: `qwen3-4b-instruct-2507`,
the leaderboard pick for structured extraction, scored **1/8** — under the
schema grammar it emits the minimal valid object and silently drops every
stated standoff/cutout (freeform it extracts them fine). Bench the
(checkpoint × decoder) pair; constrained decoding fixes shape, not values,
and on some checkpoints it changes the values.

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

**Use it** (English in, print-ready files out — the hybrid mode end to end):
```
python -m harness.make "An enclosure 80 mm long, 60 wide, 30 tall, two M2.5
standoffs at (25,20) and (55,40)..." --name pi-box
```
Extracts locally (schema-constrained), echoes the spec for a human check,
generates with sonnet, verifies against the frozen spec, and stages
thermally-gated print files + multi-view renders + a self-contained
`review.html` under `prints/pi-box/`. Every gate refuses loudly with its own
exit code.

**Review before print** — the printer is gated on a human verdict recorded
after looking at the build:
```
open prints/pi-box/review.html                  # look at what it made (Windows: start)
python -m harness.review prints/pi-box --approve       # or --reject --comment "..."
python -m harness.send prints/pi-box --plate-clear     # refused without approval
```
The approval is hash-bound to the staged artifacts — any rebuild or re-slice
makes it stale and the send refuses again. A rejection requires a comment;
that comment is the revision request you take back to the agent.

**Dev:** Python 3.12 (`python3.12 -m venv .venv`; Windows: `py -3.12 -m venv .venv`), `pip install cadquery
pydantic pytest trimesh anthropic`, `python -m pytest tests/`. Golden runs:
`python -m harness.golden --backend claude-code --model sonnet
[--tasks golden/frame_tasks.json]`; hybrid rows add
`--extract-backend ollama`.

Status, evidence, and open work: `STATUS.md`.
