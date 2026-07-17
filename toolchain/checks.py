"""Shared verification machinery, family-agnostic.

Every part family's verifier builds on the same primitives: point
classification against the solid (material where the spec demands it, void
where it demands absence), optimal bounding boxes, STEP loading that refuses
instead of crashing, and mesh watertightness on the exported geometry.
"""

from __future__ import annotations

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

CLASSIFY_TOL = 1e-4


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


class Probe:
    def __init__(self, shape: cq.Shape):
        self._c = BRepClass3d_SolidClassifier(shape.wrapped)

    def solid(self, x: float, y: float, z: float) -> bool:
        self._c.Perform(gp_Pnt(x, y, z), CLASSIFY_TOL)
        return self._c.State() == TopAbs_State.TopAbs_IN


def bbox_of(shape: cq.Shape) -> tuple[float, float, float, float, float, float]:
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, True, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return (lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z())


def check_bbox(shape: cq.Shape, label: str, expect, tol: float) -> Check:
    got = bbox_of(shape)
    errs = [abs(g - e) for g, e in zip(got, expect)]
    ok = max(errs) <= tol
    return Check(
        f"{label}.bbox", ok,
        f"max err {max(errs):.3f}mm (tol {tol}); got "
        + "({:.2f},{:.2f},{:.2f})-({:.2f},{:.2f},{:.2f})".format(*got),
    )


def watertight_check(shape: cq.Shape, label: str) -> Check:
    with tempfile.TemporaryDirectory() as td:
        stl = Path(td) / "mesh.stl"
        cq.exporters.export(shape, str(stl))
        mesh = trimesh.load(str(stl))
        return Check(
            f"{label}.watertight", bool(mesh.is_watertight),
            f"exported mesh, {len(mesh.faces)} faces",
        )


def load_single_solid(path: Path, label: str, checks: list[Check]) -> cq.Shape | None:
    """Load exactly one solid from a STEP file, refusing (not crashing) otherwise."""
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
    checks.append(watertight_check(shape, label))
    return shape
