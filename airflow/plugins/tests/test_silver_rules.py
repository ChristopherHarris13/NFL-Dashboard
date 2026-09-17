"""Unit tests for the pure rules inside the Silver transforms."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from silver.catapult import normalise_distance, normalise_speed
from silver.common import parse_facility_local
from silver.emr import parse_body_part
from silver.forcedeck import normalise_force
from silver.nutrition import infer_weight_unit
from silver.wellness import rescale, shape_of


# ------------------------------------------------------------- forcedeck

@pytest.mark.parametrize("value,unit,expected_n,inferred", [
    (2500.0, "N", 2500.0, False),
    (562.0, "lbf", 2499.9, False),
    (562.0, None, 2499.9, True),      # no label, <1500 -> lbf
    (2500.0, None, 2500.0, True),     # no label, >=1500 -> already N
])
def test_force_normalisation(value, unit, expected_n, inferred):
    n, inf = normalise_force(value, unit)
    assert n == pytest.approx(expected_n, abs=0.1) and inf is inferred


# ------------------------------------------------------------- nutrition

@pytest.mark.parametrize("value,label,lean,roster,expected", [
    (95.0, "kg", None, None, ("kg", "label")),          # honest kg label
    (95.0, "lb", None, None, ("kg", "magnitude")),      # stale lb label on a kg value
    (310.0, None, None, None, ("lb", "magnitude")),     # no label, only lb is possible
    (136.0, "lb", 250.0, None, ("kg", "lean_mass")),    # 136 'lb' below 250 lb lean mass -> kg
    (150.0, "lb", None, 330.0, ("kg", "roster")),       # 150 kg ~ 330 lb roster
    (150.0, "lb", None, 155.0, ("lb", "roster")),       # 150 lb ~ 155 lb roster
    (150.0, "lb", None, None, ("lb", "label")),         # nothing else to go on
    (150.0, None, None, None, ("lb", "default")),
])
def test_weight_unit_inference(value, label, lean, roster, expected):
    assert infer_weight_unit(value, label, lean, roster) == expected


# -------------------------------------------------------------- wellness

def test_rescale_maps_endpoints_and_midpoint():
    assert rescale(1, 5) == 1 and rescale(5, 5) == 10 and rescale(3, 5) == 5.5
    assert rescale(7, 10) == 7.0 and rescale(None, 5) is None


def test_shape_from_keys_not_envelope():
    assert shape_of({"player_id": 1, "name": "x"}) == "v1"
    assert shape_of({"athlete_id": 1, "first_name": "x", "scale_max": 10}) == "v2"


def test_facility_local_timestamps_are_eastern():
    ts, corrected = parse_facility_local("2026-09-10 07:15:00")
    assert ts == datetime(2026, 9, 10, 11, 15, tzinfo=timezone.utc) and corrected is False
    # A bogus +00:00 on a local time is treated as ET and flagged.
    ts, corrected = parse_facility_local("2026-09-10 07:15:00+00:00")
    assert ts == datetime(2026, 9, 10, 11, 15, tzinfo=timezone.utc) and corrected is True
    # Winter: -05:00
    ts, _ = parse_facility_local("2026-12-10 07:15:00")
    assert ts == datetime(2026, 12, 10, 12, 15, tzinfo=timezone.utc)


# ------------------------------------------------------------------- emr

@pytest.mark.parametrize("raw,side_field,expected", [
    ("hamstring", "L", ("hamstring", "L", "field")),
    ("hammy", None, ("hamstring", None, None)),
    ("Knee (R)", None, ("knee", "R", "text")),
    ("L mcl", None, ("knee", "L", "text")),
    ("left ac joint", None, ("shoulder", "L", "text")),
    ("high ankle", "R", ("ankle", "R", "field")),
    ("concussion protocol", None, ("concussion", None, None)),
    ("head", None, ("concussion", None, None)),
    ("elbow", None, (None, None, None)),
])
def test_body_part_and_side_parsing(raw, side_field, expected):
    assert parse_body_part(raw, side_field) == expected


# -------------------------------------------------------------- catapult

def test_speed_unit_by_magnitude_overrides_label():
    assert normalise_speed(8.5, "m/s") == (8.5, False)
    assert normalise_speed(19.0, "mph") == (pytest.approx(8.49, abs=0.01), False)
    assert normalise_speed(19.0, "m/s") == (pytest.approx(8.49, abs=0.01), True)   # stale label
    assert normalise_speed(19.0, None) == (pytest.approx(8.49, abs=0.01), True)


def test_distance_trusts_yd_label_and_flags_missing():
    assert normalise_distance(1000.0, "yd") == (pytest.approx(914.4), False)
    assert normalise_distance(1000.0, "m") == (1000.0, False)
    assert normalise_distance(1000.0, None) == (1000.0, True)
