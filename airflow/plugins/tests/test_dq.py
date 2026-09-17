"""Unit tests for the DQ layer's pure parts and for the checked-in suites."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
os.environ["GX_SUITES_DIR"] = str(REPO / "great_expectations" / "expectations")

from dq import validate as v  # noqa: E402  (needs the env var above)

SUITE_FILES = sorted((REPO / "great_expectations" / "expectations").glob("*.json"))
STAGING_SQL = (REPO / "warehouse" / "init" / "04_staging.sql").read_text()


# ------------------------------------------------------------ the suites

def test_every_source_has_a_suite():
    sources = {json.loads(p.read_text())["meta"]["source"] for p in SUITE_FILES}
    assert sources == {"forcedeck", "catapult", "ams_wellness", "nutrition", "emr"}


@pytest.mark.parametrize("path", SUITE_FILES, ids=[p.stem for p in SUITE_FILES])
def test_suite_is_well_formed(path):
    doc = json.loads(path.read_text())
    assert doc["name"] == path.stem
    assert 7 <= len(doc["expectations"]) <= 12, "keep suites focused: 8-12 expectations"
    assert f"silver.{doc['meta']['table']} AS" in STAGING_SQL, "suite must target a staging view"
    for e in doc["expectations"]:
        assert e["type"].startswith("expect_") and isinstance(e["kwargs"], dict)
        assert "column" in e["kwargs"] or {"column_A", "column_B"} <= e["kwargs"].keys()


def test_range_checks_are_in_canonical_units():
    # Conversion happens in the staging view; suites must check the converted column.
    fd = json.loads((REPO / "great_expectations/expectations/forcedeck.json").read_text())
    cols = {e["kwargs"].get("column") for e in fd["expectations"]}
    assert "peak_force_n" in cols and "peak_force_raw" not in cols
    nu = json.loads((REPO / "great_expectations/expectations/nutrition.json").read_text())
    cols = {e["kwargs"].get("column") for e in nu["expectations"]}
    assert "weight_kg" in cols and "weight_raw" not in cols


def test_load_suites_filters_by_source():
    assert [s["name"] for s in v.load_suites("emr")] == ["emr_injuries", "emr_status_updates"]
    assert v.load_suites("nope") == []


# ---------------------------------------------------------- readable text

def test_describe_and_observed_are_human_readable():
    kw = {"column": "jump_height_cm", "min_value": 5, "max_value": 90}
    assert v.describe("expect_column_values_to_be_between", kw) == "jump_height_cm outside 5..90"
    assert v.observed({"bronze_id": 1, "jump_height_cm": 113.8}, kw) == "jump_height_cm=113.8"
    from decimal import Decimal
    assert v.observed({"jump_height_cm": Decimal("96.2")}, kw) == "jump_height_cm=96.2"
    assert v.observed({"test_type": None}, {"column": "test_type"}) == "test_type=null"
    pair = {"column_A": "expected_rtp", "column_B": "event_date"}
    assert v.describe("expect_column_pair_values_a_to_be_greater_than_b", pair) == "expected_rtp < event_date"
    assert v.observed({"expected_rtp": "2026-08-01", "event_date": "2026-08-05"}, pair) == \
        "expected_rtp=2026-08-01, event_date=2026-08-05"


# --------------------------------------------------------- systemic rule

def test_systemic_threshold_is_half_the_batch():
    assert v.SYSTEMIC_UNEXPECTED_PCT == 50.0


def test_unexpected_rows_uses_gx_query_when_list_is_capped():
    class Cur:
        description = [("bronze_id",), ("x",)]
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, q): self.q = q
        def fetchall(self): return [(1, "a"), (2, "b"), (3, "c")]
    class Conn:
        def cursor(self): return Cur()
    capped = {"unexpected_count": 3, "unexpected_index_list": [{"bronze_id": 1, "x": "a"}],
              "unexpected_index_query": "SELECT bronze_id, x FROM t WHERE ..."}
    assert v.unexpected_rows(Conn(), capped) == [{"bronze_id": 1, "x": "a"}, {"bronze_id": 2, "x": "b"}, {"bronze_id": 3, "x": "c"}]
    full = {"unexpected_count": 1, "unexpected_index_list": [{"bronze_id": 9, "x": "z"}]}
    assert v.unexpected_rows(Conn(), full) == [{"bronze_id": 9, "x": "z"}]
