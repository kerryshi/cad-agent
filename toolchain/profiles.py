"""Resolve OrcaSlicer profile inheritance — its CLI does not.

OrcaSlicer's `--load-settings` / `--load-filaments` read one JSON file each
and apply only the keys literally present in that file, so every value living
in a parent profile falls back to the slicer's own built-in default. Nothing
is logged; the slice succeeds and looks normal.

The mechanism is worth stating precisely, because "the CLI ignores inherits"
is wrong and would send the next reader looking for a flag that does not
exist. The CLI *does* resolve `inherits` — against pre-flattened vendor
directories `profiles/BBL/{machine,process,filament}_full/` (strings in
OrcaSlicer.dll: "can not find parent preset for %1%, inherits %2%"). This
portable build ships only `{machine,process,filament}/`, so resolution finds
no parent and degrades quietly. There is no CLI flag that fixes this; the
alternative would be populating `*_full/` inside the install tree, which is
worse than flattening to a temp file.

Measured on this machine (OrcaSlicer 2.4.2 portable, 2026-07-19) with
`Bambu PETG Basic @BBL P2S 0.4 nozzle` — a three-deep chain:

    key                  resolved   CLI produced   came from
    filament_type        PETG       PLA            fdm_filament_common (root)
    hot_plate_temp       70         45             Orca default
    cool_plate_temp      0          35             Orca default
    textured_plate_temp  70         45             Orca default
    nozzle_temperature   250        250            leaf file — survived
    printable_area       256x256    200x200        fdm_bbl_3dp_001_common

That combination shipped two staged frame prints as "PETG" gcode carrying
filament_type=PLA and `M140 S35` — a 35 C bed. PETG does not adhere to a cold
plate, so both would have released in the first few layers. `slice_stl`
reported ok=True with plausible time and filament stats throughout: the
failure mode is silent, which is why `slicecheck.check_thermal` now gates it.

This module flattens a chain to a single self-contained JSON the CLI can't
misread. Resolution refuses rather than degrades: a missing parent raises, so
a partial profile can never reach the slicer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_PROFILE_ROOT_ENV = "CAD_AGENT_ORCA"
_DEFAULT_ORCA = r"C:\Users\PC\tools\OrcaSlicer\orca-slicer.exe"

# Bed installed on Kerry's P2S (confirmed 2026-07-19); falls back to the
# machine descriptor's own `default_bed_type` rather than a second literal.
# Bambu's enum spelling — see the `curr_bed_type` values in OrcaSlicer.dll.
_BED_OVERRIDE = os.environ.get("CAD_AGENT_BED")

# Which per-filament temperature key each plate reads.
BED_TEMP_KEY = {
    "Cool Plate": "cool_plate_temp",
    "Cool Plate (SuperTack)": "supertack_plate_temp",
    "Supertack Plate": "supertack_plate_temp",
    "Engineering Plate": "eng_plate_temp",
    "High Temp Plate": "hot_plate_temp",
    "Smooth PEI Plate": "hot_plate_temp",
    "Textured PEI Plate": "textured_plate_temp",
}


def profile_dir(kind: str) -> Path:
    """`kind` is one of machine | process | filament."""
    exe = Path(os.environ.get(_PROFILE_ROOT_ENV, _DEFAULT_ORCA))
    return exe.parent / "resources" / "profiles" / "BBL" / kind


def _find(kind: str, name: str) -> Path:
    root = profile_dir(kind)
    direct = root / (name if name.endswith(".json") else f"{name}.json")
    if direct.is_file():
        return direct
    stem = direct.stem
    matches = sorted(c for c in root.rglob("*.json") if c.stem == stem)
    if len(matches) > 1:
        raise ValueError(
            f"ambiguous profile {name!r} under {root}: {len(matches)} files "
            f"share that stem ({', '.join(m.name for m in matches[:3])}...) — "
            "refusing to guess which parent to inherit from"
        )
    if matches:
        return matches[0]
    raise FileNotFoundError(
        f"profile {name!r} not found under {root} — cannot resolve the "
        "inheritance chain, refusing to emit a partial profile"
    )


def resolve(kind: str, name: str) -> dict:
    """Flatten a profile and its ancestors into one dict (leaf wins).

    Raises if any link in the chain is missing: a partially-resolved profile
    is exactly the silent degradation this module exists to prevent.
    """
    chain: list[dict] = []
    seen: set[str] = set()
    current: str | None = name
    while current:
        path = _find(kind, current)
        if str(path) in seen:
            raise ValueError(f"circular inherits at {path}")
        seen.add(str(path))
        data = json.loads(path.read_text(encoding="utf-8"))
        chain.append(data)
        current = data.get("inherits") or None

    merged: dict = {}
    for data in reversed(chain):  # root first, leaf last
        merged.update(data)
    merged.pop("inherits", None)
    merged["name"] = Path(name).stem
    return merged


def write_flat(kind: str, name: str, dest: Path, extra: dict | None = None) -> Path:
    """Write a resolved profile to `dest` and return the path."""
    data = resolve(kind, name)
    if extra:
        data.update(extra)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return dest


def _descriptor(machine: str | None = None) -> dict:
    """The vendor's `machine_model` descriptor for the configured machine."""
    from toolchain.slicecheck import MACHINE

    preset = resolve("machine", machine or MACHINE)
    data = json.loads(
        _find("machine", preset["printer_model"]).read_text(encoding="utf-8")
    )
    if data.get("type") != "machine_model":
        raise ValueError(
            f"{preset['printer_model']}.json is not a machine_model descriptor"
        )
    return data


