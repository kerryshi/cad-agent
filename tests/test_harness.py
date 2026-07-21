"""Harness tests — loop, runner gates, extraction — all on the scripted backend.

Refuse-first: the runner's gates (toolchain reference, timeout, crash) are each
fed input they must refuse. The loop's success path runs a known-good generated
script through the real sandbox + verify.
"""

import json

import pydantic
import pytest

from harness.backends.scripted import ScriptedBackend
from harness.golden import approx_equal, load_tasks
from harness.loop import extract_spec, generate_part
from harness.runner import run_codegen
from toolchain.spec import EnclosureSpec

from pathlib import Path

GOLDEN = Path(__file__).parent.parent / "golden" / "tasks.json"


def minimal_spec() -> EnclosureSpec:
    return EnclosureSpec(length=40, width=40, height=20)


# A correct "generated" script for specs without standoffs/cutouts — what a
# cooperative codegen model should produce. Bare cadquery + stdlib only.
GOOD_SCRIPT = """
import json
import cadquery as cq
from cadquery import Solid, Vector

s = json.load(open("spec.json"))
L, W, H = s["length"], s["width"], s["height"]
wall = s.get("wall", 2.0); floor_t = s.get("floor", 2.0)
lid_t = s.get("lid_thickness", 2.4); c = s.get("fit_clearance", 0.2)
pd = s.get("post_diameter", 7.0); depth = s.get("screw_depth", 8.0)
screw = s.get("screw", "M3")
pilot = {"M2": 1.6, "M2.5": 2.05, "M3": 2.5, "M4": 3.3}[screw]
clear = {"M2": 2.4, "M2.5": 2.9, "M3": 3.4, "M4": 4.5}[screw]

def box(x0, y0, z0, x1, y1, z1):
    return Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, Vector(x0, y0, z0))

post_top = H - lid_t
inset = wall + pd / 2
centers = [(inset, inset), (L - inset, inset), (L - inset, W - inset), (inset, W - inset)]

body = box(0, 0, 0, L, W, H).cut(box(wall, wall, floor_t, L - wall, W - wall, H + 1))
for px, py in centers:
    body = body.fuse(Solid.makeCylinder(pd / 2, post_top - floor_t, Vector(px, py, floor_t), Vector(0, 0, 1)))
    body = body.cut(Solid.makeCylinder(pilot / 2, depth, Vector(px, py, post_top - depth), Vector(0, 0, 1)))
cq.exporters.export(body, "body.step")

lw, ld = L - 2 * wall - 2 * c, W - 2 * wall - 2 * c
x0, y0 = wall + c, wall + c
lid = box(x0, y0, post_top, x0 + lw, y0 + ld, H)
for px, py in centers:
    lid = lid.cut(Solid.makeCylinder(clear / 2, lid_t + 2, Vector(px, py, post_top - 1), Vector(0, 0, 1)))
cq.exporters.export(lid, "lid.step")
"""

CRASHING_SCRIPT = "import sys\nsys.exit(3)\n"
CHEATING_SCRIPT = "from toolchain.reference import build\n"
HANGING_SCRIPT = "while True:\n    pass\n"

# A correct "generated" script for the frame family — bare cadquery + stdlib.
FRAME_GOOD_SCRIPT = """
import json, math
import cadquery as cq
from cadquery import Solid, Vector

s = json.load(open("spec.json"))
wb = s["wheelbase"]; t = s.get("plate_thickness", 4.0)
aw = s.get("arm_width", 12.0); b = s.get("body_width", 36.0)
fc = s.get("fc_mount", "30.5x30.5-M3"); mm = s.get("motor_mount", "16x16-M3")
hub = s.get("hub_hole_diameter", 8.0)
size = {"30.5x30.5-M3": 30.5, "25.5x25.5-M2": 25.5, "20x20-M2": 20.0,
        "16x16-M3": 16.0, "19x19-M3": 19.0, "12x12-M2": 12.0, "9x9-M2": 9.0}
clear = {"M3": 3.4, "M2": 2.4}
fc_d = clear[fc.split("-")[1]]; mm_d = clear[mm.split("-")[1]]
R = wb / 2; pad = size[mm] * math.sqrt(2) + 8

frame = Solid.makeBox(b, b, t, Vector(-b / 2, -b / 2, 0))
angles = [45, 135, 225, 315]
centers = [(R * math.cos(math.radians(a)), R * math.sin(math.radians(a))) for a in angles]
for a, (cx, cy) in zip(angles, centers):
    arm = Solid.makeBox(R, aw, t, Vector(0, -aw / 2, 0)).rotate(Vector(0, 0, 0), Vector(0, 0, 1), a)
    frame = frame.fuse(arm).fuse(Solid.makeCylinder(pad / 2, t, Vector(cx, cy, 0), Vector(0, 0, 1)))

def drill(x, y, d):
    global frame
    frame = frame.cut(Solid.makeCylinder(d / 2, t + 2, Vector(x, y, -1), Vector(0, 0, 1)))

p = size[fc] / 2
for x, y in ((p, p), (p, -p), (-p, p), (-p, -p)):
    drill(x, y, fc_d)
sh = size[mm] / 2
for a, (cx, cy) in zip(angles, centers):
    drill(cx, cy, hub)
    ar = math.radians(a); ca, sa = math.cos(ar), math.sin(ar)
    for u, v in ((sh, sh), (sh, -sh), (-sh, sh), (-sh, -sh)):
        drill(cx + u * ca - v * sa, cy + u * sa + v * ca, mm_d)
cq.exporters.export(frame, "frame.step")
"""


