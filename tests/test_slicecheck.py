"""Slice-check tests. Real slicer runs — skipped loudly if OrcaSlicer is absent."""

import re

import cadquery as cq
import pytest

from tests.test_verify import make_spec
from toolchain import profiles
from toolchain.reference import build
from toolchain.slicecheck import orca_available, slice_stl, slice_workdir

pytestmark = pytest.mark.skipif(
    not orca_available(), reason="OrcaSlicer portable not installed (see STATUS.md)"
)


@pytest.fixture
def cube_stl(tmp_path):
    """Smallest thing that still produces a real multi-layer slice."""
    stl = tmp_path / "cube.stl"
    cq.exporters.export(cq.Workplane("XY").box(10, 10, 2), str(stl))
    return stl


def test_slice_workdir_both_parts(tmp_path):
    build(make_spec(), tmp_path)
    results = slice_workdir(tmp_path)
    by_part = {r.part: r for r in results}
    assert set(by_part) == {"body", "lid"}
    for r in results:
        assert r.ok, f"{r.part}: {r.detail}"
        assert r.minutes and r.minutes > 1, f"{r.part}: implausible time {r.minutes}"
        assert r.layers and r.layers > 5
        assert r.gcode.stat().st_size > 10_000


def test_slice_refuses_garbage_stl(tmp_path):
    junk = tmp_path / "junk.stl"
    junk.write_text("this is not an stl", encoding="utf-8")
    r = slice_stl(junk, tmp_path / "out", "junk")
    assert not r.ok, f"slicer accepted garbage: {r.detail}"


def test_slice_missing_parts_refused(tmp_path):
    results = slice_workdir(tmp_path)
    assert all(not r.ok for r in results)


# --- thermal coherence -------------------------------------------------
# OrcaSlicer's CLI does NOT resolve `inherits` in profile JSONs: only keys
# literally present in the leaf file survive, every parent is dropped
# silently. That shipped a "PETG" slice carrying filament_type=PLA and a
# 35 C bed (staged prints/, 2026-07-17) — a first-layer adhesion failure
# that the oracle reported as ok=True with plausible time/layer stats.
# These tests own that defect.


def test_slice_gcode_matches_requested_filament(cube_stl, tmp_path):
    """The gcode's material must be the one we asked the slicer for."""
    r = slice_stl(cube_stl, tmp_path / "out", "cube")
    assert r.ok, r.detail
    want = profiles.expected_filament_type()
    assert r.filament_type == want, (
        f"asked for {want}, gcode says {r.filament_type} — profile "
        "inheritance was not resolved"
    )


def test_slice_bed_temp_matches_resolved_profile(cube_stl, tmp_path):
    """Bed temp must match the filament profile, not a slicer default.

    Expected value is derived from the resolved profile chain, not hardcoded:
    a magic number here would drift from the profile it is meant to police.
    """
    from toolchain.profiles import expected_bed_temps

    r = slice_stl(cube_stl, tmp_path / "out", "cube")
    assert r.ok, r.detail
    assert r.bed_temp_c == expected_bed_temps()[1], (
        f"bed {r.bed_temp_c} C vs profile's {expected_bed_temps()[1]} C — "
        "PETG will not adhere to a cold plate"
    )


def test_stage_print_produces_printable_bundle(tmp_path):
    """End of the local path to the printer: STEP in, SD-ready bundle out."""
    from toolchain import profiles
    from toolchain.slicecheck import stage_print

    step = tmp_path / "cube.step"
    cq.exporters.export(cq.Workplane("XY").box(10, 10, 2), str(step))
    dest = tmp_path / "staged"
    r = stage_print(step, dest, "cube")

    assert r.ok, r.detail
    assert (dest / "cube.gcode.3mf").is_file()
    assert (dest / "plate_1.gcode").is_file()
    assert not (dest / "_slice").exists(), "scratch dir left behind"
    assert r.filament_type == profiles.expected_filament_type()
    assert r.bed_temp_c == profiles.expected_bed_temps()[1]


