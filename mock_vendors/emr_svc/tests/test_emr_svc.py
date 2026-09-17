from collections import Counter

import pytest
from pydantic import TypeAdapter

from mock_vendors.common.clean_schemas import EmrInjury, EmrStatusUpdate
from mock_vendors.emr_svc.app import build_vendor
from mock_vendors.testing import CLEAN, make_client, make_settings, walk

SERVICE = "emr_svc"


@pytest.fixture(scope="module")
def default_client(tmp_path_factory):
    return make_client(build_vendor, make_settings(SERVICE, tmp_path_factory.mktemp("cfg")))


@pytest.fixture(scope="module")
def clean_client(tmp_path_factory):
    return make_client(build_vendor, make_settings(SERVICE, tmp_path_factory.mktemp("cfg"), dirt=CLEAN))


def test_health(default_client):
    body = default_client.get("/health").json()
    assert body["status"] == "ok" and body["service"] == SERVICE and body["records"] > 0


def test_pagination_both_resources(default_client):
    total = 0
    for resource in ("injuries", "status_updates"):
        small = walk(default_client, resource, limit=7)
        big = walk(default_client, resource, limit=500)
        assert small == big
        total += len(small)
    assert total == default_client.get("/health").json()["records"]


def test_clean_config_passes_clean_schemas(clean_client):
    injuries = walk(clean_client, "injuries", limit=500)
    updates = walk(clean_client, "status_updates", limit=500)
    assert injuries and updates
    for r in injuries:
        TypeAdapter(EmrInjury).validate_python(r)
    for r in updates:
        TypeAdapter(EmrStatusUpdate).validate_python(r)


def test_updates_reference_known_injuries(clean_client):
    injury_ids = {r["injury_id"] for r in walk(clean_client, "injuries", limit=500)}
    for r in walk(clean_client, "status_updates", limit=500):
        assert r["injury_id"] in injury_ids


def test_default_dirt_visibly_present(default_client):
    injuries = walk(default_client, "injuries", limit=500)
    updates = walk(default_client, "status_updates", limit=500)
    # free-text body parts: more spellings than canonical terms
    assert len({r["body_part"] for r in injuries}) > 5
    # side sometimes embedded in the free text instead of its own field
    assert any("side" not in r for r in injuries)


def test_correction_overwrite_reemits_update_id(tmp_path):
    settings = make_settings(SERVICE, tmp_path,
                             dirt={SERVICE: {"correction_overwrite": {"p": 1.0}}})
    client = make_client(build_vendor, settings)
    updates = walk(client, "status_updates", limit=500)
    counts = Counter(r["update_id"] for r in updates)
    dupes = [uid for uid, c in counts.items() if c >= 2]
    assert dupes
    for uid in dupes:
        copies = [r for r in updates if r["update_id"] == uid]
        assert len({r["practice_status"] for r in copies}) > 1
        assert len({r["updated_at"] for r in copies}) > 1


def test_out_of_order_updates(tmp_path):
    settings = make_settings(SERVICE, tmp_path,
                             dirt={SERVICE: {"out_of_order_updates": {"p": 1.0}}})
    client = make_client(build_vendor, settings)
    updates = walk(client, "status_updates", limit=500)
    stamps = [r["updated_at"] for r in updates]
    assert any(a > b for a, b in zip(stamps, stamps[1:]))