# ---- runner gates (each must REFUSE) ----

def test_runner_rejects_toolchain_reference(tmp_path):
    r = run_codegen(CHEATING_SCRIPT, minimal_spec(), tmp_path)
    assert not r.ok and "toolchain" in r.detail


def test_runner_reports_crash(tmp_path):
    r = run_codegen(CRASHING_SCRIPT, minimal_spec(), tmp_path)
    assert not r.ok and "exited 3" in r.detail


def test_runner_times_out(tmp_path):
    r = run_codegen(HANGING_SCRIPT, minimal_spec(), tmp_path, timeout=3)
    assert not r.ok and "timed out" in r.detail


# ---- loop ----

def test_loop_success_first_try(tmp_path):
    backend = ScriptedBackend([GOOD_SCRIPT])
    result = generate_part(backend, minimal_spec(), tmp_path)
    assert result.ok, result.detail
    assert result.iterations == 1


def test_loop_iterates_on_failure_then_succeeds(tmp_path):
    backend = ScriptedBackend([CRASHING_SCRIPT, GOOD_SCRIPT])
    result = generate_part(backend, minimal_spec(), tmp_path)
    assert result.ok
    assert result.iterations == 2
    # the second prompt must carry failure feedback
    assert "FAILED" in backend.calls[1][1]


def test_loop_frame_family(tmp_path):
    from tests.test_frame import make_frame_spec
    from toolchain.families import get_family

    backend = ScriptedBackend([FRAME_GOOD_SCRIPT])
    result = generate_part(backend, make_frame_spec(), tmp_path, get_family("frame"))
    assert result.ok, result.detail
    assert result.iterations == 1


def test_loop_gives_up_at_cap(tmp_path):
    backend = ScriptedBackend([CRASHING_SCRIPT])
    result = generate_part(backend, minimal_spec(), tmp_path, max_iterations=2)
    assert not result.ok
    assert result.iterations == 2


# ---- extraction ----

def test_extract_passes_schema_to_backend():
    # constrained decoding: the extract stage offers the spec's JSON schema to
    # the backend; codegen never does (shape-only guarantee, extraction only)
    backend = ScriptedBackend(['{"length": 40, "width": 40, "height": 20}'])
    r = extract_spec(backend, "a 40x40x20 box")
    assert r.ok
    assert backend.schemas == [EnclosureSpec.model_json_schema()]


def test_codegen_passes_no_schema(tmp_path):
    backend = ScriptedBackend([GOOD_SCRIPT])
    result = generate_part(backend, minimal_spec(), tmp_path)
    assert result.ok, result.detail
    assert backend.schemas == [None]


def test_extract_valid_json():
    backend = ScriptedBackend(['{"length": 40, "width": 40, "height": 20}'])
    r = extract_spec(backend, "a 40x40x20 box")
    assert r.ok and r.spec.length == 40 and r.attempts == 1


def test_extract_retries_then_succeeds():
    backend = ScriptedBackend(["not json at all", '{"length": 40, "width": 40, "height": 20}'])
    r = extract_spec(backend, "a 40x40x20 box")
    assert r.ok and r.attempts == 2


def test_extract_gives_up():
    backend = ScriptedBackend(["still not json"])
    r = extract_spec(backend, "a box")
    assert not r.ok


# ---- ollama constrained decoding ----

def test_ollama_payload_carries_schema(monkeypatch):
    """schema -> Ollama `format` + temp 0; no schema -> neither (codegen path)."""
    from harness.backends import ollama_backend

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"message": {"content": "{}"}}).encode()

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(ollama_backend.urllib.request, "urlopen", fake_urlopen)
    b = ollama_backend.OllamaBackend(model="m")

    b.complete("sys", "user")
    assert "format" not in captured["payload"]
    assert "options" not in captured["payload"]

    schema = EnclosureSpec.model_json_schema()
    b.complete("sys", "user", schema=schema)
    assert captured["payload"]["format"] == schema
    assert captured["payload"]["options"]["temperature"] == 0


