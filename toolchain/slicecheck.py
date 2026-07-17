"""Printability oracle: headless OrcaSlicer slice of each built part.

Validated on this machine 2026-07-17 with the OrcaSlicer 2.4.2 portable build
and its bundled Bambu Lab P2S profiles. CLI quirks that cost the spike time,
preserved here so they aren't rediscovered:
  * The flag is --load-filaments (plural) — Bambu Studio's --load-filament
    is rejected.
  * Profile paths must be Windows-style (C:\\...); the exe cannot resolve
    Git-Bash /c/... paths.
  * The exe is GUI-subsystem: success prints nothing; errors do reach stderr.
    Judge success by exit code + the presence of plate_1.gcode.

Parts are sliced SEPARATELY (plan review finding #6): the lid sits on the
body in assembled coordinates, and slicing that stack proves nothing. Each
part is dropped to the bed (z-min -> 0) and sliced in its own output dir.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cadquery as cq
from cadquery import Vector

from toolchain.spec import BODY_STEP, LID_STEP

ORCA_EXE = Path(os.environ.get(
    "CAD_AGENT_ORCA", r"C:\Users\PC\tools\OrcaSlicer\orca-slicer.exe"))
PROFILE_DIR = ORCA_EXE.parent / "resources" / "profiles" / "BBL"
MACHINE = "Bambu Lab P2S 0.4 nozzle.json"
PROCESS = "0.20mm Standard @BBL P2S.json"
FILAMENT = "Bambu PLA Basic @BBL P2S.json"


@dataclass
class SliceResult:
    part: str
    ok: bool
    detail: str
    gcode: Path | None = None
    minutes: float | None = None
    layers: int | None = None
    filament_cm3: float | None = None


def orca_available() -> bool:
    return ORCA_EXE.is_file()


def _parse_duration(text: str) -> float:
    minutes = 0.0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text):
        minutes += int(value) * {"d": 1440, "h": 60, "m": 1, "s": 1 / 60}[unit]
    return round(minutes, 1)


def _parse_stats(result: SliceResult) -> None:
    head = result.gcode.read_text(encoding="utf-8", errors="replace")[:20000]
    if m := re.search(r"total estimated time: ([^;\n]+)", head):
        result.minutes = _parse_duration(m.group(1))
    if m := re.search(r"total layer number: (\d+)", head):
        result.layers = int(m.group(1))
    if m := re.search(r"filament used \[cm3\] = ([\d.]+)", head):
        result.filament_cm3 = float(m.group(1))


def slice_stl(stl: Path, out_dir: Path, part: str, timeout: float = 300.0) -> SliceResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = f"{PROFILE_DIR / 'machine' / MACHINE};{PROFILE_DIR / 'process' / PROCESS}"
    cmd = [
        str(ORCA_EXE),
        "--load-settings", settings,
        "--load-filaments", str(PROFILE_DIR / "filament" / FILAMENT),
        "--slice", "0",
        "--export-3mf", "sliced.3mf",
        "--outputdir", str(out_dir),
        str(stl),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return SliceResult(part, False, f"slicer timed out after {timeout}s")

    gcode = out_dir / "plate_1.gcode"
    if proc.returncode != 0 or not gcode.is_file():
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        return SliceResult(part, False, f"slicer exit {proc.returncode}, no gcode: {tail}")

    result = SliceResult(part, True, "sliced", gcode=gcode)
    _parse_stats(result)
    return result


def slice_workdir(workdir: Path, out_dir: Path | None = None) -> list[SliceResult]:
    """Slice body.step and lid.step from a build workdir, each on its own plate."""
    workdir = Path(workdir)
    out_dir = Path(out_dir) if out_dir else workdir / "slice"
    results = []
    for filename, part in ((BODY_STEP, "body"), (LID_STEP, "lid")):
        path = workdir / filename
        if not path.is_file():
            results.append(SliceResult(part, False, f"missing {filename}"))
            continue
        solids = cq.importers.importStep(str(path)).solids().vals()
        if not solids:
            results.append(SliceResult(part, False, f"no solid in {filename}"))
            continue
        shape = solids[0]
        bb = shape.BoundingBox()
        dropped = shape.translate(Vector(-bb.xmin, -bb.ymin, -bb.zmin))
        part_dir = out_dir / part
        part_dir.mkdir(parents=True, exist_ok=True)
        stl = part_dir / f"{part}.stl"
        cq.exporters.export(dropped, str(stl))
        results.append(slice_stl(stl, part_dir, part))
    return results
