# cad-agent — STATUS

**CI gate (installed 2026-07-19, CI/CD PRD WS4):** merge gate + slicecheck
skip guard, all local (no remote by choice — pre-push can never fire here).
- **Mechanism:** `.githooks/pre-merge-commit` runs
  `./.venv/Scripts/python.exe -m pytest tests/ -q` (refuse-over-skip if the
  venv is missing). Suite measured **27.1s** (2026-07-19) — over the PRD's
  10s pre-commit budget, so per rule 7 the suite gates **merges**, not every
  commit (recorded decision, not a skip). `.githooks/pre-commit` is the
  merge-completion guard: MERGE_HEAD or `.git/SQUASH_MSG` present → re-runs
  the full merge gate; plain commits pass instantly.
- **Skip guard:** `tests/conftest.py` turns ANY skipped test in
  `tests/test_slicecheck.py` into pytest exit 1 with a named
  "SKIP-GUARD REFUSAL" ("a skip is not a pass" made mechanical). Other
  modules' skips keep normal semantics.
- **Install (per-clone, re-run after any fresh clone):**
  `git config core.hooksPath .githooks && git config merge.ff false`; exec
  bit is in the index; `.gitattributes` pins LF on hooks.
- **Landing discipline:** work lands on main via **non-ff merge** so the
  gate fires. Direct commits to main are a NAMED SIDE DOOR (they run only
  the instant pre-commit guard); other accepted holes: `--no-verify`,
  rebase/cherry-pick run no hooks. Golden benchmark stays **manual** —
  hours-scale, needs LLM backends; the gate never invokes `harness.golden`.
