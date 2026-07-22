"""make.py + send.py e2e tests — scripted backends, no live models, no slicer.

The sandbox + verify path runs for real (GOOD_SCRIPT through the runner);
the slice stage is pinned by monkeypatching stage_print so the tests are
Orca-free. Each refusal gate is fed input it must refuse. The send stack
(printer, FTPS, preflight) is faked at the harness.send module boundary.
"""

import json
import types
from pathlib import Path

import harness.make as make
import harness.review as hreview
import harness.send as hsend
from harness.backends.scripted import ScriptedBackend
from tests.test_harness import CRASHING_SCRIPT, FRAME_GOOD_SCRIPT, GOOD_SCRIPT

SPEC_JSON = '{"length": 40, "width": 40, "height": 20}'
FRAME_SPEC_JSON = ('{"wheelbase": 140, "plate_thickness": 4, "arm_width": 12,'
                   ' "body_width": 36, "fc_mount": "20x20-M2",'
                   ' "motor_mount": "12x12-M2"}')


def _patch_backends(monkeypatch, extract_responses, codegen_responses):
    made = {}

    def fake(kind, model=None):
        resp = extract_responses if kind == "ollama" else codegen_responses
        made[kind] = ScriptedBackend(list(resp), name=f"{kind}:test")
        return made[kind]

    monkeypatch.setattr(make, "get_backend", fake)
    return made


def _patch_stage(monkeypatch):
    from toolchain.slicecheck import SliceResult

    staged = []

    def fake_stage(step, dest, part="part"):
        staged.append((Path(step).name, part))
        gcode_dir = Path(dest)
        gcode_dir.mkdir(parents=True, exist_ok=True)
        (gcode_dir / f"{part}.gcode.3mf").write_bytes(b"fake gcode " + part.encode())
        r = SliceResult(part, True, "ok")
        r.minutes, r.layers, r.filament_cm3 = 10.0, 5, 1.0
        return r

    monkeypatch.setattr(make, "stage_print", fake_stage)
    return staged


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
    # the review surface is part of every build
    renders = list((dest / "renders").glob("*.png"))
    assert renders, "no renders staged"
    page = (dest / "review.html").read_text(encoding="utf-8")
    assert "PENDING" in page
    assert page.count("data:image/png;base64,") == len(renders)


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


def test_make_send_refuses_pending_review(monkeypatch, tmp_path):
    # --send may not start a print the reviewer never saw: the build must be
    # fully staged (reviewable), then refused at the review gate
    _patch_backends(monkeypatch, [FRAME_SPEC_JSON], [FRAME_GOOD_SCRIPT])
    _patch_stage(monkeypatch)
    rc = make.main(["a frame", "--family", "frame", "--name", "t",
                    "--out", str(tmp_path), "--send", "--plate-clear"])
    assert rc == 10
    dest = tmp_path / "t"
    assert (dest / "frame.step").is_file(), "build must survive the refusal"
    assert (dest / "review.html").is_file(), "the thing to review must exist"


def test_send_refuses_ambiguous_part(monkeypatch, tmp_path):
    # fires BEFORE any model spend - a wrong flag must not cost a codegen run
    _patch_backends(monkeypatch, [SPEC_JSON], [GOOD_SCRIPT])
    rc = make.main(["a box", "--name", "t", "--out", str(tmp_path),
                    "--send", "--plate-clear"])  # enclosure = body AND lid
    assert rc == 6
    assert not (tmp_path / "t").exists(), "refused before any work"


class _FakeSendPrinter:
    """Printer whose reported state is scripted per pushall cycle."""

    instances: list = []

    def __init__(self, ip, code, serial):
        self.started = []
        self.states = [("RUNNING", None)]  # (gcode_state, subtask override)
        self._state_i = -1
        _FakeSendPrinter.instances.append(self)
        outer = self

        class _MQ:
            def pushall(self):
                outer._state_i = min(outer._state_i + 1, len(outer.states) - 1)

            def start_print_3mf(self, filename, plate, use_ams=True, **kw):
                outer.started.append((filename, plate, use_ams))

        self.mqtt_client = _MQ()

    def connect(self):
        pass

    def disconnect(self):
        pass

    def mqtt_client_ready(self):
        return True

    def mqtt_dump(self):
        state, task = self.states[max(self._state_i, 0)]
        return {"print": {"gcode_state": state,
                          "subtask_name": task if task is not None else "t"}}


class _FakeFTPS:
    stored: list = []

    def __init__(self, ip, code):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def storbinary(self, cmd, fh):
        _FakeFTPS.stored.append((cmd, len(fh.read())))

    def size(self, path):
        return _FakeFTPS.stored[-1][1]  # byte-exact echo


