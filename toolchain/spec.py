"""Frozen enclosure spec — the contract between spec extraction, codegen, and verify.

Everything downstream (reference builder, generated code, verify assertions) obeys
this coordinate convention. It is part of the spec, not an implementation detail:

  * Units are millimetres.
  * The body's outer minimum corner sits at the origin (0, 0, 0).
  * +X spans `length`, +Y spans `width`, +Z is up and spans `height`.
  * BOTH solids are modelled in assembled coordinates: the lid sits in the body
    opening with its top face flush at z = height. Print orientation is the
    slicer's problem, not the model's.
  * The lid is an INSET plate: footprint = inner opening minus `fit_clearance`
    per side, resting on the four corner posts (post tops at height - lid_thickness).
  * Cutout centers are given in the global frame axes of their wall:
    faces "+x"/"-x" use (cy, cz); faces "+y"/"-y" use (cx, cz).

Output contract for any builder (reference or agent-generated): write exactly
two STEP files, `body.step` and `lid.step`, in assembled coordinates.
"""

from __future__ import annotations

import enum
from typing import Literal, Union

from pydantic import BaseModel, Field, model_validator

MM = float

BODY_STEP = "body.step"
LID_STEP = "lid.step"


class Screw(str, enum.Enum):
    M2 = "M2"
    M2_5 = "M2.5"
    M3 = "M3"
    M4 = "M4"


class ScrewDims(BaseModel):
    pilot: MM  # self-tap pilot hole diameter (into printed boss)
    clearance: MM  # through-hole diameter in the lid


SCREWS: dict[Screw, ScrewDims] = {
    Screw.M2: ScrewDims(pilot=1.6, clearance=2.4),
    Screw.M2_5: ScrewDims(pilot=2.05, clearance=2.9),
    Screw.M3: ScrewDims(pilot=2.5, clearance=3.4),
    Screw.M4: ScrewDims(pilot=3.3, clearance=4.5),
}


class Face(str, enum.Enum):
    XP = "+x"
    XN = "-x"
    YP = "+y"
    YN = "-y"


class Standoff(BaseModel):
    """PCB standoff: a boss on the floor with a pilot hole down its axis."""

    x: MM
    y: MM
    height: MM = Field(gt=0)
    screw: Screw = Screw.M2_5
    outer_diameter: MM | None = Field(default=None, gt=0)  # None: pilot + 3.2 (1.6mm wall)

    def od(self) -> MM:
        return self.outer_diameter or SCREWS[self.screw].pilot + 3.2


class RectCutout(BaseModel):
    kind: Literal["rect"] = "rect"
    face: Face
    center: MM  # cy for ±x faces, cx for ±y faces
    cz: MM
    width: MM = Field(gt=0)  # along the wall
    height: MM = Field(gt=0)  # along z


class CircleCutout(BaseModel):
    kind: Literal["circle"] = "circle"
    face: Face
    center: MM
    cz: MM
    diameter: MM = Field(gt=0)


Cutout = Union[RectCutout, CircleCutout]


class EnclosureSpec(BaseModel):
    """One enclosure: open-top body + inset screw-down lid on corner posts."""

    length: MM = Field(gt=0)  # outer, along X
    width: MM = Field(gt=0)  # outer, along Y
    height: MM = Field(gt=0)  # outer, along Z (includes floor; lid is flush inside)
    wall: MM = Field(default=2.0, gt=0)
    floor: MM = Field(default=2.0, gt=0)
    lid_thickness: MM = Field(default=2.4, gt=0)
    fit_clearance: MM = Field(default=0.2, ge=0)  # per side, lid edge to inner wall
    post_diameter: MM = Field(default=7.0, gt=0)
    screw: Screw = Screw.M3
    screw_depth: MM = Field(default=8.0, gt=0)  # pilot depth into each post
    standoffs: list[Standoff] = []
    cutouts: list[Cutout] = []

    # ---- derived geometry (single source of truth for builder AND verify) ----

    @property
    def cavity_x(self) -> tuple[MM, MM]:
        return (self.wall, self.length - self.wall)

    @property
    def cavity_y(self) -> tuple[MM, MM]:
        return (self.wall, self.width - self.wall)

    @property
    def post_top_z(self) -> MM:
        return self.height - self.lid_thickness

    @property
    def lid_size(self) -> tuple[MM, MM]:
        return (
            self.length - 2 * self.wall - 2 * self.fit_clearance,
            self.width - 2 * self.wall - 2 * self.fit_clearance,
        )

    def post_centers(self) -> list[tuple[MM, MM]]:
        """Four corner posts, tangent to both adjacent inner walls."""
        inset = self.wall + self.post_diameter / 2
        return [
            (inset, inset),
            (self.length - inset, inset),
            (self.length - inset, self.width - inset),
            (inset, self.width - inset),
        ]

    # ---- validation ----

    @model_validator(mode="after")
    def _check(self) -> "EnclosureSpec":
        if 2 * self.wall + 2 * self.post_diameter >= min(self.length, self.width):
            raise ValueError("posts/walls don't fit the footprint")
        if self.floor + self.lid_thickness >= self.height:
            raise ValueError("floor + lid leave no cavity height")
        cav_top = self.post_top_z
        for s in self.standoffs:
            x0, x1 = self.cavity_x
            y0, y1 = self.cavity_y
            r = s.od() / 2
            if not (x0 + r <= s.x <= x1 - r and y0 + r <= s.y <= y1 - r):
                raise ValueError(f"standoff at ({s.x},{s.y}) not inside cavity")
            if self.floor + s.height >= cav_top:
                raise ValueError(f"standoff at ({s.x},{s.y}) taller than cavity")
        for c in self.cutouts:
            along_len = self.length if c.face in (Face.YP, Face.YN) else self.width
            if c.kind == "rect":
                half_w, half_h = c.width / 2, c.height / 2
            else:
                half_w = half_h = c.diameter / 2
            if not (self.wall + half_w <= c.center <= along_len - self.wall - half_w):
                raise ValueError(f"cutout on {c.face.value} exceeds wall span")
            if not (self.floor + half_h <= c.cz <= cav_top - half_h):
                raise ValueError(f"cutout on {c.face.value} exceeds wall height")
        return self
