"""Hand-written parametric reference builder for the enclosure spec.

This is the known-good implementation the verify layer is calibrated against,
and (with `sabotage=`) the source of deliberately-wrong parts used to prove
verify actually refuses bad geometry. Agent-generated code must honor the same
output contract: `body.step` + `lid.step` in assembled coordinates (see spec.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import cadquery as cq
from cadquery import Solid, Vector

from toolchain.spec import (
    BODY_STEP,
    LID_STEP,
    SCREWS,
    CircleCutout,
    EnclosureSpec,
    Face,
)

Sabotage = Literal["thin_wall", "misplaced_standoff", "undersized_cutout", "missing_post"]

# cut tools overshoot faces by this much so booleans are never coincident
FUDGE = 1.0


def _box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float):
    return Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, Vector(x0, y0, z0))


def build_body(spec: EnclosureSpec, sabotage: Sabotage | None = None):
    wall = spec.wall * (0.5 if sabotage == "thin_wall" else 1.0)
    L, W, H = spec.length, spec.width, spec.height

    body = _box(0, 0, 0, L, W, H)
    body = body.cut(_box(wall, wall, spec.floor, L - wall, W - wall, H + FUDGE))

    for j, (px, py) in enumerate(spec.post_centers()):
        if sabotage == "missing_post" and j == 0:
            continue
        post = Solid.makeCylinder(
            spec.post_diameter / 2, spec.post_top_z - spec.floor,
            Vector(px, py, spec.floor), Vector(0, 0, 1),
        )
        body = body.fuse(post)
        pilot = Solid.makeCylinder(
            SCREWS[spec.screw].pilot / 2, spec.screw_depth,
            Vector(px, py, spec.post_top_z - spec.screw_depth), Vector(0, 0, 1),
        )
        body = body.cut(pilot)

    for i, s in enumerate(spec.standoffs):
        sx = s.x + (4.0 if sabotage == "misplaced_standoff" and i == 0 else 0.0)
        boss = Solid.makeCylinder(
            s.od() / 2, s.height, Vector(sx, s.y, spec.floor), Vector(0, 0, 1)
        )
        body = body.fuse(boss)
        pilot = Solid.makeCylinder(
            SCREWS[s.screw].pilot / 2, s.height,
            Vector(sx, s.y, spec.floor), Vector(0, 0, 1),
        )
        body = body.cut(pilot)

    for i, c in enumerate(spec.cutouts):
        shrink = 0.5 if sabotage == "undersized_cutout" and i == 0 else 1.0
        body = body.cut(_cutout_tool(spec, c, shrink))

    return body


def _cutout_tool(spec: EnclosureSpec, c, shrink: float):
    """A through-wall cutting solid for one cutout, overshooting both wall faces."""
    if isinstance(c, CircleCutout):
        r = c.diameter * shrink / 2
        along = spec.wall + 2 * FUDGE
        if c.face in (Face.XP, Face.XN):
            x0 = spec.length - spec.wall - FUDGE if c.face == Face.XP else -FUDGE
            return Solid.makeCylinder(r, along, Vector(x0, c.center, c.cz), Vector(1, 0, 0))
        y0 = spec.width - spec.wall - FUDGE if c.face == Face.YP else -FUDGE
        return Solid.makeCylinder(r, along, Vector(c.center, y0, c.cz), Vector(0, 1, 0))

    hw, hh = c.width * shrink / 2, c.height * shrink / 2
    z0, z1 = c.cz - hh, c.cz + hh
    if c.face in (Face.XP, Face.XN):
        x0 = spec.length - spec.wall - FUDGE if c.face == Face.XP else -FUDGE
        return _box(x0, c.center - hw, z0, x0 + spec.wall + 2 * FUDGE, c.center + hw, z1)
    y0 = spec.width - spec.wall - FUDGE if c.face == Face.YP else -FUDGE
    return _box(c.center - hw, y0, z0, c.center + hw, y0 + spec.wall + 2 * FUDGE, z1)


def build_lid(spec: EnclosureSpec):
    lw, ld = spec.lid_size
    x0 = spec.wall + spec.fit_clearance
    y0 = spec.wall + spec.fit_clearance
    lid = _box(x0, y0, spec.post_top_z, x0 + lw, y0 + ld, spec.height)
    for px, py in spec.post_centers():
        hole = Solid.makeCylinder(
            SCREWS[spec.screw].clearance / 2, spec.lid_thickness + 2 * FUDGE,
            Vector(px, py, spec.post_top_z - FUDGE), Vector(0, 0, 1),
        )
        lid = lid.cut(hole)
    return lid


def build(spec: EnclosureSpec, out_dir: Path, sabotage: Sabotage | None = None) -> tuple[Path, Path]:
    """Build both solids and write the STEP pair per the output contract."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    body_path, lid_path = out_dir / BODY_STEP, out_dir / LID_STEP
    cq.exporters.export(build_body(spec, sabotage), str(body_path))
    cq.exporters.export(build_lid(spec), str(lid_path))
    return body_path, lid_path
