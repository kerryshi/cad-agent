"""Spec-derived verification for the drone-frame family.

Same doctrine as the enclosure verifier: every assertion derives from the
frozen FrameSpec (the codegen actor never writes these), probes are
spec-anchored oracles assuming cooperative codegen, and thickness is checked
by classification at known depths, not measured on the B-Rep.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from toolchain.checks import Check, Probe, Report, check_bbox, load_single_solid
from toolchain.frame import FRAME_STEP, FrameSpec, pattern_hole_d, pattern_size

BBOX_TOL = 0.2
DEPTH_FRACTIONS = (0.35, 0.9)


def _check_plate(spec: FrameSpec, probe: Probe) -> Check:
    """Thickness probes at the body center and each arm midpoint; the space
    just above the nominal top must be void (catches thin AND thick)."""
    t = spec.plate_thickness
    spots = [(0.0, 0.0)]
    for a in spec.arm_angles_deg():
        r = 0.6 * spec.motor_radius
        spots.append((r * math.cos(math.radians(a)), r * math.sin(math.radians(a))))
    problems = []
    for x, y in spots:
        for frac in DEPTH_FRACTIONS:
            if not probe.solid(x, y, frac * t):
                problems.append(f"void at ({x:.0f},{y:.0f}) z={frac * t:.2f}")
        if probe.solid(x, y, t + 0.5):
            problems.append(f"material above nominal top at ({x:.0f},{y:.0f})")
    return Check("plate.thickness", not problems,
                 ("; ".join(problems[:3]) if problems else f"{len(spots)} spots solid through depth, top clear"))


def _check_arms(spec: FrameSpec, probe: Probe) -> list[Check]:
    checks = []
    z = spec.plate_thickness / 2
    for i, a in enumerate(spec.arm_angles_deg()):
        problems = []
        for frac in (0.55, 0.8):
            r = frac * spec.motor_radius
            x, y = r * math.cos(math.radians(a)), r * math.sin(math.radians(a))
            if not probe.solid(x, y, z):
                problems.append(f"no arm material at r={r:.0f}")
        checks.append(Check(f"arm[{i}]", not problems,
                            ("; ".join(problems) if problems else "centerline solid")))
    return checks


def _check_motors(spec: FrameSpec, probe: Probe) -> list[Check]:
    checks = []
    z = spec.plate_thickness / 2
    s_half = pattern_size(spec.motor_mount) / 2
    ring_r = s_half * math.sqrt(2)  # same radius as the holes, rotated 45° off them
    for i in range(4):
        cx, cy = spec.motor_centers()[i]
        a = math.radians(spec.arm_angles_deg()[i])
        problems = []
        if probe.solid(cx, cy, z):
            problems.append("hub hole missing")
        for hx, hy in spec.motor_hole_centers(i):
            if probe.solid(hx, hy, z):
                problems.append(f"pattern hole blocked at ({hx:.1f},{hy:.1f})")
        # pad material between the holes (arm-local axes, rotated to global)
        for u, v in ((ring_r, 0), (-ring_r, 0), (0, ring_r), (0, -ring_r)):
            gx = cx + u * math.cos(a) - v * math.sin(a)
            gy = cy + u * math.sin(a) + v * math.cos(a)
            if not probe.solid(gx, gy, z):
                problems.append(f"no pad material at ({gx:.1f},{gy:.1f})")
        checks.append(Check(f"motor[{i}]", not problems,
                            ("; ".join(problems[:3]) if problems else "hub+4 holes open, pad solid")))
    return checks


def _check_fc(spec: FrameSpec, probe: Probe) -> Check:
    z = spec.plate_thickness / 2
    p = pattern_size(spec.fc_mount) / 2
    problems = []
    for x, y in spec.fc_hole_centers():
        if probe.solid(x, y, z):
            problems.append(f"FC hole blocked at ({x:.2f},{y:.2f})")
    for x, y in ((p, 0), (-p, 0), (0, p), (0, -p)):
        if not probe.solid(x, y, z):
            problems.append(f"no body material between FC holes at ({x:.1f},{y:.1f})")
    return Check("fc_mount", not problems,
                 ("; ".join(problems[:3]) if problems else "4 holes open, body solid between"))


def verify_frame(spec: FrameSpec, out_dir: Path) -> Report:
    out_dir = Path(out_dir)
    checks: list[Check] = []
    frame = load_single_solid(out_dir / FRAME_STEP, "frame", checks)
    if frame is not None:
        half = max(spec.motor_radius / math.sqrt(2) + spec.pad_diameter / 2,
                   spec.body_width / 2)
        checks.append(check_bbox(
            frame, "frame",
            (-half, -half, 0, half, half, spec.plate_thickness), BBOX_TOL,
        ))
        probe = Probe(frame)
        checks.append(_check_plate(spec, probe))
        checks.extend(_check_arms(spec, probe))
        checks.extend(_check_motors(spec, probe))
        checks.append(_check_fc(spec, probe))
    return Report(checks)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m toolchain.frame_verify <spec.json> <out_dir>", file=sys.stderr)
        return 2
    spec = FrameSpec.model_validate(json.loads(Path(argv[0]).read_text(encoding="utf-8")))
    report = verify_frame(spec, Path(argv[1]))
    print(report.text())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
