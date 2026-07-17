"""Part-family registry — the dispatch point for everything family-specific.

A family bundles its frozen spec model, the source files to embed in prompts
(single source of geometric truth), the output-file contract, the reference
builder, and the verifier. The harness (prompts, loop, golden) is family-
agnostic and dispatches through this table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from toolchain import frame_reference, frame_verify, reference, verify
from toolchain.frame import FRAME_STEP, FrameSpec
from toolchain.spec import BODY_STEP, LID_STEP, EnclosureSpec


@dataclass(frozen=True)
class Family:
    name: str
    spec_cls: type
    spec_sources: tuple[str, ...]  # file names under toolchain/ embedded in prompts
    output_files: tuple[str, ...]
    extract_notes: str  # family-specific rules for the extraction prompt
    design_summary: str  # geometry walkthrough for the codegen prompt
    build: Callable  # reference builder: (spec, out_dir, sabotage=None)
    verify: Callable  # (spec, out_dir) -> Report


ENCLOSURE = Family(
    name="enclosure",
    spec_cls=EnclosureSpec,
    spec_sources=("spec.py",),
    output_files=(BODY_STEP, LID_STEP),
    extract_notes=(
        'Cutouts need a "kind" field: "rect" (center, cz, width, height) or '
        '"circle" (center, cz, diameter). "center" is y for faces "+x"/"-x" '
        'and x for "+y"/"-y". Screw sizes are strings: "M2", "M2.5", "M3", "M4".'
    ),
    design_summary=(
        "Body: outer box length x width x height at the origin; walls `wall` thick on "
        "all four sides, floor `floor` thick, open top. Four corner posts (cylinders, "
        "diameter `post_diameter`) fused inside the corners from the floor top up to "
        "z = height - lid_thickness, each with a pilot hole (SCREWS[screw].pilot "
        "diameter, `screw_depth` deep) drilled down from the post top. Standoffs are "
        "cylinders on the floor at their (x, y) centers, `height` tall, outer diameter "
        "od(), with a pilot hole through their full height. Cutouts pierce the stated "
        "wall completely.\n"
        "Lid: a separate flat plate, lid_size() footprint, from z = height - "
        "lid_thickness to z = height, positioned at x = wall + fit_clearance, "
        "y = wall + fit_clearance (assembled coordinates, NOT at the origin), with a "
        "clearance hole (SCREWS[screw].clearance diameter) through it at each post "
        "center."
    ),
    build=reference.build,
    verify=verify.verify,
)

FRAME = Family(
    name="frame",
    spec_cls=FrameSpec,
    spec_sources=("frame.py",),
    output_files=(FRAME_STEP,),
    extract_notes=(
        'fc_mount is one of "30.5x30.5-M3", "25.5x25.5-M2", "20x20-M2"; motor_mount '
        'is one of "16x16-M3", "19x19-M3", "12x12-M2", "9x9-M2". Wheelbase is the '
        "diagonal motor-to-motor distance in mm. prop_size_inch is the propeller "
        "size when stated."
    ),
    design_summary=(
        "One flat plate, centered at the origin, z = 0 to plate_thickness. Central "
        "square body body_width x body_width. Four rectangular arms (arm_width wide) "
        "from the center along the diagonals (45/135/225/315 degrees) out to radius "
        "wheelbase/2, each ending in a circular motor pad of diameter pad_diameter. "
        "Fuse body + arms + pads, then drill through-holes (overshoot both faces): "
        "4 FC holes at fc_hole_centers(); per motor i: a hub hole "
        "(hub_hole_diameter) at motor_centers()[i] and 4 pattern holes at "
        "motor_hole_centers(i). Hole diameters come from the screw clearance table: "
        "M2 -> 2.4 mm, M2.5 -> 2.9 mm, M3 -> 3.4 mm, M4 -> 4.5 mm (pattern hole "
        "diameter = clearance of the pattern's screw)."
    ),
    build=frame_reference.build,
    verify=frame_verify.verify_frame,
)

FAMILIES: dict[str, Family] = {f.name: f for f in (ENCLOSURE, FRAME)}


def get_family(name: str) -> Family:
    if name not in FAMILIES:
        raise KeyError(f"unknown part family: {name!r} (have: {sorted(FAMILIES)})")
    return FAMILIES[name]
