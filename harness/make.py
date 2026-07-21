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
  manifest.json          request, spec, backends, iterations, slice stats

Usage:
  python -m harness.make "An enclosure 80 mm long, 60 wide..." --name pi-box
  python -m harness.make "A 3-inch quad frame plate..." --family frame \
      --name micro-frame --no-slice

Exit codes: 2 extraction refused, 3 codegen/verify failed, 4 slice refused,
5 destination exists (use --force).
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from harness.backends import get_backend
from harness.loop import extract_spec, generate_part
from toolchain.families import get_family
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
    args = p.parse_args(argv)

    fam = get_family(args.family)
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
    print(f"staged in {dest}" + ("" if args.no_slice else
          " — copy the *.gcode.3mf to the USB drive, print from the touchscreen"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
