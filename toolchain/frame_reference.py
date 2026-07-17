"""Hand-written reference builder for the drone-frame family, with sabotage
hooks so the frame verifier can be watched refusing bad geometry."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import cadquery as cq
from cadquery import Solid, Vector

from toolchain.frame import FRAME_STEP, FrameSpec, pattern_hole_d

Sabotage = Literal["missing_arm", "misplaced_motor_hole", "thin_plate"]

FUDGE = 1.0


def build_frame(spec: FrameSpec, sabotage: Sabotage | None = None):
    t = spec.plate_thickness * (0.5 if sabotage == "thin_plate" else 1.0)
    b = spec.body_width

    frame = Solid.makeBox(b, b, t, Vector(-b / 2, -b / 2, 0))

    for i, angle in enumerate(spec.arm_angles_deg()):
        if sabotage == "missing_arm" and i == 0:
            continue
        arm = Solid.makeBox(
            spec.motor_radius, spec.arm_width, t,
            Vector(0, -spec.arm_width / 2, 0),
        ).rotate(Vector(0, 0, 0), Vector(0, 0, 1), angle)
        frame = frame.fuse(arm)
        cx, cy = spec.motor_centers()[i]
        pad = Solid.makeCylinder(spec.pad_diameter / 2, t, Vector(cx, cy, 0), Vector(0, 0, 1))
        frame = frame.fuse(pad)

    def drill(x: float, y: float, d: float):
        nonlocal frame
        frame = frame.cut(Solid.makeCylinder(
            d / 2, t + 2 * FUDGE, Vector(x, y, -FUDGE), Vector(0, 0, 1)))

    for x, y in spec.fc_hole_centers():
        drill(x, y, pattern_hole_d(spec.fc_mount))

    hole_d = pattern_hole_d(spec.motor_mount)
    for i in range(4):
        if sabotage == "missing_arm" and i == 0:
            continue
        cx, cy = spec.motor_centers()[i]
        drill(cx, cy, spec.hub_hole_diameter)
        for j, (hx, hy) in enumerate(spec.motor_hole_centers(i)):
            if sabotage == "misplaced_motor_hole" and i == 0 and j == 0:
                hx += 3.0
            drill(hx, hy, hole_d)

    return frame


def build(spec: FrameSpec, out_dir: Path, sabotage: Sabotage | None = None) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / FRAME_STEP
    cq.exporters.export(build_frame(spec, sabotage), str(path))
    return path
