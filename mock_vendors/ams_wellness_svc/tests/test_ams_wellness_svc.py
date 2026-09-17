from collections import Counter
from datetime import date

import pytest
from pydantic import TypeAdapter

from mock_vendors.ams_wellness_svc.app import build_vendor
from mock_vendors.common.clean_schemas import WellnessSurvey
from mock_vendors.testing import CLEAN, make_client, make_settings, walk

SERVICE = "ams_wellness_svc"


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
    small = walk(default_client, "surveys", limit=97)
    big = walk(default_client, "surveys", limit=500)
    assert small == big
    assert len(small) == default_client.get("/health").json()["records"]


def test_clean_config_passes_clean_schema(clean_client):
    records = walk(clean_client, "surveys", limit=500)
    assert records
    adapter = TypeAdapter(WellnessSurvey)
    for r in records:
        adapter.validate_python(r)


def _survey_key(record):
    pid = record.get("player_id", record.get("athlete_id"))
    return pid, record["submitted_at"][:10]


def test_duplicate_submission_p1_yields_two_copies_per_day(tmp_path):
    settings = make_settings(SERVICE, tmp_path,
                             dirt={SERVICE: {"duplicate_submission": {"p": 1.0}}})
    client = make_client(build_vendor, settings)
    records = walk(client, "surveys", limit=500)
    counts = Counter(_survey_key(r) for r in records)
    assert counts and all(c == 2 for c in counts.values())


def test_scale_silently_changes(clean_client):
    records = walk(clean_client, "surveys", limit=500)
    before = [r for r in records if r["submitted_at"][:10] < "2026-08-25"]
    after = [r for r in records if r["submitted_at"][:10] >= "2026-08-25"]
    assert before and after
    assert all(r["soreness"] <= 5 for r in before)
    assert any(r["soreness"] > 5 for r in after)
    # ...and nothing in a v1 record admits it happened
    assert all("scale_max" not in r for r in before)


def test_schema_drift_renames_and_splits(clean_client):
    records = walk(clean_client, "surveys", limit=500)
    v1 = [r for r in records if r["submitted_at"][:10] < "2026-09-01"]
    v2 = [r for r in records if r["submitted_at"][:10] >= "2026-09-01"]
    assert v1 and v2
    assert all("player_id" in r and "name" in r for r in v1)
    assert all("athlete_id" in r and "first_name" in r and "last_name" in r
               and r["scale_max"] == 10 for r in v2)


def test_response_schema_version_is_v2_after_drift(clean_client):
    body = clean_client.get("/v1/surveys", params={"limit": 1}).json()
    assert body["schema_version"] == "v2"  # sim frozen at day 44 = 2026-09-02


def test_srpe_null_on_off_days(clean_client):
    records = walk(clean_client, "surveys", limit=500)
    for r in records:
        d = date.fromisoformat(r["submitted_at"][:10])
        if d.weekday() in (0, 1):  # Mon/Tue off
            assert r["srpe"] is None
        elif d.weekday() in (2, 3, 4, 6):
            assert r["srpe"] is not None
