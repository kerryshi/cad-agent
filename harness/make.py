"""End-to-end: English request -> verified CadQuery part -> staged print files.

The product surface over the benchmark pieces. Hybrid by default: local
extraction (schema-constrained decoding), frontier codegen, deterministic
verification, thermal-gated slicing. Every gate refuses loudly with a
distinct exit code; nothing silently degrades.

There is no oracle for intent here (golden compares against an expected spec;
a live request has none) — the extracted spec is echoed and written to the
manifest so the human can check the numbers BEFORE printing.

Output layout under --out/<name>/:
  <part>.step            verified CAD (assembled coordinates)
  <part>/<part>.gcode.3mf + <part>/plate_1.gcode   per-part print files
  renders/<part>_<view>.png                        multi-view renders
  review.html            self-contained review page (open it, look, verdict)
  manifest.json          request, spec, backends, iterations, slice stats

Usage:
  python -m harness.make "An enclosure 80 mm long, 60 wide..." --name pi-box
  python -m harness.make "A 3-inch quad frame plate..." --family frame \
      --name micro-frame --no-slice

Every build stages a review page; printing is gated on a human verdict
(harness.review) recorded AFTER looking at it. --send therefore cannot
start a print in the same invocation that built the part — a fresh build
has no approval by construction. It still stages everything, then refuses
at the review gate with the exact next commands; the actual send happens
via `python -m harness.send <dir> --plate-clear` once approved.

Exit codes: 2 extraction refused, 3 codegen/verify failed, 4 slice refused,
5 destination exists (use --force), 6 send preconditions unmet,
7 physical preflight refused, 8 upload mismatch, 9 start not confirmed,
10 review gate refused (no verdict / rejected / stale approval).
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from harness.backends import get_backend
from harness.loop import extract_spec, generate_part
from harness.review import send_gate, write_review_page
from harness.send import send_print
from toolchain.families import get_family
from toolchain.render import render_build
from toolchain.slicecheck import stage_print

ROOT = Path(__file__).parent.parent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("request", help="the English part request")
    p.add_argument("--name", required=True, help="output directory name")
    p.add_argument("--family", default="enclosure", choices=["enclosure", "frame"])
    p.add_argument("--out", default=str(ROOT / "prints"))
    p.add_argument("--extract-backend", default="ollama",
                   choices=["anthropic", "ollama", "claude-code"])
    p.add_argument("--extract-model", default=None)
    p.add_argument("--backend", default="claude-code",
                   choices=["anthropic", "ollama", "claude-code"])
    p.add_argument("--model", default="sonnet")
    p.add_argument("--max-iterations", type=int, default=3)
    p.add_argument("--no-slice", action="store_true",
                   help="stop after verify (no print files staged)")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing output directory")
    p.add_argument("--send", action="store_true",
                   help="attempt upload + remote-start after staging (refused "
                        "until the build is review-approved; see harness.send)")
    p.add_argument("--plate-clear", action="store_true",
                   help="human attestation: I looked, the build plate is empty")
    p.add_argument("--send-part", default=None,
                   help="which part to send for multi-part families")
    args = p.parse_args(argv)
    if args.send and args.no_slice:
        p.error("--send needs staged print files; drop --no-slice")

    fam = get_family(args.family)
    if args.send:
        # part choice is validated BEFORE any model spend; the review and
        # plate-clear gates come after staging (they need the build to exist)
        parts = [f.split(".")[0] for f in fam.output_files]
        send_part = args.send_part or (parts[0] if len(parts) == 1 else None)
        if send_part not in parts:
            print(f"REFUSED: pick --send-part from {parts} — one plate, one part")
            return 6
    dest = Path(args.out) / args.name
    if dest.exists() and any(dest.iterdir()) and not args.force:
        print(f"REFUSED: {dest} exists and is not empty (--force to overwrite)")
        return 5

    extractor = get_backend(args.extract_backend, args.extract_model)
    coder = get_backend(args.backend, args.model)

    er = extract_spec(extractor, args.request, fam)
    if not er.ok:
        print(f"REFUSED at extraction ({extractor.name}): {er.error}")
        return 2
    spec = er.spec
    print(f"extracted spec ({extractor.name}) — CHECK THESE NUMBERS:")
    print(spec.model_dump_json(indent=2))

    workroot = dest / "_build"
    cr = generate_part(coder, spec, workroot, fam,
                       max_iterations=args.max_iterations)
    if not cr.ok:
        print(f"REFUSED at codegen/verify ({coder.name}): {cr.detail}")
        for line in cr.failures_last:
            print(f"  {line}")
        return 3
    workdir = workroot / f"iter{cr.iterations}"
    print(f"verified in {cr.iterations} iteration(s)")

    for filename in fam.output_files:
        shutil.copy2(workdir / filename, dest / filename)

    renders = render_build(dest, fam)
    print(f"rendered {len(renders)} view(s) -> {dest / 'renders'}")

    slices = []
    if not args.no_slice:
        for filename in fam.output_files:
            part = filename.split(".")[0]
            s = stage_print(dest / filename, dest / part, part=part)
            if not s.ok:
                print(f"REFUSED at slice ({part}): {s.detail}")
                return 4
            slices.append({"part": part, "minutes": s.minutes,
                           "layers": s.layers, "cm3": s.filament_cm3})
            print(f"staged {part}: {s.minutes} min, {s.layers} layers, "
                  f"{s.filament_cm3} cm3")

    manifest = {
        "request": args.request,
        "family": fam.name,
        "extract_backend": extractor.name,
        "codegen_backend": coder.name,
        "spec": spec.model_dump(mode="json"),
        "iterations": cr.iterations,
        "slices": slices,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    page = write_review_page(dest)
    print(f"staged in {dest}")
    print(f"REVIEW IT: open {page}, then record a verdict:")
    print(f'  python -m harness.review "{dest}" --approve   (or --reject '
          '--comment "...")')
    if not args.no_slice:
        print(f'then send: python -m harness.send "{dest}" --plate-clear'
              + (f" --part {send_part}" if args.send and args.send_part else ""))

    if args.send:
        ok, why = send_gate(dest, fam)
        if not ok:
            print(f"REFUSED at review gate: {why}")
            return 10
        if not args.plate_clear:
            print("REFUSED: --plate-clear required (a human must look at the "
                  "plate; no sensor reports it)")
            return 6
        return send_print(dest, send_part)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
