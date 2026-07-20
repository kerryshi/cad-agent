"""Phase-0 printer spike, step 1: READ-ONLY status probe.

Establishes that we can reach the P2S over LAN and that MQTT auth works,
without moving anything. Nothing here uploads a file, starts a print, heats
a component, or moves an axis — every call is a getter. Step 2 (upload) and
step 3 (start_print) are separate scripts on purpose: first contact with a
physical machine should not be able to move it.

Credentials come from printleg/printer.env (gitignored) or the environment.
The P2S is new (Oct 2025) and its protocol specifics are undocumented, so a
garbled or empty reading here is a real finding, not a bug to code around.

Usage: ./.venv/Scripts/python.exe -m printleg.probe
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ENV_FILE = Path(__file__).parent / "printer.env"


def load_credentials() -> tuple[str, str, str]:
    """Read IP / access code / serial, env taking precedence over the file."""
    values: dict[str, str] = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    missing = []
    out = []
    for key in ("BAMBU_IP", "BAMBU_ACCESS_CODE", "BAMBU_SERIAL"):
        value = os.environ.get(key) or values.get(key)
        if not value:
            missing.append(key)
        out.append(value or "")
    if missing:
        raise SystemExit(
            f"missing credential(s): {', '.join(missing)} — set them in "
            f"{ENV_FILE} or the environment"
        )
    return out[0], out[1], out[2]


def main() -> int:
    import bambulabs_api as bl

    ip, code, serial = load_credentials()
    print(f"connecting to {ip} (serial {serial[:4]}...{serial[-4:]})")

    printer = bl.Printer(ip, code, serial)
    printer.connect()
    try:
        # MQTT needs a moment to receive the first full status payload
        for _ in range(20):
            if printer.mqtt_client_ready():
                break
            time.sleep(0.5)
        else:
            print("  MQTT never reported ready — auth or LAN-mode problem")
            return 1

        print(f"  mqtt connected: {printer.mqtt_client_connected()}")
        readings = [
            ("state", printer.get_current_state),
            ("bed temp", printer.get_bed_temperature),
            ("nozzle temp", printer.get_nozzle_temperature),
            ("chamber temp", printer.get_chamber_temperature),
            ("nozzle diameter", printer.nozzle_diameter),
            ("nozzle type", printer.nozzle_type),
            ("light", printer.get_light_state),
            ("current file", printer.get_file_name),
            ("progress %", printer.get_percentage),
            ("layer", printer.current_layer_num),
            ("total layers", printer.total_layer_num),
        ]
        blank = 0
        for label, getter in readings:
            try:
                value = getter()
            except Exception as e:  # a getter raising is itself a finding
                value = f"<raised {type(e).__name__}: {e}>"
            if value in (None, "", "unknown"):
                blank += 1
            print(f"  {label:16s} {value}")

        print(f"\n{blank}/{len(readings)} readings blank")
        if blank == len(readings):
            print("ALL readings blank — connected but the payload is not being "
                  "parsed. Likely the undocumented P2S/N7 protocol; report as a "
                  "finding rather than working around it.")
            return 1
        return 0
    finally:
        printer.disconnect()
        print("disconnected")


if __name__ == "__main__":
    sys.exit(main())
