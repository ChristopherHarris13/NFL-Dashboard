import pytest
from pydantic import TypeAdapter

from mock_vendors.common.clean_schemas import NutritionMeasurement
from mock_vendors.nutrition_svc.app import build_vendor
from mock_vendors.testing import CLEAN, make_client, make_settings, walk

SERVICE = "nutrition_svc"


@pytest.fixture(scope="module")
def default_client(tmp_path_factory):
    return make_client(build_vendor, make_settings(SERVICE, tmp_path_factory.mktemp("cfg")))


@pytest.fixture(scope="module")
def clean_client(tmp_path_factory):
    return make_client(build_vendor, make_settings(SERVICE, tmp_path_factory.mktemp("cfg"), dirt=CLEAN))


def test_health(default_client):
    body = default_client.get("/health").json()
    assert body["status"] == "ok" and body["service"] == SERVICE and body["records"] > 0


def test_pagination_walks_full_stream_without_gaps_or_repeats(default_client):
    small = walk(default_client, "measurements", limit=53)
    big = walk(default_client, "measurements", limit=500)
    assert small == big
    assert len(small) == default_client.get("/health").json()["records"]


def test_clean_config_passes_clean_schema(clean_client):
    records = walk(clean_client, "measurements", limit=500)
    assert records
    adapter = TypeAdapter(NutritionMeasurement)
    for r in records:
        adapter.validate_python(r)


def test_default_dirt_visibly_present(default_client, clean_client):
    records = walk(default_client, "measurements", limit=500)
    clean_names = {r["player_name"] for r in walk(clean_client, "measurements", limit=500)}
    # name mangling produces names outside the canonical roster spellings
    assert any(r["player_name"] not in clean_names for r in records)
    # kg-swapped weights (numerically < 150) and omitted unit labels
    assert any(r["weight"] < 150 for r in records)
    assert any("weight_unit" not in r for r in records)
    # lean_mass emitted as a percent with no indicator
    assert any(r["lean_mass"] < 100 for r in records)
    # scheduled weigh-ins skipped: fewer scale rows than players x Wednesdays
    scale_rows = [r for r in records if r["method"] == "scale"]
    clean_scale = [r for r in walk(clean_client, "measurements", limit=500)
                   if r["method"] == "scale"]
    assert len(scale_rows) < len(clean_scale)


def test_method_disagreement_same_day(tmp_path):
    settings = make_settings(SERVICE, tmp_path,
                             dirt={SERVICE: {"method_disagreement": {"p": 1.0}}})
    client = make_client(build_vendor, settings)
    records = walk(client, "measurements", limit=500)
    by_day = {}
    for r in records:
        by_day.setdefault((r["player_name"], r["measured_on"]), []).append(r)
    pairs = [v for v in by_day.values()
             if {x["method"] for x in v} >= {"DEXA", "BIA"}]
    assert pairs
    for pair in pairs:
        dexa = next(x for x in pair if x["method"] == "DEXA")
        bia = next(x for x in pair if x["method"] == "BIA")
        assert abs(dexa["body_fat_pct"] - bia["body_fat_pct"]) >= 2.5


def test_no_id_field_ever(default_client):
    records = walk(default_client, "measurements", limit=500)
    for r in records:
        assert not any(k.endswith("_id") and k != "measurement_id" for k in r)