def test_patched_3mf_carries_model_id(cube_stl, tmp_path):
    import re
    import zipfile

    from toolchain import profiles
    from toolchain.slicecheck import _patch_model_id

    r = slice_stl(cube_stl, tmp_path / "out", "cube")
    assert r.ok, r.detail
    src = tmp_path / "out" / "sliced.3mf"
    dest = tmp_path / "patched.3mf"
    _patch_model_id(src, dest)

    with zipfile.ZipFile(dest) as z:
        info = z.read("Metadata/slice_info.config").decode()
        assert z.read("Metadata/plate_1.gcode") == \
            zipfile.ZipFile(src).read("Metadata/plate_1.gcode"), \
            "gcode payload changed — the md5 sidecar would no longer match"
    got = re.search(r'printer_model_id" value="([^"]*)"', info).group(1)
    assert got == profiles.model_id() != "", f"model id not patched in: {got!r}"


def test_gate_refuses_cold_first_layer(cube_stl, tmp_path, monkeypatch):
    """A cold FIRST layer under a correct steady temp must still be refused.

    The adhesion failure this module exists to stop is a cold plate on layer
    one; a correct steady temperature afterwards makes the file look fine.
    Any reading that collapses the two setpoints into one (a max, or the
    first match) accepts it.

    Sliced with PLA Translucent because it is the stock profile whose initial
    (60) and steady (55) temps differ — under PETG the two are both 70 and
    Orca emits a single setpoint, so the defect cannot be expressed.
    """
    from toolchain import slicecheck

    monkeypatch.setattr(
        slicecheck, "FILAMENT", "Bambu PLA Translucent @BBL P2S 0.4 nozzle.json"
    )
    initial, steady = profiles.expected_bed_temps()
    assert initial != steady, "profile no longer expresses the defect"

    r = slicecheck.slice_stl(cube_stl, tmp_path / "out", "cube")
    assert r.ok, r.detail
    text = r.gcode.read_text(encoding="utf-8", errors="replace")
    # cool only the initial-layer block — everything before the steady
    # setpoint that Orca emits once the first layer is down
    head, sep, tail = text.partition("; set bed temperature")
    assert sep, "expected a steady-state bed command in the gcode"
    sabotaged = re.sub(rf"M1(40|90) S{initial}", r"M1\1 S35", head) + sep + tail
    r.gcode.write_text(sabotaged, encoding="utf-8")

    slicecheck._parse_stats(r)
    slicecheck.check_thermal(r)
    assert r.bed_temp_c == steady, "sabotage disturbed the steady setpoint"
    assert not r.ok, (
        f"gate accepted a {r.bed_temp_initial_c}C first layer under a "
        f"correct {r.bed_temp_c}C steady bed"
    )
    assert "first layer" in r.detail


@pytest.mark.parametrize(
    "filament",
    [
        "Bambu PETG Basic @BBL P2S 0.4 nozzle.json",
        # initial 60 / steady 55 — differing setpoints, the case a single
        # collapsed reading false-refuses
        "Bambu PLA Translucent @BBL P2S 0.4 nozzle.json",
    ],
)
def test_stock_profiles_are_not_false_refused(cube_stl, tmp_path, monkeypatch, filament):
    """Every stock profile reachable via CAD_AGENT_FILAMENT must slice clean."""
    from toolchain import slicecheck

    monkeypatch.setattr(slicecheck, "FILAMENT", filament)
    r = slicecheck.slice_stl(cube_stl, tmp_path / "out", "cube")
    assert r.ok, f"{filament}: {r.detail}"


@pytest.mark.parametrize(
    "pattern, replace, defect",
    [
        (r"; filament_type = PETG", "; filament_type = PLA", "filament_type"),
        # every bed command, not just one — a partial edit leaves the real
        # temperature visible in the setpoint the parser reads
        (r"M1(40|90) S70", r"M1\1 S35", "bed temp"),
    ],
)
def test_thermal_gate_refuses_sabotaged_gcode(cube_stl, tmp_path, pattern, replace, defect):
    """Refuse-first: each thermal defect must be refused by the check that owns it.

    Sabotage the gcode on disk and re-parse, so this exercises the parser and
    the gate together — the pair that let a 35 C PETG slice ship as green.
    """
    import re

    from toolchain.slicecheck import _parse_stats, check_thermal

    r = slice_stl(cube_stl, tmp_path / "out", "cube")
    assert r.ok, r.detail
    text = r.gcode.read_text(encoding="utf-8", errors="replace")
    sabotaged, n = re.subn(pattern, replace, text)
    assert n, f"sabotage pattern {pattern!r} matched nothing in the gcode"
    r.gcode.write_text(sabotaged, encoding="utf-8")

    _parse_stats(r)
    check_thermal(r)
    assert not r.ok, f"gate accepted a gcode with a bad {defect}"
    assert defect in r.detail
