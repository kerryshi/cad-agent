"""Offscreen multi-view PNG renders of built parts, for VLM gross-error critique.

VTK offscreen rendering (VTK ships with cadquery); validated on this machine
2026-07-17 — no display, no xvfb. Renders are for a vision model to catch
gross shape errors (missing features, wildly wrong proportions) ONLY. A VLM
cannot measure; dimensional truth lives in verify.py's probes.
"""

from __future__ import annotations

from pathlib import Path

import cadquery as cq
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (registers the GL backend)
from vtkmodules.vtkIOImage import vtkPNGWriter
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkPolyDataMapper,
    vtkRenderer,
    vtkRenderWindow,
    vtkWindowToImageFilter,
)

from toolchain.spec import BODY_STEP, LID_STEP

SIZE = 768
MESH_TOL = 1e-2

# unit view directions (scaled by the part's bbox diagonal), view-up per view
BODY_VIEWS = {
    "iso_ne": ((1, 1, 0.8), (0, 0, 1)),
    "iso_sw": ((-1, -1, 0.8), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "front": ((0, -1, 0.1), (0, 0, 1)),
}
LID_VIEWS = {
    "top": ((0, 0, 1), (0, 1, 0)),
    "iso": ((1, 1, 1), (0, 0, 1)),
}
# flat plate-like parts (frames): one oblique, one true-top, one edge-on
PLATE_VIEWS = {
    "iso": ((1, 1, 0.8), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "front": ((0, -1, 0.1), (0, 0, 1)),
}


def render_solid(shape: cq.Shape, out_dir: Path, prefix: str,
                 views: dict = BODY_VIEWS) -> list[Path]:
    """Render one solid to a PNG per view. Returns written paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    poly = shape.toVtkPolyData(MESH_TOL, 0.1)
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(poly)
    actor = vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(0.69, 0.77, 0.87)

    renderer = vtkRenderer()
    renderer.AddActor(actor)
    renderer.SetBackground(1.0, 1.0, 1.0)
    window = vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.AddRenderer(renderer)
    window.SetSize(SIZE, SIZE)

    bb = shape.BoundingBox()
    center = ((bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2, (bb.zmin + bb.zmax) / 2)
    diag = max(bb.DiagonalLength, 1.0)

    written = []
    for name, (direction, up) in views.items():
        cam = renderer.GetActiveCamera()
        cam.SetFocalPoint(*center)
        cam.SetPosition(*(c + d * diag * 1.6 for c, d in zip(center, direction)))
        cam.SetViewUp(*up)
        renderer.ResetCamera()
        renderer.ResetCameraClippingRange()
        window.Render()

        w2i = vtkWindowToImageFilter()
        w2i.SetInput(window)
        w2i.Update()
        writer = vtkPNGWriter()
        path = out_dir / f"{prefix}_{name}.png"
        writer.SetFileName(str(path))
        writer.SetInputConnection(w2i.GetOutputPort())
        writer.Write()
        written.append(path)
    return written


def render_build(build_dir: Path, family, out_dir: Path | None = None) -> list[Path]:
    """Render every staged part of a build per the family's view specs.

    Strict counterpart to render_workdir: a missing part file raises — in a
    staged build the output contract guarantees the files, so absence is a
    defect, not a case to skip past.
    """
    build_dir = Path(build_dir)
    out_dir = Path(out_dir) if out_dir else build_dir / "renders"
    written = []
    for filename in family.output_files:
        path = build_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"staged part missing: {path}")
        prefix = filename.split(".")[0]
        views = family.part_views[filename]
        solids = cq.importers.importStep(str(path)).solids().vals()
        if not solids:
            raise ValueError(f"no solids in {path}")
        written.extend(render_solid(solids[0], out_dir, prefix, views))
    return written


def render_workdir(workdir: Path, out_dir: Path | None = None) -> list[Path]:
    """Render body.step + lid.step from a build workdir (the output contract)."""
    workdir = Path(workdir)
    out_dir = Path(out_dir) if out_dir else workdir / "renders"
    written = []
    for filename, prefix, views in (
        (BODY_STEP, "body", BODY_VIEWS), (LID_STEP, "lid", LID_VIEWS),
    ):
        path = workdir / filename
        if not path.is_file():
            continue
        solids = cq.importers.importStep(str(path)).solids().vals()
        if solids:
            written.extend(render_solid(solids[0], out_dir, prefix, views))
    return written