# ---- golden hybrid (extract backend != codegen backend) ----

def test_golden_hybrid_uses_extract_backend(monkeypatch, tmp_path):
    import harness.golden as golden

    backends = {}

    def fake_get_backend(kind, model=None):
        name = f"{kind}:{model}" if model else kind
        backends[kind] = ScriptedBackend(
            ['{"length": 40, "width": 40, "height": 20}'], name=name)
        return backends[kind]

    monkeypatch.setattr(golden, "get_backend", fake_get_backend)
    monkeypatch.setattr(golden, "ROOT", tmp_path)  # results/out go to tmp

    rc = golden.main(["--backend", "claude-code", "--extract-backend", "ollama",
                      "--extract-model", "q", "--stage", "extract", "--limit", "1",
                      "--tasks", str(GOLDEN)])
    assert rc == 0
    assert backends["ollama"].calls, "extraction must run on the extract backend"
    assert not backends["claude-code"].calls, "codegen backend must sit idle in extract stage"

    out = json.loads(
        (tmp_path / "results" / "hybrid--ollama-q--claude-code.json").read_text())
    assert out["extract_backend"] == "ollama:q"
    assert out["backend"] == "claude-code"


def test_golden_hybrid_routes_codegen_to_codegen_backend(monkeypatch, tmp_path):
    # pins the full-stage routing: extract calls land on the extract backend,
    # codegen calls (and only those) on the codegen backend
    import harness.golden as golden

    task = {"id": "mini", "request": "a 40x40x20 box, all defaults",
            "expected": {"length": 40, "width": 40, "height": 20}}
    tasks_file = tmp_path / "mini_tasks.json"
    tasks_file.write_text(json.dumps([task]), encoding="utf-8")

    backends = {}

    def fake_get_backend(kind, model=None):
        responses = {"ollama": ['{"length": 40, "width": 40, "height": 20}'],
                     "claude-code": [GOOD_SCRIPT]}[kind]
        backends[kind] = ScriptedBackend(responses, name=kind)
        return backends[kind]

    monkeypatch.setattr(golden, "get_backend", fake_get_backend)
    monkeypatch.setattr(golden, "ROOT", tmp_path)

    rc = golden.main(["--backend", "claude-code", "--extract-backend", "ollama",
                      "--stage", "both", "--tasks", str(tasks_file)])
    assert rc == 0
    assert len(backends["ollama"].calls) == 1
    assert backends["ollama"].schemas == [EnclosureSpec.model_json_schema()]
    assert len(backends["claude-code"].calls) == 1
    assert "cadquery" in backends["claude-code"].calls[0][0]  # codegen system prompt
    assert backends["claude-code"].schemas == [None]


# ---- golden set integrity ----

def test_golden_tasks_load_and_validate():
    tasks = load_tasks(GOLDEN)
    assert len(tasks) == 8
    ids = [t["id"] for t in tasks]
    assert len(set(ids)) == len(ids)
    for t in tasks:
        assert t["request"].strip()


def test_canonical_dump_resolves_derived_standoff_od():
    # outer_diameter=None means "derive od()"; an extraction that states the
    # SAME derived value is the same part and must score MATCH — a different
    # value is a real miss and must still FAIL
    from copy import deepcopy

    from toolchain.families import get_family

    fam = get_family("enclosure")
    base = {"length": 90, "width": 90, "height": 15,
            "standoffs": [{"x": 20, "y": 20, "height": 4, "screw": "M2"}]}
    expected = fam.spec_cls.model_validate(base)

    explicit = deepcopy(base)
    explicit["standoffs"][0]["outer_diameter"] = expected.standoffs[0].od()
    same = fam.spec_cls.model_validate(explicit)
    assert approx_equal(fam.dump_canonical(same), fam.dump_canonical(expected))

    wrong = deepcopy(base)
    wrong["standoffs"][0]["outer_diameter"] = 7.5
    other = fam.spec_cls.model_validate(wrong)
    assert not approx_equal(fam.dump_canonical(other), fam.dump_canonical(expected))

    # 0 must refuse loudly, not silently fold into "derive" via `or` truthiness
    zero = deepcopy(base)
    zero["standoffs"][0]["outer_diameter"] = 0
    with pytest.raises(pydantic.ValidationError):
        fam.spec_cls.model_validate(zero)


def test_approx_equal_semantics():
    assert approx_equal({"a": [1.0, {"b": 2}]}, {"a": [1.0000000001, {"b": 2}]})
    assert not approx_equal({"a": 1}, {"a": 2})
    assert not approx_equal({"a": 1}, {"b": 1})
