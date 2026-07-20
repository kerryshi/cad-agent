"""Physical-state gate: refuse to send a print the machine isn't set up for.

The thermal gate (toolchain/slicecheck) proves the gcode agrees with the
profile we sliced against. It cannot know what is actually loaded in the
printer. This closes that gap — same discipline one layer down: check the
physical state, never assume it matches the file.

P2S schema notes (measured 2026-07-20, firmware as shipped):
  * Loaded filament lives in `print.vir_slot[]`, NOT `vt_tray` (empty dict)
    and NOT the AMS `tray[]` records (which report only id/state here).
    bambulabs_api 2.6.6 reads none of these, so `vir_slot` is parsed directly.
  * MQTT sends partial deltas. A key absent from one payload is NOT absent
    state — always `pushall()` and wait before reading, or a check will pass
    on a message that simply didn't mention the field.
  * `nozzle_type` is 'HS01' (hardened steel), which the library's enum
    rejects with ValueError; read it from the raw payload instead.
  * `hw_switch_state` tracks filament presence (2 = none, 3 = loaded here).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

PUSHALL_SETTLE_S = 6.0
IDLE_STATES = {"IDLE", "FINISH", "FAILED"}


@dataclass
class Preflight:
    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append((name, ok, detail))
        if not ok:
            self.ok = False

    def report(self) -> str:
        lines = [f"  {'ok  ' if ok else 'FAIL'} {n:22s} {d}" for n, ok, d in self.checks]
        return "\n".join(lines)


def loaded_filament(payload: dict) -> dict | None:
    """The slot the printer is actually feeding from, or None."""
    for slot in payload.get("vir_slot", []) or []:
        if slot.get("tray_type"):
            return slot
    for unit in payload.get("ams", {}).get("ams", []) or []:
        for tray in unit.get("tray", []) or []:
            if tray.get("tray_type"):
                return tray
    return None


def check(printer, want_filament: str, want_nozzle_c: int,
          want_nozzle_mm: float = 0.4) -> Preflight:
    """Gate a physical printer against what we are about to send it."""
    printer.mqtt_client.pushall()
    time.sleep(PUSHALL_SETTLE_S)
    payload = printer.mqtt_dump().get("print", {})
    pre = Preflight(ok=True)

    if not payload:
        pre.add("payload", False, "empty MQTT payload — cannot verify anything")
        return pre

    state = payload.get("gcode_state")
    pre.add("printer idle", state in IDLE_STATES, f"gcode_state={state!r}")

    err = payload.get("print_error", 0)
    pre.add("no error", not err, f"print_error={err}")

    # The P2S has NO SD slot — it takes a USB-A drive (verified against Bambu's
    # own P2S docs 2026-07-20, after wrongly assuming P1/X1-style microSD).
    # The MQTT field is still named `sdcard`; on this model it reports the USB
    # drive. Don't rename it back to match the field.
    #
    # Without storage, FTPS fails with a bare "553 Could not create file" that
    # names neither the drive nor the cause — and STOR fails at every path,
    # MKD fails, and / lists empty, so it reads like a path or permissions bug.
    # Check the state that explains it instead of the error that doesn't.
    storage = payload.get("sdcard")
    pre.add("usb storage", storage is True,
            "present" if storage else
            "NO USB DRIVE — upload has nowhere to write (FAT32, <=32GB)")

    slot = loaded_filament(payload)
    if slot is None:
        pre.add("filament loaded", False,
                "no slot reports a material — load filament, or set its type "
                "on the touchscreen if the spool has no RFID tag")
        return pre

    got = slot.get("tray_type", "")
    pre.add("filament matches", got == want_filament,
            f"loaded {got!r}, sliced for {want_filament!r}")

    lo = int(slot.get("nozzle_temp_min") or 0)
    hi = int(slot.get("nozzle_temp_max") or 0)
    if lo and hi:
        pre.add("nozzle temp in range", lo <= want_nozzle_c <= hi,
                f"gcode wants {want_nozzle_c}C, spool allows {lo}-{hi}C")

    nozzles = payload.get("device", {}).get("nozzle", {}).get("info", [])
    if nozzles:
        got_mm = nozzles[0].get("diameter")
        pre.add("nozzle diameter", got_mm == want_nozzle_mm,
                f"printer {got_mm}mm, profile {want_nozzle_mm}mm")

    # No RFID means the printer cannot report how much is left; say so rather
    # than let a silent 0 read as "empty" or as "fine".
    remain = slot.get("remain", -1)
    if not slot.get("tag_uid", "").strip("0"):
        pre.add("filament remaining", True,
                "UNKNOWN - no RFID tag, printer cannot measure; eyeball the spool")
    else:
        pre.add("filament remaining", remain > 5, f"{remain}% left")

    return pre
