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
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import cadquery as cq
from cadquery import Vector

from toolchain import profiles
from toolchain.spec import BODY_STEP, LID_STEP

ORCA_EXE = Path(os.environ.get(
    "CAD_AGENT_ORCA", r"C:\Users\PC\tools\OrcaSlicer\orca-slicer.exe"))
PROFILE_DIR = ORCA_EXE.parent / "resources" / "profiles" / "BBL"
MACHINE = "Bambu Lab P2S 0.4 nozzle.json"
PROCESS = "0.20mm Standard @BBL P2S.json"
# Kerry prints Bambu PETG Basic (2026-07-17); override per-run if needed
FILAMENT = os.environ.get(
    "CAD_AGENT_FILAMENT", "Bambu PETG Basic @BBL P2S 0.4 nozzle.json")


@dataclass
class SliceResult:
    part: str
    ok: bool
    detail: str
    gcode: Path | None = None
    minutes: float | None = None
    layers: int | None = None
    filament_cm3: float | None = None
    filament_type: str | None = None
    bed_temp_c: int | None = None  # steady state
    bed_temp_initial_c: int | None = None  # first layer
    nozzle_temp_c: int | None = None  # peak
    warnings: list[str] = field(default_factory=list)  # slicer's own channel


def orca_available() -> bool:
    return ORCA_EXE.is_file()


def _parse_duration(text: str) -> float:
    minutes = 0.0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text):
        minutes += int(value) * {"d": 1440, "h": 60, "m": 1, "s": 1 / 60}[unit]
    return round(minutes, 1)


def _parse_stats(result: SliceResult) -> None:
    # time/layers sit at the top of the gcode; filament totals at the bottom
    text = result.gcode.read_text(encoding="utf-8", errors="replace")
    head = text[:20000] + text[-40000:]
    if m := re.search(r"total estimated time: ([^;\n]+)", head):
        result.minutes = _parse_duration(m.group(1))
    if m := re.search(r"total layer number: (\d+)", head):
        result.layers = int(m.group(1))
    if m := re.search(r"filament used \[cm3\] = ([\d.]+)", head):
        result.filament_cm3 = float(m.group(1))
    if m := re.search(r"^; filament_type = (\w+)", text, re.M):
        result.filament_type = m.group(1)
    # Bed temps from the commands the printer executes, not the config echo.
    # The heat commands sit indented inside the start-gcode block, so the
    # patterns cannot be anchored hard to column 0. Orca emits the initial
    # layer setpoint first, then the steady one, then `M140 S0` to shut the
    # bed off at the end; drop that shutdown and keep the two real setpoints
    # SEPARATELY. Collapsing them (a max, or the first match) either hides a
    # cold first layer or false-refuses profiles whose two values differ.
    # NOTE: the `M190 R<temp>` wait-while-cooling form is deliberately not
    # matched — it yields None, which the gate refuses. Fails closed; do not
    # "fix" it into failing open.
    body = text.rsplit("CONFIG_BLOCK_END", 1)[-1]
    setpoints = [int(s) for s in re.findall(r"^\s*M1(?:40|90) S(\d+)", body, re.M)]
    while setpoints and setpoints[-1] == 0:  # trailing bed-off
        setpoints.pop()
    result.bed_temp_initial_c = setpoints[0] if setpoints else None
    result.bed_temp_c = setpoints[-1] if setpoints else None
    nozzle = [int(s) for s in re.findall(r"^\s*M10[49] S(\d+)", body, re.M)]
    result.nozzle_temp_c = max(nozzle) if nozzle else None


def check_thermal(result: SliceResult) -> None:
    """Refuse a slice whose gcode contradicts the filament we asked for.

    The oracle's other stats (time, layers, volume) are plausible whether or
    not the profile resolved, so they cannot catch a wrong-material slice.
    Both expectations come from the resolved profile chain — see
    toolchain/profiles.py for the defect this owns.
    """
    if not result.ok:
        return
    want_type = profiles.expected_filament_type()
    want_initial, want_steady = profiles.expected_bed_temps()
    want_nozzle = profiles.expected_nozzle_temp()

    def refuse(detail: str) -> None:
        result.ok = False
        result.detail = detail

    if result.filament_type != want_type:
        return refuse(
            f"filament_type mismatch: gcode says {result.filament_type}, "
            f"profile resolves to {want_type}"
        )
    if result.bed_temp_initial_c != want_initial:
        return refuse(
            f"bed temp (first layer) {result.bed_temp_initial_c}C, profile "
            f"specifies {want_initial}C for the {profiles.bed_type()}"
        )
    if result.bed_temp_c != want_steady:
        return refuse(
            f"bed temp {result.bed_temp_c}C, profile specifies {want_steady}C "
            f"for the {profiles.bed_type()}"
        )
    if result.nozzle_temp_c != want_nozzle:
        return refuse(
            f"nozzle temp {result.nozzle_temp_c}C, profile specifies "
            f"{want_nozzle}C"
        )


