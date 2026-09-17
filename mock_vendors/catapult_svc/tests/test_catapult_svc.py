import pytest
from pydantic import TypeAdapter

from mock_vendors.catapult_svc.app import build_vendor
from mock_vendors.common.clean_schemas import CatapultAthlete, CatapultSession
from mock_vendors.testing import CLEAN, make_client, make_settings, walk

SERVICE = "catapult_svc"


@pytest.fixture(scope="module")
def default_client(tmp_path_factory):
    settings = make_settings(SERVICE, tmp_path_factory.mktemp("cfg"))
    return make_client(build_vendor, settings)


@pytest.fixture(scope="module")
def clean_client(tmp_path_factory):
    settings = make_settings(SERVICE, tmp_path_factory.mktemp("cfg"), dirt=CLEAN)
    return make_client(build_vendor, settings)


def test_health(default_client):
    body = default_client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == SERVICE
    assert body["records"] > 0


def test_pagination_walks_full_stream_without_gaps_or_repeats(default_client):
    small = walk(default_client, "sessions", limit=73)
    big = walk(default_client, "sessions", limit=500)
    assert small == big
    athletes = walk(default_client, "athletes", limit=500)
    assert len(small) + len(athletes) == default_client.get("/health").json()["records"]


def test_clean_config_passes_clean_schema(clean_client):
    records = walk(clean_client, "sessions", limit=500)
    assert records
    adapter = TypeAdapter(CatapultSession)
    for r in records:
        adapter.validate_python(r)


def test_default_dirt_visibly_present(default_client):
    records = walk(default_client, "sessions", limit=500)
    assert len(records) >= 1000
    # unit labels: correct, stale, and absent all occur
    assert any("distance_unit" not in r for r in records)
    assert {r.get("distance_unit") for r in records} >= {"m", "yd"}
    # nulled player_load
    assert any(r["player_load"] is None for r in records)
    # impossible distances
    assert any(r["total_distance"] >= 30000 for r in records)
    # late emissions: event dates are NOT monotone in the emission stream
    dates = [r["session_ts"][:10] for r in records]
    assert any(a > b for a, b in zip(dates, dates[1:]))


def test_unit_swap_forced_always_converts(tmp_path):
    settings = make_settings(
        SERVICE, tmp_path,
        dirt={SERVICE: {"unit_swap_distance": {"p": 1.0}}},
    )
    client = make_client(build_vendor, settings)
    records = walk(client, "sessions", limit=500)
    labels = {r.get("distance_unit", "<absent>") for r in records}
    assert len(labels) >= 2  # stale 'm', honest 'yd', and/or absent


def test_vendor_ids_never_expose_gsis(default_client):
    records = walk(default_client, "sessions", limit=500)
    assert all(r["player_id"].startswith("cat_") for r in records)
    athletes = walk(default_client, "athletes", limit=500)
    assert athletes and not any("gsis" in k for a in athletes for k in a)


def test_athletes_cover_every_session_player(default_client, clean_client):
    athletes = walk(default_client, "athletes", limit=500)
    ids = {a["athlete_id"] for a in athletes}
    assert len(ids) == len(athletes)  # emitted once, no dupes
    sessions = walk(default_client, "sessions", limit=500)
    assert {s["player_id"] for s in sessions} <= ids
    adapter = TypeAdapter(CatapultAthlete)
    for a in walk(clean_client, "athletes", limit=500):
        adapter.validate_python(a)