def _patch_send_stack(monkeypatch, preflight_ok=True):
    _FakeSendPrinter.instances = []
    _FakeFTPS.stored = []
    monkeypatch.setattr(hsend, "_bl", types.SimpleNamespace(Printer=_FakeSendPrinter))
    monkeypatch.setattr(hsend, "PrinterFTPS", _FakeFTPS)
    monkeypatch.setattr(hsend, "load_credentials", lambda: ("ip", "code", "serial"))
    monkeypatch.setattr(hsend, "START_POLL_S", 0.0)

    class _Pre:
        ok = preflight_ok

        def report(self):
            return "  (faked preflight)"

    monkeypatch.setattr(
        hsend, "_preflight", types.SimpleNamespace(check=lambda *a, **k: _Pre()))
    monkeypatch.setattr(hsend, "_profiles", types.SimpleNamespace(
        expected_filament_type=lambda: "PETG", expected_nozzle_temp=lambda: 250))
    monkeypatch.setattr(hsend, "time", types.SimpleNamespace(
        sleep=lambda s: None, monotonic=time_counter()))


def time_counter():
    t = [0.0]

    def tick():
        t[0] += 5.0
        return t[0]

    return tick


def _staged_frame(tmp_path):
    """A staged frame build laid out as make leaves it (no renders needed)."""
    dest = tmp_path / "t"
    (dest / "frame").mkdir(parents=True)
    (dest / "frame.step").write_bytes(b"step bytes")
    (dest / "frame" / "frame.gcode.3mf").write_bytes(b"gcode!")
    (dest / "manifest.json").write_text(json.dumps({
        "request": "a frame", "family": "frame", "spec": {"wheelbase": 140},
        "iterations": 1, "slices": [], "extract_backend": "x",
        "codegen_backend": "y", "created": "2026-07-21 12:00:00"}),
        encoding="utf-8")
    return dest


def test_send_happy_path_frame(monkeypatch, tmp_path):
    dest = _staged_frame(tmp_path)
    _patch_send_stack(monkeypatch)
    rc = hsend.send_print(dest, "frame")
    assert rc == 0
    printer = _FakeSendPrinter.instances[0]
    assert printer.started == [("t.gcode.3mf", 1, False)], "use_ams must be False"
    assert _FakeFTPS.stored[0][0] == "STOR /t.gcode.3mf"


def test_send_refuses_failed_preflight(monkeypatch, tmp_path):
    dest = _staged_frame(tmp_path)
    _patch_send_stack(monkeypatch, preflight_ok=False)
    rc = hsend.send_print(dest, "frame")
    assert rc == 7
    assert not _FakeFTPS.stored, "nothing uploaded after a preflight refusal"


def test_send_refuses_unconfirmed_start(monkeypatch, tmp_path):
    dest = _staged_frame(tmp_path)
    _patch_send_stack(monkeypatch)
    _FakeSendPrinter.instances = []
    orig_init = _FakeSendPrinter.__init__

    def stuck_init(self, *a):
        orig_init(self, *a)
        self.states = [("FINISH", None)]  # never reaches RUNNING

    monkeypatch.setattr(_FakeSendPrinter, "__init__", stuck_init)
    rc = hsend.send_print(dest, "frame")
    assert rc == 9


def test_send_cli_refuses_unreviewed(monkeypatch, tmp_path):
    dest = _staged_frame(tmp_path)
    _patch_send_stack(monkeypatch)
    rc = hsend.main([str(dest), "--plate-clear"])
    assert rc == 10
    assert not _FakeFTPS.stored, "no printer contact without an approval"
    assert not _FakeSendPrinter.instances


def test_send_cli_refuses_without_plate_clear(monkeypatch, tmp_path):
    dest = _staged_frame(tmp_path)
    assert hreview.main([str(dest), "--approve"]) == 0
    _patch_send_stack(monkeypatch)
    rc = hsend.main([str(dest)])
    assert rc == 6
    assert not _FakeFTPS.stored


def test_send_cli_refuses_stale_approval(monkeypatch, tmp_path):
    dest = _staged_frame(tmp_path)
    assert hreview.main([str(dest), "--approve"]) == 0
    (dest / "frame" / "frame.gcode.3mf").write_bytes(b"rebuilt gcode")
    _patch_send_stack(monkeypatch)
    rc = hsend.main([str(dest), "--plate-clear"])
    assert rc == 10
    assert not _FakeFTPS.stored


def test_send_cli_happy_after_approval(monkeypatch, tmp_path):
    dest = _staged_frame(tmp_path)
    assert hreview.main([str(dest), "--approve"]) == 0
    _patch_send_stack(monkeypatch)
    rc = hsend.main([str(dest), "--plate-clear"])
    assert rc == 0
    printer = _FakeSendPrinter.instances[0]
    assert printer.started == [("t.gcode.3mf", 1, False)]


def test_make_slice_stage_wiring(monkeypatch, tmp_path):
    _patch_backends(monkeypatch, [SPEC_JSON], [GOOD_SCRIPT])
    staged = _patch_stage(monkeypatch)
    rc = make.main(["a box", "--name", "t", "--out", str(tmp_path)])
    assert rc == 0
    assert staged == [("body.step", "body"), ("lid.step", "lid")]
    m = json.loads((tmp_path / "t" / "manifest.json").read_text(encoding="utf-8"))
    assert [s["part"] for s in m["slices"]] == ["body", "lid"]
