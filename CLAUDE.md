# cad-agent — project instructions

Read `STATUS.md` first — it is the source of truth for where the project
stands, evidence, and next actions. Update it before ending a substantial
session.

## Environment (hard rules)
- Python is `.venv` (3.12, created with `py -3.12`). The machine's default
  `python` is 3.13 and must NOT be used — cadquery wheels target 3.12 here.
  Run everything as `./.venv/Scripts/python.exe ...`.
- OrcaSlicer is the PORTABLE build at `C:\Users\PC\tools\OrcaSlicer`
  (`CAD_AGENT_ORCA` overrides). Do not winget-install it — the installer needs
  a UAC prompt background shells can't show and fails silently.
- Orca CLI quirks are documented in `toolchain/slicecheck.py` — read them
  before touching slicer invocations.
- Filament default is Bambu PETG Basic (`CAD_AGENT_FILAMENT` overrides).

## Design invariants (do not weaken)
- The codegen agent NEVER sees or writes verification code. Verifiers derive
  all assertions from the frozen spec (`an-actor-cannot-be-its-own-verifier`).
- Generated code runs only via `harness/runner.py` (subprocess, `-I`, temp
  cwd, timeout, toolchain-reference gate) — never in-process.
- Every new verify check needs refuse-first evidence: a sabotage hook in the
  family's reference builder and a test asserting the owning check refuses it.
- New part families go through `toolchain/families.py`; the harness stays
  family-agnostic.
- Builder output contracts (file names, coordinate conventions) are frozen in
  each family's spec module docstring — changing them is a breaking change.

## Verification
- `./.venv/Scripts/python.exe -m pytest tests/` must be green before commit.
- Slicecheck tests skip loudly when OrcaSlicer is absent — a skip is not a pass.
- VLM critique (`harness/critique.py`) is NOT wired into the loop — negative
  result documented in its docstring; don't enable it without new evidence.
