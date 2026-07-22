"""Send an approved, staged build to the printer — the reviewed print leg.

  python -m harness.send <build-dir> --plate-clear [--part frame]

Sending is deliberately a separate invocation from building: the review gate
means an approval can only exist AFTER a human has seen the staged artifacts,
so a single build-and-send command is impossible by design. The flow is
harness.make -> open review.html -> harness.review --approve -> here.

Two independent human attestations gate a send:
  - the review approval (harness.review), hash-bound to the artifacts —
    covers WHAT is printed;
  - --plate-clear, per-invocation — covers the MOMENT (no sensor reports an
    empty plate; an autonomous session must never pass it on its own
    authority).

Exit codes (shared ledger with harness.make): 0 started and confirmed from
printer state, 6 preconditions unmet (attestation, part choice, missing
files), 7 physical preflight refused, 8 upload mismatch, 9 start not
confirmed, 10 review gate refused (no verdict / rejected / stale).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import bambulabs_api as _bl

from harness.review import send_gate
from printleg import preflight as _preflight
from printleg.ftps import PrinterFTPS
from printleg.probe import load_credentials
from toolchain import profiles as _profiles
from toolchain.families import get_family

START_VERIFY_BUDGET_S = 64.0
START_POLL_S = 8.0


def send_print(dest: Path, part: str) -> int:
    """Upload dest/<part>/<part>.gcode.3mf and remote-start it, verified.

    Caller has already run the review gate and --plate-clear; this function
    gates on the machine's own reported state (preflight) and confirms the
    start by reading gcode_state + subtask_name back, per
    printleg/commands.py's success-is-observed-state discipline.
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
    p.add_argument("build_dir", help="staged build directory (has manifest.json)")
    p.add_argument("--plate-clear", action="store_true",
                   help="human attestation: I looked, the build plate is empty")
    p.add_argument("--part", default=None,
                   help="which part to send for multi-part families")
    args = p.parse_args(argv)

    dest = Path(args.build_dir)
    manifest_path = dest / "manifest.json"
    if not manifest_path.is_file():
        print(f"REFUSED: not a staged build (no manifest.json): {dest}")
        return 6
    if not args.plate_clear:
        print("REFUSED: --plate-clear required (a human must look at the "
              "plate; no sensor reports it)")
        return 6
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fam = get_family(manifest["family"])
    parts = [f.split(".")[0] for f in fam.output_files]
    part = args.part or (parts[0] if len(parts) == 1 else None)
    if part not in parts:
        print(f"REFUSED: pick --part from {parts} — one plate, one part")
        return 6
    gcode_3mf = dest / part / f"{part}.gcode.3mf"
    if not gcode_3mf.is_file():
        print(f"REFUSED: no staged print file ({gcode_3mf}); slice first")
        return 6

    ok, why = send_gate(dest, fam)
    if not ok:
        print(f"REFUSED at review gate: {why}")
        return 10

    return send_print(dest, part)


if __name__ == "__main__":
    raise SystemExit(main())
