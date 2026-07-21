"""make.py e2e tests — scripted backends, no live models, no slicer needed.

The sandbox + verify path runs for real (GOOD_SCRIPT through the runner);
the slice stage is pinned by monkeypatching stage_print so the tests are
Orca-free. Each refusal gate is fed input it must refuse.
"""

import json
from pathlib import Path

import harness.make as make
from harness.backends.scripted import ScriptedBackend
from tests.test_harness import CRASHING_SCRIPT, GOOD_SCRIPT

SPEC_JSON = '{"length": 40, "width": 40, "height": 20}'


def _patch_backends(monkeypatch, extract_responses, codegen_responses):
    made = {}

    def fake(kind, model=None):
        resp = extract_responses if kind == "ollama" else codegen_responses
        made[kind] = ScriptedBackend(list(resp), name=f"{kind}:test")
        return made[kind]

    monkeypatch.setattr(make, "get_backend", fake)
    return made


def test_make_happy_path_no_slice(monkeypatch, tmp_path):
    made = _patch_backends(monkeypatch, [SPEC_JSON], [GOOD_SCRIPT])
    rc = make.main(["a 40 box", "--name", "t", "--out", str(tmp_path), "--no-slice"])
    assert rc == 0
    dest = tmp_path / "t"
    assert (dest / "body.step").is_file() and (dest / "lid.step").is_file()
    m = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert m["spec"]["length"] == 40 and m["iterations"] == 1
    assert m["extract_backend"] == "ollama:test"
    assert m["slices"] == []
    assert made["ollama"].schemas[0] is not None  # constrained extraction
    assert made["claude-code"].schemas == [None]  # unconstrained codegen


def test_make_refuses_bad_extraction(monkeypatch, tmp_path):
    _patch_backends(monkeypatch, ["not json"], [GOOD_SCRIPT])
    rc = make.main(["a box", "--name", "t", "--out", str(tmp_path)])
    assert rc == 2
    assert not (tmp_path / "t" / "manifest.json").exists()


def test_make_refuses_failed_codegen(monkeypatch, tmp_path):
    _patch_backends(monkeypatch, [SPEC_JSON], [CRASHING_SCRIPT])
    rc = make.main(["a box", "--name", "t", "--out", str(tmp_path),
                    "--max-iterations", "1"])
    assert rc == 3
    assert not (tmp_path / "t" / "manifest.json").exists()


def test_make_refuses_existing_dest(monkeypatch, tmp_path):
    _patch_backends(monkeypatch, [SPEC_JSON], [GOOD_SCRIPT])
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "keep.txt").write_text("precious")
    rc = make.main(["a box", "--name", "t", "--out", str(tmp_path)])
    assert rc == 5
    assert (tmp_path / "t" / "keep.txt").read_text() == "precious"


def test_make_slice_stage_wiring(monkeypatch, tmp_path):
    from toolchain.slicecheck import SliceResult

    _patch_backends(monkeypatch, [SPEC_JSON], [GOOD_SCRIPT])
    staged = []

    def fake_stage(step, dest, part="part"):
        staged.append((Path(step).name, part))
        r = SliceResult(part, True, "ok")
        r.minutes, r.layers, r.filament_cm3 = 10.0, 5, 1.0
        return r

    monkeypatch.setattr(make, "stage_print", fake_stage)
    rc = make.main(["a box", "--name", "t", "--out", str(tmp_path)])
    assert rc == 0
    assert staged == [("body.step", "body"), ("lid.step", "lid")]
    m = json.loads((tmp_path / "t" / "manifest.json").read_text(encoding="utf-8"))
    assert [s["part"] for s in m["slices"]] == ["body", "lid"]
