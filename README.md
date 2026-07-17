# cad-agent

An agent that turns an English request into a verified, 3D-printable parametric
enclosure — and can't grade its own homework.

**Pipeline:** English → frozen spec (pydantic) → agent-written CadQuery code →
deterministic verification against the spec → headless slice check → print
(Bambu Lab P2S).

**The point:** LLMs write plausible CAD code with wrong geometry. The fix here
is structural, not prompt-side — the codegen agent never writes its own
checks. `toolchain/verify.py` derives every assertion (bounding box, wall
material probes, standoff rings, cutout openings, lid fit/interference) from
the frozen spec, and the gate itself is tested refuse-first: sabotaged parts
(thin walls, misplaced standoffs, undersized cutouts) must be refused by the
specific check that owns the defect.

**Layout:**
- `toolchain/` — deterministic, zero-LLM: `spec.py` (the contract, including
  the coordinate convention), `reference.py` (known-good parametric builder +
  sabotage hooks for tests), `verify.py` (the gate)
- `harness/` — agent loop with swappable model backends (Phase B)
- `golden/` — benchmark requests + per-backend pass-rate table (Phase B)
- `printleg/` — Bambu P2S control spike (parallel track)

**Dev:** Python 3.12 (`py -3.12 -m venv .venv`), `pip install cadquery
pydantic pytest trimesh`, `python -m pytest tests/`.

Status and evidence: see `STATUS.md`.
