"""Offscreen rendering must produce non-blank images — a blank frame is the
documented silent-failure mode of headless GL, so blankness is the assertion."""

import numpy as np
from PIL import Image

from tests.test_verify import make_spec
from toolchain.reference import build
from toolchain.render import render_workdir


def test_render_workdir_produces_nonblank_views(tmp_path):
    spec = make_spec()
    build(spec, tmp_path)
    written = render_workdir(tmp_path)
    names = {p.name for p in written}
    assert {"body_iso_ne.png", "body_top.png", "lid_top.png"} <= names
    for p in written:
        arr = np.asarray(Image.open(p).convert("L"))
        assert arr.std() > 5, f"{p.name} rendered blank (std={arr.std():.2f})"


def test_render_missing_files_is_graceful(tmp_path):
    assert render_workdir(tmp_path) == []
