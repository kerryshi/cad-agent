"""Profile-resolution tests — no slicer needed.

`resolve()` promises to refuse rather than degrade: a partially-resolved
profile is the exact silent failure the module exists to prevent, so both
refusal paths need refuse-first evidence (CLAUDE.md).
"""

import json

import pytest

from toolchain import profiles


def test_resolve_flattens_the_chain():
    """Values from every level of a real chain must survive."""
    flat = profiles.resolve("filament", "Bambu PETG Basic @BBL P2S 0.4 nozzle.json")
    assert flat["filament_type"] == ["PETG"]  # root-level, 3 deep
    assert flat["textured_plate_temp"] == ["70"]  # @base, 2 deep
    assert flat["nozzle_temperature"] == ["250", "250"]  # leaf
    assert "inherits" not in flat, "inherits must not survive into the flat file"


def test_resolve_refuses_missing_parent(tmp_path, monkeypatch):
    """A broken chain must raise, never yield a partial profile."""
    fake = tmp_path / "filament"
    fake.mkdir()
    (fake / "orphan.json").write_text(
        json.dumps({"type": "filament", "inherits": "does_not_exist",
                    "filament_type": ["PETG"]}), encoding="utf-8",
    )
    monkeypatch.setattr(profiles, "profile_dir", lambda kind: fake)
    with pytest.raises(FileNotFoundError, match="does_not_exist"):
        profiles.resolve("filament", "orphan")


def test_resolve_refuses_circular_inherits(tmp_path, monkeypatch):
    """A cycle must raise instead of looping forever."""
    fake = tmp_path / "filament"
    fake.mkdir()
    (fake / "a.json").write_text(
        json.dumps({"type": "filament", "inherits": "b"}), encoding="utf-8")
    (fake / "b.json").write_text(
        json.dumps({"type": "filament", "inherits": "a"}), encoding="utf-8")
    monkeypatch.setattr(profiles, "profile_dir", lambda kind: fake)
    with pytest.raises(ValueError, match="circular"):
        profiles.resolve("filament", "a")


def test_resolve_refuses_ambiguous_stem(tmp_path, monkeypatch):
    """Two parents with the same stem must not be silently picked between."""
    fake = tmp_path / "filament"
    (fake / "nested").mkdir(parents=True)
    body = json.dumps({"type": "filament"})
    (fake / "dup.json").write_text(body, encoding="utf-8")
    (fake / "nested" / "dup.json").write_text(body, encoding="utf-8")
    monkeypatch.setattr(profiles, "profile_dir", lambda kind: fake)
    # direct hit wins; ambiguity only bites via the rglob fallback
    (fake / "dup.json").unlink()
    (fake / "nested2").mkdir()
    (fake / "nested2" / "dup.json").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="ambiguous"):
        profiles.resolve("filament", "dup")


def test_bed_type_refuses_unsupported_plate(monkeypatch):
    """A plate the vendor marks unsupported must be refused by name."""
    data = profiles._descriptor()
    unsupported = data["not_support_bed_type"].split(";")[0].strip()
    monkeypatch.setattr(profiles, "_BED_OVERRIDE", unsupported)
    with pytest.raises(ValueError, match="does not support"):
        profiles.bed_type()


def test_bed_type_defaults_to_the_machine_descriptor(monkeypatch):
    monkeypatch.setattr(profiles, "_BED_OVERRIDE", None)
    assert profiles.bed_type() == profiles._descriptor()["default_bed_type"]


def test_expected_bed_temps_refuses_unsupported_combination(monkeypatch):
    """PETG on a Cool Plate resolves to 0 C — refuse, don't slice it."""
    monkeypatch.setattr(profiles, "_BED_OVERRIDE", "Cool Plate")
    with pytest.raises(ValueError, match="unsupported"):
        profiles.expected_bed_temps()
