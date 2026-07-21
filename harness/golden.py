"""Golden benchmark runner: per-backend pass-rate table over golden/tasks.json.

Two separately-scored stages (extraction noise must not contaminate the
codegen score): extraction is graded against the task's expected spec, and
codegen always starts from the EXPECTED spec, not the extracted one.

Usage:
  python -m harness.golden --backend anthropic [--model claude-haiku-4-5]
  python -m harness.golden --backend ollama --model llama3.1:8b --limit 2

Hybrid (local extraction, frontier codegen): --extract-backend/--extract-model
route the extract stage to a different backend; codegen stays on --backend.
  python -m harness.golden --backend claude-code --model sonnet \
      --extract-backend ollama --extract-model qwen3:4b
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from harness.backends import get_backend
from harness.loop import extract_spec, generate_part
from toolchain.families import get_family
from toolchain.slicecheck import orca_available, slice_workdir

ROOT = Path(__file__).parent.parent


def load_tasks(path: Path) -> list[dict]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    for t in tasks:
        fam = get_family(t.get("family", "enclosure"))
        fam.spec_cls.model_validate(t["expected"])  # fail loud on a bad golden file
    return tasks


def approx_equal(a, b, tol: float = 1e-6) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(approx_equal(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(approx_equal(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not (
        isinstance(a, bool) or isinstance(b, bool)
    ):
        return abs(a - b) <= tol
    return a == b


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", required=True,
                   choices=["anthropic", "ollama", "claude-code"])
    p.add_argument("--model", default=None)
    p.add_argument("--extract-backend", default=None,
                   choices=["anthropic", "ollama", "claude-code"])
    p.add_argument("--extract-model", default=None)
    p.add_argument("--stage", default="both", choices=["extract", "codegen", "both"])
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--max-iterations", type=int, default=3)
    p.add_argument("--tasks", default=str(ROOT / "golden" / "tasks.json"))
    args = p.parse_args(argv)
    if args.extract_model and not args.extract_backend:
        p.error("--extract-model requires --extract-backend")

    backend = get_backend(args.backend, args.model)
    extract_backend = (get_backend(args.extract_backend, args.extract_model)
                       if args.extract_backend else backend)
    tasks = load_tasks(Path(args.tasks))
    if args.limit:
        tasks = tasks[: args.limit]

    def safe(name: str) -> str:
        return name.replace(":", "-").replace("/", "-").replace(".", "_")

    safe_name = safe(backend.name)
    if extract_backend is not backend:
        safe_name = f"hybrid--{safe(extract_backend.name)}--{safe(backend.name)}"
    stem = Path(args.tasks).stem
    if stem != "tasks":  # non-default task file gets its own results/workdir names
        safe_name = f"{safe_name}--{stem}"
    workroot = ROOT / "out" / "golden" / safe_name
    results_dir = ROOT / "results"
    results_dir.mkdir(exist_ok=True)

    rows = []
    for task in tasks:
        fam = get_family(task.get("family", "enclosure"))
        expected = fam.spec_cls.model_validate(task["expected"])
        row: dict = {"id": task["id"], "family": fam.name}

        if args.stage in ("extract", "both"):
            t0 = time.monotonic()
            er = extract_spec(extract_backend, task["request"], fam)
            match = bool(
                er.ok
                and approx_equal(fam.dump_canonical(er.spec), fam.dump_canonical(expected))
            )
            row["extract"] = {
                "ok": match, "parsed": er.ok, "attempts": er.attempts,
                "seconds": round(time.monotonic() - t0, 1),
                "error": er.error if not er.ok else ("spec mismatch" if not match else ""),
            }
            print(f"[{task['id']}] extract: {'PASS' if match else 'FAIL'} "
                  f"({row['extract']['seconds']}s)", flush=True)

        if args.stage in ("codegen", "both"):
            t0 = time.monotonic()
            cr = generate_part(backend, expected, workroot / task["id"], fam,
                               max_iterations=args.max_iterations)
            row["codegen"] = {
                "ok": cr.ok, "iterations": cr.iterations,
                "seconds": round(time.monotonic() - t0, 1),
                "failures_last": cr.failures_last[:8],
            }
            print(f"[{task['id']}] codegen: {'PASS' if cr.ok else 'FAIL'} "
                  f"iter={cr.iterations} ({row['codegen']['seconds']}s)", flush=True)

            # printability column: slice the verified part(s); reporting only,
            # verify remains the gate
            if cr.ok and orca_available():
                parts = tuple((f, f.split(".")[0]) for f in fam.output_files)
                workdir = workroot / task["id"] / f"iter{cr.iterations}"
                row["slice"] = [
                    {"part": s.part, "ok": s.ok, "minutes": s.minutes,
                     "layers": s.layers, "cm3": s.filament_cm3}
                    for s in slice_workdir(workdir, parts=parts)
                ]
                summary = ", ".join(
                    f"{s['part']} {s['minutes']}min" if s["ok"] else f"{s['part']} FAIL"
                    for s in row["slice"])
                print(f"[{task['id']}] slice: {summary}", flush=True)

        rows.append(row)

    out = {"backend": backend.name, "extract_backend": extract_backend.name,
           "stage": args.stage, "max_iterations": args.max_iterations, "rows": rows}
    out_path = results_dir / f"{safe_name}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    # markdown table
    title = (backend.name if extract_backend is backend
             else f"extract={extract_backend.name} + codegen={backend.name}")
    lines = [f"\n### {title}", "",
             "| task | extract | codegen (iters) | slice |", "|---|---|---|---|"]
    ex_pass = cg_pass = 0
    for r in rows:
        ex = r.get("extract")
        cg = r.get("codegen")
        ex_cell = "—" if ex is None else ("PASS" if ex["ok"] else "FAIL")
        cg_cell = "—" if cg is None else (
            f"{'PASS' if cg['ok'] else 'FAIL'} ({cg['iterations']})")
        sl_cell = "—" if "slice" not in r else ", ".join(
            f"{s['part']} {s['minutes']}min" if s["ok"] else f"{s['part']} FAIL"
            for s in r["slice"])
        ex_pass += bool(ex and ex["ok"])
        cg_pass += bool(cg and cg["ok"])
        lines.append(f"| {r['id']} | {ex_cell} | {cg_cell} | {sl_cell} |")
    lines.append(
        f"| **total** | **{ex_pass}/{len(rows)}** | **{cg_pass}/{len(rows)}** | |")
    print("\n".join(lines))
    print(f"\nresults written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