- **Refuse-first evidence (2026-07-19, scratch proof-base branch, all
  cleaned up):** planted red → merge refused exit 1, HEAD unmoved
  (c4c73fc before/after); `git commit` completing the refused merge also
  blocked exit 1; `CAD_AGENT_ORCA=C:/bogus` → SKIP-GUARD refused the merge
  exit 1, HEAD unmoved; clean green merge landed (23.8s); stripped-env
  (`env -i`) merge completion ran the full gate green via absolute paths —
  finding: Windows Python needs `USERPROFILE` (cadquery's `Path.home()`),
  not just `HOME`. GUI-client (VS Code) commit not yet exercised — named gap.

**Where we are:** Phases A (toolchain) and B (harness) complete. Phase C:
renderer DONE; VLM critique shelved on an honest negative result (see below).
Phase D part 1 DONE: headless OrcaSlicer slice check with bundled P2S
profiles. **Golden benchmark headline (2026-07-17):**

| backend | extract | codegen |
|---|---|---|
| ollama:llama3.1:8b | 5/8 | 0/8 (all hit the 3-iter cap) |
| claude-code:sonnet | **8/8** | **8/8 (every task iter=1)** |

Same tasks, same sandbox, same verifier — the pass-rate gap is the artifact.
Sonnet runs via the `claude -p` adapter on the Max plan (no API key needed).
Its t1 part renders visually identical to the reference builder's output.

**Partial Haiku column (2026-07-17, run stopped externally before finishing —
results JSON not written):** claude-code:haiku failed enclosure codegen on
t1–t4 at the 3-iteration cap while extraction kept passing. The t2 iteration
trace shows the loop working but the model unable to close: iter1 failed
bbox+posts+lid+interference, iter2 fixed all but posts, iter3 regressed to an
unloadable STEP. Resume with: `python -m harness.golden --backend claude-code
--model haiku` (and `--tasks golden/frame_tasks.json`). qwen2.5-coder:14b runs
(timeout fixed to 900s, commit f-series) also stopped before completing —
same resume commands with `--backend ollama --model qwen2.5-coder:14b`.

**v2 family shipped (2026-07-17): drone frames.** Flat X-quad plate —
FrameSpec (wheelbase, FC/motor mount pattern enums, prop-collision
validation), reference builder + 3 sabotages, spec-derived frame verifier,
family registry (`toolchain/families.py`) threading enclosure|frame through
prompts/loop/golden. Frame golden run, claude-code:sonnet: **4/4 extract,
4/4 codegen, all iter=1** (`results/claude-code-sonnet--frame_tasks.json`).

**PRINT FILES STAGED for Kerry** (gitignored, `prints/`): agent-generated,
probe-verified, PETG-sliced, **re-staged 2026-07-19** after the profile-
inheritance defect below invalidated the 07-17 batch —
- `prints/f1-5inch-freestyle/` — 62.1 min, 25 layers, 21.7 cm³ PETG
- `prints/f2-3inch-micro/` — 39.5 min, 20 layers, 10.8 cm³ PETG
Each has `<name>.gcode.3mf`, raw gcode, and renders. **To print: copy the
`.gcode.3mf` to the microSD, print from the touchscreen.** Bambu Studio is NOT
installed on this machine (the 07-17 note said to use it — wrong); OrcaSlicer
portable is the only slicer here. Re-stage any part with
`python -m toolchain.slicecheck <part.step> <dest-dir> --part <name>`.
After printing: check hole fit (M3/M2 screws), plate flatness, arm stiffness.

**DEFECT FOUND + FIXED (2026-07-19): OrcaSlicer's CLI ignores `inherits`.**
`--load-settings` / `--load-filaments` apply only keys literally present in
the leaf JSON; every parent key silently falls back to the slicer's own
default. Nothing is logged and the slice succeeds. Measured on the PETG chain
(3 deep): `filament_type` PETG→**PLA**, plate temps 70→**45/35**,
`printable_area` 256×256→**200×200**. The two frames staged 07-17 therefore
carried `M140 S35` — a 35 °C bed for PETG, i.e. first-layer release — while
`slice_stl` reported ok=True with plausible time/layer/volume stats. Nozzle
temp came out right (250 °C, a leaf key), which is what made it look sane.
- **Fix:** `toolchain/profiles.py` flattens the chain before invoking the CLI
  (refuses on a missing parent rather than emitting a partial profile).
- **Gate:** `slicecheck.check_thermal` refuses any slice whose gcode
  `filament_type` or bed temp disagrees with the resolved profile — both
  expectations derived from the chain, no literals. This is the check whose
  absence let a cold-bed slice pass as green.
- **Also fixed:** `curr_bed_type` is now set explicitly (Textured PEI Plate,
  the P2S factory default per its `machine_model` descriptor; `CAD_AGENT_BED`
  overrides), and `printer_model_id` — left empty by the CLI because it lives
  in a descriptor the CLI never loads — is patched into the staged 3mf from
  the descriptor (`N7` for the P2S), gcode payload copied byte-for-byte so the
  md5 sidecar stays valid.
- **Fail-first evidence:** the new thermal tests fail on the pre-fix
  invocation with `asked for PETG, gcode says PLA`; sabotage cases (wrong
  `filament_type`, 35 °C bed, cold first layer) are each refused by the
  owning check. Notably, once inheritance resolved, OrcaSlicer's *own*
  validator started refusing the unconfigured case — `Plate 1: Cool Plate
  does not support filament 1` — which the bogus 35 °C default had masked.
- **Review caught a blocker in the first cut (fixed).** The bed gate read one
  temperature via `max()` over the body. That (a) false-refused any stock
  profile whose initial and steady temps differ — `Bambu PLA Translucent` is
  60 then 55, and under `CAD_AGENT_FILAMENT` the suite went 7-failed — and
  (b) **accepted a 35 °C first layer under a correct 70 °C steady bed**, the
  exact adhesion failure the gate exists to stop. PETG hides it because its
  initial and steady are both 70. Now parsed and gated as two values, plus
  peak nozzle temp. Proof: old parser `ACCEPTED (hole)`, new parser
  `REFUSED: bed temp (first layer) 35C, profile specifies 60C`.
- **Mechanism correction:** the CLI does *not* "ignore" `inherits` — it
  resolves against `profiles/BBL/*_full/` dirs the portable build doesn't
  ship, so lookup fails and degrades quietly. No flag fixes it. (The first
  write-up said "ignored", which would send the next reader hunting for a
  flag that doesn't exist.)
- **Not verified:** nothing has been printed yet. The gate proves the gcode is
  thermally coherent with the profile, not that the part comes out good.
  Whether the printer accepts the rewritten zip is untestable without the
  hardware.

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
anthropic 0.117.0, pytest. **OrcaSlicer 2.4.2 portable** at
`C:\Users\PC\tools\OrcaSlicer` (winget install fails silently from a
background shell — UAC 0x800704c7; portable build needs no elevation;
`CAD_AGENT_ORCA` env var overrides the path). Ollama up (llama3.1:8b,
qwen3:4b, qwen2.5vl:7b).

**Phase C/D additions (2026-07-17):**
- `toolchain/render.py` — VTK offscreen multi-view PNGs (4 body + 2 lid views),
  no display/xvfb needed; tests assert non-blank pixels (blank frame is the
  silent-failure mode of headless GL).
- `harness/critique.py` — VLM gross-error critique, mechanically sound but
  **NOT wired into the loop**: negative result — qwen2.5vl:7b hallucinates
  issues on a good part (6 views) and passes a missing-post sabotage (2 views).
  Revisit with Claude vision once API credentials exist.
- `toolchain/slicecheck.py` — slices body and lid SEPARATELY (dropped to bed,
  own plates) with P2S 0.4-nozzle + 0.20mm Standard + PLA Basic profiles;
  parses time/layers/filament from gcode. CLI quirks documented in the module.
  Reference body: 55m49s model time, 150 layers, 24.3 cm³.

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
1. **Printer spike** — put the P2S in LAN-only + Developer Mode, note IP +
   access code. Then run the Phase-0 spike in a parallel session.

**No longer blocked:** the frontier column no longer needs an API key — the
`claude-code` backend runs on the Max plan (`python -m harness.golden
--backend claude-code --model sonnet|opus|haiku`). The `anthropic` API backend
remains for if/when a key exists (also the path to Claude-vision critique,
though `claude -p` can Read PNGs and may cover that too — untested).

**Next actions:**
1. **First physical print** — `prints/f2-3inch-micro/` is the cheaper first
   run (39.5 min, 10.8 cm³). microSD → touchscreen. Confirm the Textured PEI
   plate is installed and PETG is loaded before starting. Then measure hole
   fit vs spec (fit_clearance=0.2 is a guess until measured) and report back —
   the thermal gate proves profile coherence, not print quality.
2. Wire slicecheck into the golden runner as a printability column (verify
   remains the gate; slice stats are reporting).
3. Optional fairness column: `qwen2.5-coder:14b` (code-tuned local model);
   optional `--model haiku` / `opus` claude-code rows.
4. Printer spike in a parallel session once Developer Mode is on (BLOCKED).
5. Revisit VLM critique via `claude -p` with Read access to the render PNGs.

**Open questions:** verify's oracles assume cooperative codegen (documented in
verify.py); cutout checks cross-talk with wall defects (observed, harmless).

**Last updated:** 2026-07-19 (merge gate + skip guard installed, refuse-first proven; prior: 2026-07-17 Phase B build + first golden run).
