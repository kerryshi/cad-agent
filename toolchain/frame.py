"""Frozen drone-frame spec — v2 part family: a flat one-piece quadcopter plate.

Coordinate convention (fixed, part of the contract):
  * Units are millimetres. The plate is CENTERED at the origin in X/Y and
    spans z = 0 (bottom, print bed) to z = plate_thickness.
  * The FC mount square is aligned with the global X/Y axes; the four arms
    run along the diagonals at 45°, 135°, 225°, 315°.
  * Motor centers sit at radius wheelbase/2 along those diagonals
    (wheelbase = distance between diagonally opposite motor centers).
  * Motor mount holes sit at the corners of the pattern square in ARM-LOCAL
    coordinates (u along the arm, v perpendicular): (±s/2, ±s/2), plus a
    central hub clearance hole. All holes are through-holes.

Output contract for any builder: write exactly one STEP file, `frame.step`,
in these coordinates.
"""

from __future__ import annotations

import enum
import math

from pydantic import BaseModel, Field, model_validator

from toolchain.spec import SCREWS, Screw

MM = float

FRAME_STEP = "frame.step"
PROP_CLEARANCE = 5.0  # mm margin between prop tips
PAD_MARGIN = 8.0  # motor pad diameter = pattern diagonal + this


class FCPattern(str, enum.Enum):
    FC30 = "30.5x30.5-M3"
    FC25 = "25.5x25.5-M2"
    FC20 = "20x20-M2"


class MotorPattern(str, enum.Enum):
    M16 = "16x16-M3"
    M19 = "19x19-M3"
    M12 = "12x12-M2"
    M9 = "9x9-M2"


_PATTERNS: dict[str, tuple[float, Screw]] = {
    FCPattern.FC30: (30.5, Screw.M3),
    FCPattern.FC25: (25.5, Screw.M2),
    FCPattern.FC20: (20.0, Screw.M2),
    MotorPattern.M16: (16.0, Screw.M3),
    MotorPattern.M19: (19.0, Screw.M3),
    MotorPattern.M12: (12.0, Screw.M2),
    MotorPattern.M9: (9.0, Screw.M2),
}


def pattern_size(p: FCPattern | MotorPattern) -> float:
    return _PATTERNS[p][0]


def pattern_hole_d(p: FCPattern | MotorPattern) -> float:
    return SCREWS[_PATTERNS[p][1]].clearance


class FrameSpec(BaseModel):
    """Flat X-quad frame plate: central body, four diagonal arms, motor pads."""

    wheelbase: MM = Field(gt=0)  # diagonal motor-to-motor distance
    plate_thickness: MM = Field(default=4.0, ge=2.0)
    arm_width: MM = Field(default=12.0, ge=6.0)
    body_width: MM = Field(default=36.0, gt=0)  # square central body, side length
    fc_mount: FCPattern = FCPattern.FC30
    motor_mount: MotorPattern = MotorPattern.M16
    hub_hole_diameter: MM = Field(default=8.0, gt=0)
    prop_size_inch: float | None = Field(default=None, gt=0)  # validation only

    # ---- derived geometry (single source of truth for builder AND verify) ----

    @property
    def motor_radius(self) -> MM:
        return self.wheelbase / 2

    @property
    def pad_diameter(self) -> MM:
        return pattern_size(self.motor_mount) * math.sqrt(2) + PAD_MARGIN

    def arm_angles_deg(self) -> list[float]:
        return [45.0, 135.0, 225.0, 315.0]

    def motor_centers(self) -> list[tuple[MM, MM]]:
        return [
            (self.motor_radius * math.cos(math.radians(a)),
             self.motor_radius * math.sin(math.radians(a)))
            for a in self.arm_angles_deg()
        ]

    def motor_hole_centers(self, i: int) -> list[tuple[MM, MM]]:
        """Global (x, y) of the 4 pattern holes on motor i (arm-local corners)."""
        s = pattern_size(self.motor_mount) / 2
        a = math.radians(self.arm_angles_deg()[i])
        cx, cy = self.motor_centers()[i]
        cos_a, sin_a = math.cos(a), math.sin(a)
        return [
            (cx + u * cos_a - v * sin_a, cy + u * sin_a + v * cos_a)
            for u, v in ((s, s), (s, -s), (-s, s), (-s, -s))
        ]

    def fc_hole_centers(self) -> list[tuple[MM, MM]]:
        p = pattern_size(self.fc_mount) / 2
        return [(p, p), (p, -p), (-p, p), (-p, -p)]

    # ---- validation ----

    @model_validator(mode="after")
    def _check(self) -> "FrameSpec":
        fc_d = pattern_hole_d(self.fc_mount)
        if self.body_width < pattern_size(self.fc_mount) + fc_d + 4:
            raise ValueError("body too small for the FC mount pattern")
        body_half_diag = self.body_width * math.sqrt(2) / 2
        if self.motor_radius < body_half_diag + self.pad_diameter / 2 + 5:
            raise ValueError("wheelbase too small: motor pads reach into the body")
        motor_spacing = self.wheelbase / math.sqrt(2)  # adjacent motors
        if motor_spacing < self.pad_diameter + 2:
            raise ValueError("adjacent motor pads overlap")
        if self.prop_size_inch is not None:
            prop_d = self.prop_size_inch * 25.4
            if motor_spacing < prop_d + PROP_CLEARANCE:
                raise ValueError(
                    f"props collide: {self.prop_size_inch}\" props need "
                    f">= {prop_d + PROP_CLEARANCE:.1f}mm adjacent spacing, "
                    f"wheelbase {self.wheelbase} gives {motor_spacing:.1f}mm"
                )
        s = pattern_size(self.motor_mount)
        if self.hub_hole_diameter / 2 + pattern_hole_d(self.motor_mount) / 2 + 1 > s * math.sqrt(2) / 2:
            raise ValueError("hub hole overlaps motor pattern holes")
        return self
