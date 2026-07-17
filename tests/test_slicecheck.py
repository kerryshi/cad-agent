"""Slice-check tests. Real slicer runs — skipped loudly if OrcaSlicer is absent."""

import pytest

from tests.test_verify import make_spec
from toolchain.reference import build
from toolchain.slicecheck import orca_available, slice_stl, slice_workdir

pytestmark = pytest.mark.skipif(
    not orca_available(), reason="OrcaSlicer portable not installed (see STATUS.md)"
)


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
