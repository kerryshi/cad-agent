# cad-agent — STATUS

**Where we are:** Phase A (toolchain core) complete and evidence-backed. The
deterministic layer exists: frozen enclosure spec, hand-written reference
builder, and a verify gate that has been watched refusing bad parts.

**What this is:** English request → parametric CAD (CadQuery) → deterministic
verification → (later) slice check → print on the Bambu P2S. v1 vertical:
project enclosures (open-top body + inset screw-down lid on corner posts).
Design invariant: the codegen agent never writes its own checks — verify
derives everything from the frozen spec (`an-actor-cannot-be-its-own-verifier`).

**Decisions (2026-07-16, Kerry):** hybrid harness from day one (deterministic
CLI toolchain + thin agent loop with swappable backends: Anthropic, Ollama);
enclosures as the only v1 part family; printer Phase-0 spike runs in a
parallel session (it gates only the auto-print leg, not this).

**Key contracts (from plan review, 2026-07-16):**
- Builder output = exactly `body.step` + `lid.step`, assembled coordinates,
  min corner at origin, +Z up (documented in `toolchain/spec.py` docstring).
- Wall thickness is NOT measured on the B-Rep (research problem) — verified by
  spec-anchored probe-point classification (`toolchain/verify.py` docstring).
- Agent-generated code must run in a subprocess (temp cwd + timeout), never
  in-process exec — OCCT can segfault/hang; also the security floor. NOT YET
  BUILT (harness is Phase B).
- Golden requests must be numerically explicit, or extraction noise swamps the
  codegen pass-rate table (Phase B).
- Slicecheck must slice parts separately, not as-assembled (Phase D).

**Environment:** `.venv` = Python 3.12 (`py -3.12`; the machine's default
`python` is 3.13 — do not use it). cadquery 2.8.0, pydantic 2.13.4,
trimesh 4.12.2, pytest. OrcaSlicer NOT installed (needed Phase D). Ollama up,
but no vision model pulled (needed Phase C).

**Evidence (Phase A):**
- `pytest tests/` — 7/7 green: good part passes all check families; each
  sabotage (thin wall, misplaced standoff, undersized cutout) refused by the
  specific owning check; missing outputs refused; impossible specs rejected.
- Refuse-first: CLI run on sabotaged part → exit 1, `wall.*` FAILs with probe
  coordinates; good part → exit 0.
- Mutation check: weakening verify's probe depths turned
  `test_thin_wall_refused` red (it demands `wall.*` specifically fire).

**In progress:** nothing (end of session 2026-07-16).

**Next actions:**
1. Phase B: `harness/` — backend interface, Anthropic backend, subprocess
   codegen runner (temp cwd + timeout), loop: spec → codegen → verify →
   feed failures back, capped iterations. Golden set (8–10 numerically
   explicit enclosure requests) + pass-rate table.
2. Phase C: Ollama backend (frame as contract-proof, not quality parity —
   4B/8B models will score low), render.py (validate Windows offscreen
   rendering EARLY, it's finicky), pull a VLM (`qwen2.5-vl` or `llava`).
3. Phase D: OrcaSlicer install + slicecheck.py (slice parts separately);
   one manual physical print, measure lid fit vs spec.
4. Parallel session, any time: printleg Phase-0 spike — needs Kerry to put
   the P2S in LAN-only + Developer Mode first (IP + access code).

**Open questions:**
- Lid retention feel: fit_clearance=0.2 default is a guess until the first
  physical print (FDM shrinkage is process, not CAD).
- Verify limitations documented in verify.py: point-sampling oracles assume
  cooperative codegen; cutout checks cross-talk with wall defects (observed,
  harmless — refusal still correct and named).

**Last updated:** 2026-07-16 (session: plan review + Phase A build).
