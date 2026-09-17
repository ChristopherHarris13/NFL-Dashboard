from collections import Counter

import pytest
from pydantic import TypeAdapter

from mock_vendors.common.clean_schemas import ForcedeckTest
from mock_vendors.forcedeck_svc.app import build_vendor
from mock_vendors.testing import CLEAN, make_client, make_settings, walk

SERVICE = "forcedeck_svc"


@pytest.fixture(scope="module")
def default_client(tmp_path_factory):
    return make_client(build_vendor, make_settings(SERVICE, tmp_path_factory.mktemp("cfg")))


@pytest.fixture(scope="module")
def clean_client(tmp_path_factory):
    return make_client(build_vendor, make_settings(SERVICE, tmp_path_factory.mktemp("cfg"), dirt=CLEAN))


def test_health(default_client):
    body = default_client.get("/health").json()
    assert body == {"status": "ok", "service": SERVICE, "records": body["records"]}
    assert body["records"] > 0


def test_pagination_walks_full_stream_without_gaps_or_repeats(default_client):
    small = walk(default_client, "tests", limit=61)
    big = walk(default_client, "tests", limit=500)
    assert small == big
    assert len(small) == default_client.get("/health").json()["records"]


def test_clean_config_passes_clean_schema(clean_client):
    records = walk(clean_client, "tests", limit=500)
    assert records
    adapter = TypeAdapter(ForcedeckTest)
    for r in records:
        adapter.validate_python(r)


def test_duplicate_p1_yields_at_least_two_copies(tmp_path):
    settings = make_settings(SERVICE, tmp_path, dirt={SERVICE: {"duplicate": {"p": 1.0}}})
    client = make_client(build_vendor, settings)
    records = walk(client, "tests", limit=500)
    counts = Counter(r["test_id"] for r in records)
    assert counts and all(c >= 2 for c in counts.values())


def test_default_dirt_visibly_present(default_client):
    records = walk(default_client, "tests", limit=500)
    counts = Counter(r["test_id"] for r in records)
    assert any(c >= 2 for c in counts.values())               # duplicates
    assert any("force_unit" not in r for r in records)        # omitted unit
    assert any(r["test_type"] is None for r in records)       # nulled type
    assert any("device_id" not in r for r in records)         # dropped device
    aborted = [r for r in records
               if r["jump_height_cm"] is not None
               and (r["jump_height_cm"] < 5 or r["jump_height_cm"] > 90)]
    assert aborted


def test_imtp_has_null_jump_height(clean_client):
    records = walk(clean_client, "tests", limit=500)
    imtp = [r for r in records if r["test_type"] == "IMTP"]
    assert imtp and all(r["jump_height_cm"] is None for r in imtp)
