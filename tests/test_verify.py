"""Refuse-first evidence for the verify gate.

The gate is only trustworthy if we watch it go red: each sabotaged build must
be REFUSED by the specific check that owns that defect, and the good build must
pass everything. A verify layer that never refused anything is decoration.
"""

import pydantic
import pytest

from toolchain.reference import build
from toolchain.spec import CircleCutout, EnclosureSpec, RectCutout, Screw, Standoff
from toolchain.verify import verify


def make_spec() -> EnclosureSpec:
    return EnclosureSpec(
        length=80, width=60, height=30,
        standoffs=[
            Standoff(x=25, y=20, height=6, screw=Screw.M2_5),
            Standoff(x=55, y=40, height=6, screw=Screw.M2_5),
        ],
        cutouts=[
            RectCutout(face="+x", center=30, cz=12, width=12, height=8),
            CircleCutout(face="-y", center=40, cz=15, diameter=8),
        ],
    )


def build_and_verify(tmp_path_factory, sabotage=None):
    spec = make_spec()
    out = tmp_path_factory.mktemp(sabotage or "good")
    build(spec, out, sabotage=sabotage)
    return verify(spec, out)


@pytest.fixture(scope="module")
def good_report(tmp_path_factory):
    return build_and_verify(tmp_path_factory)


def test_good_part_passes(good_report):
    assert good_report.ok, "good part refused:\n" + good_report.text()


def test_good_part_ran_all_check_families(good_report):
    names = {c.name for c in good_report.checks}
    for expected in (
        "body.valid", "body.watertight", "body.bbox",
        "wall.+x", "wall.-x", "wall.+y", "wall.-y", "floor",
        "standoff[0]", "standoff[1]", "cutout[0]", "cutout[1]",
        "lid.valid", "lid.watertight", "lid.bbox", "lid.screw_holes",
        "fit.interference",
    ):
        assert expected in names, f"check family {expected} never ran"


def test_thin_wall_refused(tmp_path_factory):
    report = build_and_verify(tmp_path_factory, sabotage="thin_wall")
    assert not report.ok
    failed = {c.name for c in report.failures()}
    assert any(n.startswith("wall.") for n in failed), f"wrong checks fired: {failed}"


def test_misplaced_standoff_refused(tmp_path_factory):
    report = build_and_verify(tmp_path_factory, sabotage="misplaced_standoff")
    failed = {c.name for c in report.failures()}
    assert "standoff[0]" in failed, f"wrong checks fired: {failed}"
    assert "standoff[1]" not in failed, "untouched standoff falsely refused"


def test_undersized_cutout_refused(tmp_path_factory):
    report = build_and_verify(tmp_path_factory, sabotage="undersized_cutout")
    failed = {c.name for c in report.failures()}
    assert "cutout[0]" in failed, f"wrong checks fired: {failed}"
    assert "cutout[1]" not in failed, "untouched cutout falsely refused"


def test_missing_output_refused(tmp_path):
    report = verify(make_spec(), tmp_path)
    assert not report.ok
    assert {c.name for c in report.failures()} >= {"body.load", "lid.load"}


def test_spec_rejects_impossible_geometry():
    with pytest.raises(pydantic.ValidationError):
        EnclosureSpec(length=80, width=60, height=30,
                      standoffs=[Standoff(x=1, y=1, height=6)])
    with pytest.raises(pydantic.ValidationError):
        EnclosureSpec(length=80, width=60, height=3.9)
