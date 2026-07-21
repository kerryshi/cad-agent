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
  python -m harness.make "..." --family frame --name f9 --send --plate-clear

The print leg (--send) uploads the staged .gcode.3mf over FTPS and remote-
starts it, then confirms the start from printer STATE (never the publish
ack). It hard-requires --plate-clear, a per-invocation human attestation
that the build plate is empty — no sensor reports that, and starting onto
a stray part crashes the toolhead. An autonomous session must never pass
it on its own authority.

Exit codes: 2 extraction refused, 3 codegen/verify failed, 4 slice refused,
5 destination exists (use --force), 6 send preconditions unmet,
7 physical preflight refused, 8 upload mismatch, 9 start not confirmed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import bambulabs_api as _bl

from harness.backends import get_backend
from harness.loop import extract_spec, generate_part
from printleg import preflight as _preflight
from printleg.ftps import PrinterFTPS
from printleg.probe import load_credentials
from toolchain import profiles as _profiles
from toolchain.families import get_family
from toolchain.slicecheck import stage_print

ROOT = Path(__file__).parent.parent
START_VERIFY_BUDGET_S = 64.0
START_POLL_S = 8.0


def send_print(dest: Path, part: str) -> int:
    """Upload dest/<part>/<part>.gcode.3mf and remote-start it, verified.

    Caller has already validated --plate-clear; this function gates on the
    machine's own reported state (preflight) and confirms the start by
    reading gcode_state + subtask_name back, per printleg/commands.py's
    success-is-observed-state discipline.
    """
    gcode_3mf = dest / part / f"{part}.gcode.3mf"
    remote_name = f"{dest.name}.gcode.3mf"

    ip, code, serial = load_credentials()
    printer = _bl.Printer(ip, code, serial)
    printer.connect()
    try:
        for _ in range(20):
            if printer.mqtt_client_ready():
                break
            time.sleep(0.5)
        pre = _preflight.check(
            printer,
            want_filament=_profiles.expected_filament_type(),
            want_nozzle_c=_profiles.expected_nozzle_temp(),
        )
        print(pre.report())
        if not pre.ok:
            print("REFUSED at physical preflight")
            return 7

        with PrinterFTPS(ip, code) as ftp:
            with open(gcode_3mf, "rb") as fh:
                ftp.storbinary(f"STOR /{remote_name}", fh)
            local, remote = gcode_3mf.stat().st_size, ftp.size(f"/{remote_name}")
        if remote != local:
            print(f"REFUSED: upload size mismatch (local {local}, drive {remote})")
            return 8
        print(f"uploaded /{remote_name} ({remote} bytes, byte-exact)")

        printer.mqtt_client.start_print_3mf(remote_name, 1, use_ams=False)
        deadline = time.monotonic() + START_VERIFY_BUDGET_S
        while time.monotonic() < deadline:
            time.sleep(START_POLL_S)
            printer.mqtt_client.pushall()
            time.sleep(3)
            payload = printer.mqtt_dump().get("print", {})
            state = payload.get("gcode_state")
            task = payload.get("subtask_name")
            print(f"  state={state!r} task={task!r}")
            if state in ("RUNNING", "PREPARE") and task == dest.name:
                print("PRINT STARTED (confirmed from printer state)")
                return 0
        print("REFUSED: start not confirmed from state within budget")
        return 9
    finally:
        printer.disconnect()


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
                   help="upload + remote-start the staged print (needs --plate-clear)")
    p.add_argument("--plate-clear", action="store_true",
                   help="human attestation: I looked, the build plate is empty")
    p.add_argument("--send-part", default=None,
                   help="which part to send for multi-part families")
    args = p.parse_args(argv)
    if args.send and args.no_slice:
        p.error("--send needs staged print files; drop --no-slice")

    fam = get_family(args.family)
    if args.send:
        if not args.plate_clear:
            print("REFUSED: --send requires --plate-clear (a human must look "
                  "at the plate; no sensor reports it)")
            return 6
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

    if args.send:
        return send_print(dest, send_part)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
