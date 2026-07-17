"""Deterministic verification of a built enclosure against its frozen spec.

Input contract: the spec (JSON or object) plus the two STEP files the builder
wrote (`body.step`, `lid.step`, assembled coordinates — see spec.py). The
builder — reference or agent — never writes these checks; they derive from the
spec alone, so the actor is not its own verifier.

Technique notes (why not naive B-Rep queries):
  * Wall/floor thickness: OCCT has no cheap min-thickness query. Because the
    spec pins where material must be, we classify probe points at known depths
    inside each wall (`BRepClass3d_SolidClassifier`) instead of measuring.
  * Standoffs: a 4-point ring of solid probes around the axis plus a void probe
    down the pilot — a shifted boss escapes one probe but not the ring.
  * Cutouts: void probes at center + just-inside-edges (catches undersize),
    solid probes just outside the boundary (catches oversize).
  * Lid fit: optimal bounding box enforces footprint (= clearance per side) and
    a boolean intersection with the body must have ~zero volume.
These are spec-anchored oracles, not general feature recognition: they assume
cooperative (not adversarial) codegen, which is the threat model here.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cadquery as cq
import trimesh
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_State

from toolchain.spec import (
    BODY_STEP,
    LID_STEP,
    SCREWS,
    CircleCutout,
    EnclosureSpec,
    Face,
)

BBOX_TOL = 0.1  # mm, on every bounding-box comparison
CLASSIFY_TOL = 1e-4
INTERFERENCE_TOL = 1e-3  # mm^3
DEPTH_FRACTIONS = (0.35, 0.9)  # probe depths as fraction of nominal thickness
EDGE_EPS = 0.5  # void probes sit this far inside a cutout's expected edge
OUTSIDE_MARGIN = 1.0  # solid probes sit this far outside a cutout's boundary
CUTOUT_EXCLUDE = 2.0  # wall probes keep this margin away from cutouts


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Report:
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def text(self) -> str:
        lines = [f"{'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}" for c in self.checks]
        lines.append(f"=> {'OK' if self.ok else f'{len(self.failures())} FAILURE(S)'}")
        return "\n".join(lines)


class _Probe:
    def __init__(self, shape: cq.Shape):
        self._c = BRepClass3d_SolidClassifier(shape.wrapped)

    def solid(self, x: float, y: float, z: float) -> bool:
        self._c.Perform(gp_Pnt(x, y, z), CLASSIFY_TOL)
        return self._c.State() == TopAbs_State.TopAbs_IN


def _bbox(shape: cq.Shape) -> tuple[float, float, float, float, float, float]:
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, True, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return (lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z())


def _load_solid(path: Path, label: str, checks: list[Check]) -> cq.Shape | None:
    if not Path(path).is_file():
        checks.append(Check(f"{label}.load", False, f"missing file {path}"))
        return None
    try:
        solids = cq.importers.importStep(str(path)).solids().vals()
    except Exception as e:
        checks.append(Check(f"{label}.load", False, f"unreadable STEP: {e}"))
        return None
    if len(solids) != 1:
        checks.append(Check(f"{label}.load", False, f"expected 1 solid, got {len(solids)}"))
        return None
    shape = solids[0]
    checks.append(Check(f"{label}.load", True, f"1 solid from {Path(path).name}"))
    checks.append(Check(f"{label}.valid", shape.isValid(), "BRepCheck_Analyzer"))
    checks.append(_watertight(shape, label))
    return shape


def _watertight(shape: cq.Shape, label: str) -> Check:
    with tempfile.TemporaryDirectory() as td:
        stl = Path(td) / "mesh.stl"
        cq.exporters.export(shape, str(stl))
        mesh = trimesh.load(str(stl))
        return Check(
            f"{label}.watertight", bool(mesh.is_watertight),
            f"exported mesh, {len(mesh.faces)} faces",
        )


def _check_bbox(shape: cq.Shape, label: str, expect) -> Check:
    got = _bbox(shape)
    errs = [abs(g - e) for g, e in zip(got, expect)]
    ok = max(errs) <= BBOX_TOL
    return Check(
        f"{label}.bbox", ok,
        f"max err {max(errs):.3f}mm (tol {BBOX_TOL}); got "
        + "({:.2f},{:.2f},{:.2f})-({:.2f},{:.2f},{:.2f})".format(*got),
    )


def _cutout_zones(spec: EnclosureSpec, face: Face) -> list[tuple[float, float, float, float]]:
    """(center_u, cz, half_u, half_z) exclusion rects for wall probing, with margin."""
    zones = []
    for c in spec.cutouts:
        if c.face != face:
            continue
        if isinstance(c, CircleCutout):
            hu = hz = c.diameter / 2
        else:
            hu, hz = c.width / 2, c.height / 2
        zones.append((c.center, c.cz, hu + CUTOUT_EXCLUDE, hz + CUTOUT_EXCLUDE))
    return zones


def _check_walls(spec: EnclosureSpec, probe: _Probe) -> list[Check]:
    checks = []
    z_lo, z_hi = spec.floor, spec.post_top_z
    z_samples = [z_lo + f * (z_hi - z_lo) for f in (0.3, 0.7)]

    for face in Face:
        span = spec.width if face in (Face.XP, Face.XN) else spec.length
        u_samples = [spec.wall + f * (span - 2 * spec.wall) for f in (0.25, 0.5, 0.75)]
        zones = _cutout_zones(spec, face)
        misses, probed = [], 0
        for u in u_samples:
            for z in z_samples:
                if any(abs(u - cu) <= hu and abs(z - cz) <= hz for cu, cz, hu, hz in zones):
                    continue
                for frac in DEPTH_FRACTIONS:
                    d = frac * spec.wall
                    if face == Face.XP:
                        pt = (spec.length - d, u, z)
                    elif face == Face.XN:
                        pt = (d, u, z)
                    elif face == Face.YP:
                        pt = (u, spec.width - d, z)
                    else:
                        pt = (u, d, z)
                    probed += 1
                    if not probe.solid(*pt):
                        misses.append(f"depth {d:.2f}mm at u={u:.1f},z={z:.1f}")
        checks.append(Check(
            f"wall.{face.value}", not misses,
            f"{probed} probes" + (f", missing material: {'; '.join(misses[:3])}" if misses else ", all solid"),
        ))

    misses, probed = [], 0
    for fx in (0.3, 0.5, 0.7):
        for fy in (0.3, 0.7):
            x = spec.wall + fx * (spec.length - 2 * spec.wall)
            y = spec.wall + fy * (spec.width - 2 * spec.wall)
            for frac in DEPTH_FRACTIONS:
                probed += 1
                if not probe.solid(x, y, frac * spec.floor):
                    misses.append(f"z={frac * spec.floor:.2f} at ({x:.1f},{y:.1f})")
    checks.append(Check(
        "floor", not misses,
        f"{probed} probes" + (f", missing material: {'; '.join(misses[:3])}" if misses else ", all solid"),
    ))
    return checks


def _check_posts(spec: EnclosureSpec, probe: _Probe) -> list[Check]:
    """Corner posts: solid ring at mid-height, pilot void below the post top."""
    checks = []
    ring_r = (spec.post_diameter / 2 + SCREWS[spec.screw].pilot / 2) / 2
    z_mid = (spec.floor + spec.post_top_z) / 2
    pilot_depth = min(spec.screw_depth, spec.post_top_z - spec.floor)
    z_pilot = spec.post_top_z - pilot_depth / 2
    for i, (px, py) in enumerate(spec.post_centers()):
        problems = []
        for dx, dy in ((ring_r, 0), (-ring_r, 0), (0, ring_r), (0, -ring_r)):
            if not probe.solid(px + dx, py + dy, z_mid):
                problems.append(f"no post material at ring({dx:+.1f},{dy:+.1f})")
        if probe.solid(px, py, z_pilot):
            problems.append("pilot hole missing (center is solid near top)")
        checks.append(Check(
            f"post[{i}]", not problems,
            f"({px:.1f},{py:.1f}): " + ("; ".join(problems[:3]) if problems else "ring solid, pilot void"),
        ))
    return checks


def _check_standoffs(spec: EnclosureSpec, probe: _Probe) -> list[Check]:
    checks = []
    for i, s in enumerate(spec.standoffs):
        ring_r = (s.od() / 2 + SCREWS[s.screw].pilot / 2) / 2
        problems = []
        for zf in (0.5, 0.9):
            z = spec.floor + zf * s.height
            for dx, dy in ((ring_r, 0), (-ring_r, 0), (0, ring_r), (0, -ring_r)):
                if not probe.solid(s.x + dx, s.y + dy, z):
                    problems.append(f"no boss material at ring({dx:+.1f},{dy:+.1f}) z={z:.1f}")
        if probe.solid(s.x, s.y, spec.floor + 0.5 * s.height):
            problems.append("pilot hole missing (center is solid)")
        if probe.solid(s.x + ring_r, s.y, spec.floor + s.height + 0.5):
            problems.append("material above nominal top (too tall)")
        checks.append(Check(
            f"standoff[{i}]", not problems,
            f"({s.x},{s.y}) h={s.height}: " + ("; ".join(problems[:3]) if problems else "ring solid, pilot void, top clear"),
        ))
    return checks


def _check_cutouts(spec: EnclosureSpec, probe: _Probe) -> list[Check]:
    checks = []
    for i, c in enumerate(spec.cutouts):
        mid = spec.wall / 2
        if c.face == Face.XP:
            to_xyz = lambda u, z: (spec.length - mid, u, z)
        elif c.face == Face.XN:
            to_xyz = lambda u, z: (mid, u, z)
        elif c.face == Face.YP:
            to_xyz = lambda u, z: (u, spec.width - mid, z)
        else:
            to_xyz = lambda u, z: (u, mid, z)

        if isinstance(c, CircleCutout):
            r = c.diameter / 2
            inner = [(c.center, c.cz)] + [
                (c.center + du * (r - EDGE_EPS), c.cz + dz * (r - EDGE_EPS))
                for du, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
            ]
            outer = [
                (c.center + du * (r + OUTSIDE_MARGIN), c.cz + dz * (r + OUTSIDE_MARGIN))
                for du, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
            ]
        else:
            hw, hh = c.width / 2, c.height / 2
            inner = [(c.center, c.cz)] + [
                (c.center + du * (hw - EDGE_EPS), c.cz + dz * (hh - EDGE_EPS))
                for du, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
            ]
            outer = [
                (c.center + du * (hw + OUTSIDE_MARGIN), c.cz + dz * (hh + OUTSIDE_MARGIN))
                for du, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
            ]

        problems = []
        for u, z in inner:
            if probe.solid(*to_xyz(u, z)):
                problems.append(f"expected opening blocked at (u={u:.1f},z={z:.1f})")
        for u, z in outer:
            if not probe.solid(*to_xyz(u, z)):
                problems.append(f"opening larger than spec at (u={u:.1f},z={z:.1f})")
        checks.append(Check(
            f"cutout[{i}]", not problems,
            f"{c.kind} on {c.face.value}: " + ("; ".join(problems[:3]) if problems else "open to size, closed outside"),
        ))
    return checks


def _check_lid(spec: EnclosureSpec, lid: cq.Shape, body: cq.Shape) -> list[Check]:
    checks = []
    lw, ld = spec.lid_size
    x0 = spec.wall + spec.fit_clearance
    y0 = spec.wall + spec.fit_clearance
    checks.append(_check_bbox(
        lid, "lid", (x0, y0, spec.post_top_z, x0 + lw, y0 + ld, spec.height)
    ))

    probe = _Probe(lid)
    z_mid = spec.post_top_z + spec.lid_thickness / 2
    blocked = [
        f"({px:.1f},{py:.1f})" for px, py in spec.post_centers()
        if probe.solid(px, py, z_mid)
    ]
    checks.append(Check(
        "lid.screw_holes", not blocked,
        "4 clearance holes open at post centers" if not blocked else f"no hole at {', '.join(blocked)}",
    ))

    try:
        common = body.intersect(lid)
        vol = common.Volume() if common is not None else 0.0
    except Exception:
        vol = 0.0  # empty boolean result
    checks.append(Check(
        "fit.interference", vol <= INTERFERENCE_TOL,
        f"body∩lid volume {vol:.6f}mm³ (tol {INTERFERENCE_TOL})",
    ))
    return checks


def verify(spec: EnclosureSpec, out_dir: Path) -> Report:
    """Run every spec-derived assertion against body.step + lid.step in out_dir."""
    out_dir = Path(out_dir)
    checks: list[Check] = []

    body = _load_solid(out_dir / BODY_STEP, "body", checks)
    lid = _load_solid(out_dir / LID_STEP, "lid", checks)

    if body is not None:
        checks.append(_check_bbox(
            body, "body", (0, 0, 0, spec.length, spec.width, spec.height)
        ))
        probe = _Probe(body)
        checks.extend(_check_walls(spec, probe))
        checks.extend(_check_posts(spec, probe))
        checks.extend(_check_standoffs(spec, probe))
        checks.extend(_check_cutouts(spec, probe))
    if body is not None and lid is not None:
        checks.extend(_check_lid(spec, lid, body))

    return Report(checks)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m toolchain.verify <spec.json> <out_dir>", file=sys.stderr)
        return 2
    spec = EnclosureSpec.model_validate(json.loads(Path(argv[0]).read_text(encoding="utf-8")))
    report = verify(spec, Path(argv[1]))
    print(report.text())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