def slice_stl(stl: Path, out_dir: Path, part: str, timeout: float = 300.0) -> SliceResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Flatten the inheritance chains first — this build's CLI cannot resolve
    # `inherits` (it looks in *_full/ dirs that ship only with the full
    # install), so a raw profile path silently loses every parent key.
    flat = out_dir / "_profiles"
    machine = profiles.write_flat("machine", MACHINE, flat / "machine.json")
    process = profiles.write_flat(
        "process", PROCESS, flat / "process.json",
        extra={"curr_bed_type": profiles.bed_type()},
    )
    filament = profiles.write_flat("filament", FILAMENT, flat / "filament.json")
    cmd = [
        str(ORCA_EXE),
        "--load-settings", f"{machine};{process}",
        "--load-filaments", str(filament),
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
    result.warnings = _slicer_warnings(out_dir / "sliced.3mf")
    _parse_stats(result)
    check_thermal(result)
    return result


# Warning the vendor profiles emit for every filament on this machine: the
# plate runs above the material's `temperature_vitrification` (PETG 70 > 60,
# PLA 60 > 45). Verified inherent, not induced by the flattening — so it is
# surfaced, not gated. Anything NOT on this list is worth a human look.
_INHERENT_WARNINGS = frozenset({"bed_temperature_too_high_than_filament"})


def _slicer_warnings(sliced_3mf: Path) -> list[str]:
    """The slicer's own warning channel, which nothing was reading.

    Advisory only — Orca refuses hard errors with a non-zero exit — but a new
    warning appearing here is the cheapest signal that a slice changed shape.
    """
    if not sliced_3mf.is_file():
        return []
    try:
        with zipfile.ZipFile(sliced_3mf) as z:
            info = z.read("Metadata/slice_info.config").decode("utf-8")
    except (KeyError, zipfile.BadZipFile):
        return []
    return [w for w in re.findall(r'<warning msg="([^"]+)"', info)
            if w not in _INHERENT_WARNINGS]


def _patch_model_id(src_3mf: Path, dest_3mf: Path) -> None:
    """Copy a sliced 3mf, filling in the `printer_model_id` the CLI leaves empty.

    Only Metadata/slice_info.config is rewritten; the gcode payload is copied
    byte-for-byte so the sidecar plate_1.gcode.md5 stays valid.
    """
    want = profiles.model_id()
    with zipfile.ZipFile(src_3mf) as zin:
        entries = [(i, zin.read(i.filename)) for i in zin.infolist()]
    patched = False
    for idx, (info, data) in enumerate(entries):
        if info.filename != "Metadata/slice_info.config":
            continue
        text = data.decode("utf-8")
        # lambda replacement: `want` is data, and a backslash in it would be
        # read as a group reference in a template string
        new, n = re.subn(
            r'(<metadata key="printer_model_id" value=")"',
            lambda m: f'{m.group(1)}{want}"', text, count=1,
        )
        if not n:
            raise ValueError(
                f"printer_model_id not empty/absent in {src_3mf} — refusing to "
                "guess; inspect slice_info.config before staging"
            )
        entries[idx] = (info, new.encode("utf-8"))
        patched = True
    if not patched:
        raise ValueError(f"no slice_info.config in {src_3mf}")
    with zipfile.ZipFile(dest_3mf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in entries:
            zout.writestr(info, data)


def stage_print(step: Path, dest: Path, part: str = "part") -> SliceResult:
    """Slice one STEP and stage a printable .3mf + gcode into `dest`.

    The end of the local path to the printer: copy `dest` to the microSD and
    print from the touchscreen. Refuses to stage anything the thermal gate
    rejects — a staged file is meant to be printed unattended-ish, so it must
    not be the thing that quietly wastes a plate.
    """
    step, dest = Path(step), Path(dest)
    work = dest / "_slice"
    solids = cq.importers.importStep(str(step)).solids().vals()
    if not solids:
        return SliceResult(part, False, f"no solid in {step}")
    bb = solids[0].BoundingBox()
    dropped = solids[0].translate(Vector(-bb.xmin, -bb.ymin, -bb.zmin))
    work.mkdir(parents=True, exist_ok=True)
    stl = work / f"{part}.stl"
    cq.exporters.export(dropped, str(stl))

    result = slice_stl(stl, work, part)
    if not result.ok:
        # don't leave a full slice tree inside a user-facing staging dir
        shutil.rmtree(work, ignore_errors=True)
        return result
    dest.mkdir(parents=True, exist_ok=True)
    _patch_model_id(work / "sliced.3mf", dest / f"{part}.gcode.3mf")
    shutil.copy2(result.gcode, dest / "plate_1.gcode")
    shutil.rmtree(work, ignore_errors=True)
    result.gcode = dest / "plate_1.gcode"
    return result


DEFAULT_PARTS = ((BODY_STEP, "body"), (LID_STEP, "lid"))


def slice_workdir(workdir: Path, out_dir: Path | None = None,
                  parts: tuple = DEFAULT_PARTS) -> list[SliceResult]:
    """Slice each part STEP from a build workdir, each on its own plate.

    `parts` is (filename, partname) pairs — defaults to the enclosure contract;
    pass ((FRAME_STEP, "frame"),) for the frame family.
    """
    workdir = Path(workdir)
    out_dir = Path(out_dir) if out_dir else workdir / "slice"
    results = []
    for filename, part in parts:
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


if __name__ == "__main__":  # stage a printable file: python -m toolchain.slicecheck
    import argparse
    import sys

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("step", type=Path, help="part STEP to slice")
    ap.add_argument("dest", type=Path, help="directory to stage the .gcode.3mf into")
    ap.add_argument("--part", default="part", help="part name (file stem)")
    args = ap.parse_args()

    r = stage_print(args.step, args.dest, args.part)
    status = "OK " if r.ok else "REFUSED"
    print(f"{status} {r.part}: {r.detail}")
    if r.ok:
        print(f"  {r.filament_type} @ {r.bed_temp_c}C bed ({profiles.bed_type()})")
        print(f"  {r.minutes} min, {r.layers} layers, {r.filament_cm3} cm3")
        if r.warnings:
            print(f"  slicer warnings: {', '.join(r.warnings)}")
        print(f"  staged -> {args.dest / (args.part + '.gcode.3mf')}")
    sys.exit(0 if r.ok else 1)