def bed_type(machine: str | None = None) -> str:
    """Installed build plate: CAD_AGENT_BED, else the machine's factory default.

    Refuses a plate the vendor marks unsupported for this machine — the same
    class of mistake as a cold bed, caught before a slice is attempted.
    """
    data = _descriptor(machine)
    bed = _BED_OVERRIDE or data["default_bed_type"]
    unsupported = [
        b.strip() for b in data.get("not_support_bed_type", "").split(";") if b.strip()
    ]
    if bed in unsupported:
        raise ValueError(
            f"{data['name']} does not support the {bed}; "
            f"unsupported: {', '.join(unsupported)}"
        )
    if bed not in BED_TEMP_KEY:
        raise ValueError(
            f"unknown bed type {bed!r}; expected one of {sorted(BED_TEMP_KEY)}"
        )
    return bed


def model_id(machine: str | None = None) -> str:
    """Bambu's short model code (P2S -> "N7") for the configured machine.

    Lives in the vendor's `machine_model` descriptor (e.g. "Bambu Lab P2S.json",
    type=machine_model), which the CLI never loads — it reads only the machine
    *preset*. So `printer_model_id` lands empty in every CLI-exported 3mf, and
    the printer loses the field it uses to tell whether a file is meant for it.
    Read it from the descriptor rather than hardcoding the code.
    """
    return str(_descriptor(machine)["model_id"])


def _first(value):
    """Orca stores most per-extruder values as single-element lists."""
    return value[0] if isinstance(value, list) else value


def expected_filament_type(filament: str | None = None) -> str:
    from toolchain.slicecheck import FILAMENT

    return str(_first(resolve("filament", filament or FILAMENT)["filament_type"]))


def expected_bed_temps(
    filament: str | None = None, bed: str | None = None
) -> tuple[int, int]:
    """(initial_layer, steady) bed temps for the installed plate.

    Two values, not one: Orca emits a separate initial-layer setpoint, and
    profiles disagree between them (Bambu PLA Translucent is 60 then 55).
    Collapsing them hides a cold first layer under a correct steady temp —
    the adhesion failure this module exists to catch — and false-refuses
    every stock profile where the two differ.

    Derived from the profile chain, never hardcoded: a literal here would
    drift away from the profile it is supposed to police.
    """
    from toolchain.slicecheck import FILAMENT

    bed = bed or bed_type()
    key = BED_TEMP_KEY[bed]
    resolved = resolve("filament", filament or FILAMENT)
    steady = int(_first(resolved[key]))
    initial = int(_first(resolved.get(f"{key}_initial_layer", resolved[key])))
    if 0 in (steady, initial):
        raise ValueError(
            f"{resolved['name']} sets {key}=0 — Bambu marks the {bed} "
            "unsupported for this filament. Swap the plate or the filament; "
            "slicing it would produce a print that cannot stick."
        )
    return initial, steady


def expected_nozzle_temp(filament: str | None = None) -> int:
    """Hottest nozzle setpoint the resolved profile calls for.

    Gated as a max rather than per-phase: the P2S start sequence commands a
    string of purge/preheat temperatures (140, 165, 180...) before the print
    temp, so the peak is the only reading that maps cleanly onto a profile
    value.
    """
    from toolchain.slicecheck import FILAMENT

    resolved = resolve("filament", filament or FILAMENT)
    return max(
        int(_first(resolved["nozzle_temperature"])),
        int(_first(resolved.get("nozzle_temperature_initial_layer",
                                resolved["nozzle_temperature"]))),
    )
