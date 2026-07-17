# cad-agent — STATUS

**Where we are:** Phase A (toolchain) and Phase B (harness) built and committed
with evidence. First real-model golden run (Ollama llama3.1:8b) in progress.

**What this is:** English request → parametric CAD (CadQuery) → deterministic
verification → (later) slice check → print on the Bambu P2S. v1 vertical:
project enclosures (open-top body + inset screw-down lid on corner posts).
Design invariant: the codegen agent never writes its own checks — verify
derives everything from the frozen spec (`an-actor-cannot-be-its-own-verifier`).

**Decisions (2026-07-16, Kerry):** hybrid harness from day one (deterministic
toolchain + swappable backends: Anthropic, Ollama); enclosures only; printer
Phase-0 spike in a parallel session (gates only the auto-print leg).

**Architecture (as built):**
- `toolchain/` — LLM-free: `spec.py` (frozen contract + coordinate convention),
  `reference.py` (known-good builder + 4 sabotage hooks), `verify.py`
  (spec-derived probes: walls/floor, corner posts, standoff rings, cutout
  open/oversize, lid bbox + screw holes + boolean interference, trimesh
  watertight). Builder output contract: `body.step` + `lid.step`, assembled
  coords, min corner at origin.
- `harness/` — `backends/` (protocol + anthropic | ollama | scripted),
  `prompts.py` (embeds spec.py source verbatim; codegen never sees verify.py),
  `runner.py` (subprocess `-I` + temp cwd + timeout; static gate rejects
  `toolchain` references), `loop.py` (extract and codegen separately scored;
  verify failures fed back, capped iterations), `golden.py` (benchmark CLI →
  `results/<backend>.json` + markdown table).
- `golden/tasks.json` — 8 numerically explicit enclosure tasks + expected specs.

**Environment:** `.venv` = Python 3.12 (`py -3.12`; machine default `python` is
3.13 — do not use). cadquery 2.8.0, pydantic 2.13.4, trimesh 4.12.2,
anthropic 0.117.0, pytest. OrcaSlicer NOT installed (Phase D). Ollama up
(llama3.1:8b, qwen3:4b); no vision model pulled (Phase C).

**Evidence so far:**
- 19/19 pytest green (`tests/`): verify refuses all 4 sabotages (thin wall,
  misplaced standoff, undersized cutout, missing post) via the specific owning
  check; runner gates refuse cheating/crashing/hanging scripts; loop iterates
  on failure and succeeds via the real sandbox + verify; golden file validates.
- Refuse-first + mutation evidence from Phase A (see git log 9f3ae0a).
- Golden run, ollama:llama3.1:8b (2026-07-17, full 8 tasks, 3-iter cap):
  **extract 5/8, codegen 0/8** — every codegen exhausted the cap with invalid
  Python (undefined classes, broken chaining); the sandbox contained every
  crash and the loop fed failures back as designed. Confirms the framing:
  local 8B proves the peer-agent contract, not quality. Full per-task data in
  `results/ollama-llama3_1-8b.json`. A code-tuned local model
  (`qwen2.5-coder:14b`) is worth one later run.

**BLOCKED on Kerry:**
1. **Anthropic golden column** — no API credentials on this machine (no
   `ANTHROPIC_API_KEY`, no `ant auth login` profile; Claude Code's own login
   is not visible to the SDK). Set a key or run `ant auth login`, then:
   `python -m harness.golden --backend anthropic` (defaults to Haiku per the
   budget decision; `--model claude-opus-4-8` for the comparison run).
2. **Printer spike** — put the P2S in LAN-only + Developer Mode, note IP +
   access code. Then run the Phase-0 spike in a parallel session.

**Next actions:**
1. Read Ollama golden results (`results/ollama-llama3_1-8b.json`), record the
   pass-rate table here, commit results.
2. Anthropic golden run once credentials exist (see BLOCKED).
3. Phase C: render.py (validate Windows offscreen rendering EARLY — finicky),
   VLM critique (pull `qwen2.5-vl` or `llava`), frame Ollama codegen as
   contract-proof, not quality parity.
4. Phase D: install OrcaSlicer; slicecheck.py (slice body and lid SEPARATELY,
   not as-assembled); one manual print, measure lid fit vs spec
   (fit_clearance=0.2 is a guess until then).

**Open questions:** verify's oracles assume cooperative codegen (documented in
verify.py); cutout checks cross-talk with wall defects (observed, harmless).

**Last updated:** 2026-07-17 (session: Phase B build + first golden run).
