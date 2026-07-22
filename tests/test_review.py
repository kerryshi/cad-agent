"""Review page + verdict + send gate — fail-first coverage.

The gate exists to make an unreviewed print mechanically impossible: every
refusal case (no verdict, rejected, stale hashes) is fed to it here and must
refuse via its own message; the approved path must pass. Page generation is
exercised over a real (reference-built, really rendered) frame build so the
image embedding is honest, not a fixture of fakes.
"""

import hashlib
import json
import shutil

import pytest

import harness.review as review
from toolchain.families import get_family
from toolchain.frame import FrameSpec
from toolchain.render import render_build

FAM = get_family("frame")


@pytest.fixture(scope="module")
def _template(tmp_path_factory):
    """One real staged frame build, copied per-test by build_dir."""
    dest = tmp_path_factory.mktemp("tmpl") / "t-frame"
    dest.mkdir()
    spec = FrameSpec(wheelbase=140, plate_thickness=4, arm_width=12,
                     body_width=36, fc_mount="20x20-M2", motor_mount="12x12-M2")
    FAM.build(spec, dest)
    render_build(dest, FAM)
    (dest / "frame").mkdir()
    (dest / "frame" / "frame.gcode.3mf").write_bytes(b"fake gcode payload")
    manifest = {
        "request": "a 140 mm micro quad frame",
        "family": "frame",
        "extract_backend": "ollama:test",
        "codegen_backend": "claude-code:test",
        "spec": spec.model_dump(mode="json"),
        "iterations": 1,
        "slices": [{"part": "frame", "minutes": 39.6, "layers": 20,
                    "cm3": 10.8}],
        "created": "2026-07-21 12:00:00",
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return dest


@pytest.fixture()
def build_dir(_template, tmp_path):
    dest = tmp_path / "t-frame"
    shutil.copytree(_template, dest)
    return dest


def _page(build_dir):
    return (build_dir / "review.html").read_text(encoding="utf-8")


def test_page_pending_embeds_renders_and_spec(build_dir):
    path = review.write_review_page(build_dir)
    assert path == build_dir / "review.html"
    page = _page(build_dir)
    assert "PENDING" in page
    assert "140" in page  # wheelbase from the spec echo
    assert "a 140 mm micro quad frame" in page
    # every render is embedded (self-contained page, no file references)
    n_renders = len(list((build_dir / "renders").glob("*.png")))
    assert n_renders >= 3
    assert page.count("data:image/png;base64,") == n_renders
    assert "harness.review" in page  # the approve/reject commands are shown


def test_approve_writes_hash_bound_verdict(build_dir):
    rc = review.main([str(build_dir), "--approve"])
    assert rc == 0
    rj = json.loads((build_dir / "review.json").read_text(encoding="utf-8"))
    assert rj["verdict"] == "approved"
    step_hash = hashlib.sha256(
        (build_dir / "frame.step").read_bytes()).hexdigest()
    assert rj["hashes"]["frame.step"] == step_hash
    assert "frame/frame.gcode.3mf" in rj["hashes"]
    assert "APPROVED" in _page(build_dir)  # page regenerated with the verdict


def test_reject_requires_comment(build_dir):
    rc = review.main([str(build_dir), "--reject"])
    assert rc == 2
    assert not (build_dir / "review.json").exists()
    rc = review.main([str(build_dir), "--reject", "--comment", "arms too thin"])
    assert rc == 0
    rj = json.loads((build_dir / "review.json").read_text(encoding="utf-8"))
    assert rj["verdict"] == "rejected" and rj["comment"] == "arms too thin"
    assert "REJECTED" in _page(build_dir)


def test_review_cli_refuses_non_build_dir(tmp_path):
    assert review.main([str(tmp_path)]) == 1  # no manifest.json


def test_gate_refuses_missing_verdict(build_dir):
    ok, why = review.send_gate(build_dir, FAM)
    assert not ok and "no verdict" in why


def test_gate_refuses_rejected(build_dir):
    review.main([str(build_dir), "--reject", "--comment", "wrong FC pattern"])
    ok, why = review.send_gate(build_dir, FAM)
    assert not ok and "REJECTED" in why


def test_gate_refuses_stale_approval(build_dir):
    review.main([str(build_dir), "--approve"])
    with open(build_dir / "frame" / "frame.gcode.3mf", "ab") as fh:
        fh.write(b"!")  # the artifact changed after the verdict
    ok, why = review.send_gate(build_dir, FAM)
    assert not ok and "STALE" in why


def test_gate_refuses_stale_on_new_artifact(build_dir):
    # approve a --no-slice build, slice afterwards: the gcode was never seen
    # by the reviewer, so the approval must not cover it
    shutil.rmtree(build_dir / "frame")
    review.main([str(build_dir), "--approve"])
    (build_dir / "frame").mkdir()
    (build_dir / "frame" / "frame.gcode.3mf").write_bytes(b"unseen gcode")
    ok, why = review.send_gate(build_dir, FAM)
    assert not ok and "STALE" in why


def test_gate_passes_approved(build_dir):
    review.main([str(build_dir), "--approve"])
    ok, why = review.send_gate(build_dir, FAM)
    assert ok, why
