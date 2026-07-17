"""Refuse-first evidence for the drone-frame verify gate."""

import pydantic
import pytest

from toolchain.frame import FrameSpec
from toolchain.frame_reference import build
from toolchain.frame_verify import verify_frame


def make_frame_spec() -> FrameSpec:
    return FrameSpec(
        wheelbase=140, plate_thickness=4, arm_width=12, body_width=36,
        fc_mount="20x20-M2", motor_mount="12x12-M2", prop_size_inch=3,
    )


def build_and_verify(tmp_path_factory, sabotage=None):
    spec = make_frame_spec()
    out = tmp_path_factory.mktemp(sabotage or "good_frame")
    build(spec, out, sabotage=sabotage)
    return verify_frame(spec, out)


@pytest.fixture(scope="module")
def good_report(tmp_path_factory):
    return build_and_verify(tmp_path_factory)


def test_good_frame_passes(good_report):
    assert good_report.ok, "good frame refused:\n" + good_report.text()


def test_good_frame_ran_all_check_families(good_report):
    names = {c.name for c in good_report.checks}
    for expected in (
        "frame.load", "frame.valid", "frame.watertight", "frame.bbox",
        "plate.thickness", "arm[0]", "arm[3]", "motor[0]", "motor[3]", "fc_mount",
    ):
        assert expected in names, f"check family {expected} never ran"


def test_missing_arm_refused(tmp_path_factory):
    report = build_and_verify(tmp_path_factory, sabotage="missing_arm")
    failed = {c.name for c in report.failures()}
    assert "arm[0]" in failed, f"wrong checks fired: {failed}"
    assert "arm[1]" not in failed, "untouched arm falsely refused"


def test_misplaced_motor_hole_refused(tmp_path_factory):
    report = build_and_verify(tmp_path_factory, sabotage="misplaced_motor_hole")
    failed = {c.name for c in report.failures()}
    assert "motor[0]" in failed, f"wrong checks fired: {failed}"
    assert "motor[1]" not in failed, "untouched motor falsely refused"


def test_thin_plate_refused(tmp_path_factory):
    report = build_and_verify(tmp_path_factory, sabotage="thin_plate")
    failed = {c.name for c in report.failures()}
    assert "plate.thickness" in failed, f"wrong checks fired: {failed}"


def test_missing_output_refused(tmp_path):
    report = verify_frame(make_frame_spec(), tmp_path)
    assert not report.ok
    assert "frame.load" in {c.name for c in report.failures()}


def test_spec_rejects_impossible_frames():
    with pytest.raises(pydantic.ValidationError):  # 5" props on a 140mm frame
        FrameSpec(wheelbase=140, prop_size_inch=5)
    with pytest.raises(pydantic.ValidationError):  # FC pattern larger than body
        FrameSpec(wheelbase=220, body_width=30, fc_mount="30.5x30.5-M3")
    with pytest.raises(pydantic.ValidationError):  # pads reach into the body
        FrameSpec(wheelbase=70, body_width=36)
