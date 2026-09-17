"""Shared helpers for the per-service pytest suites."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from .common.settings import DEFAULT_DIRT_CONFIG, Settings

CLEAN = "clean"      # every dirt probability 0.0
DEFAULT = "default"  # repo dirt_config.yaml


def make_settings(service_name: str, tmp_path: Path, dirt=DEFAULT, **overrides) -> Settings:
    if dirt == DEFAULT:
        dirt_path = DEFAULT_DIRT_CONFIG
    else:
        dirt_path = tmp_path / "dirt.yaml"
        dirt_path.write_text(yaml.safe_dump({} if dirt == CLEAN else dirt))
    settings = Settings(
        service_name=service_name,
        port=0,
        # Huge interval freezes the sim at the last backfill day, so tests
        # are deterministic regardless of wall time.
        gen_interval_seconds=10**9,
        dirt_config_path=dirt_path,
        run_background_loop=False,
        **overrides,
    )
    return settings


def make_client(build_vendor, settings: Settings) -> TestClient:
    from .common.base_service import create_app

    vendor = build_vendor(settings)
    # TestClient only runs lifespan inside a `with` block; backfill here so
    # plain requests see the full stream either way.
    vendor.catch_up()
    return TestClient(create_app(vendor))


def walk(client: TestClient, resource: str, limit: int = 100) -> list[dict]:
    """Walk the cursor from the beginning to exhaustion."""
    out: list[dict] = []
    cursor = None
    while True:
        params = {"limit": limit}
        if cursor:
            params["since"] = cursor
        resp = client.get(f"/v1/{resource}", params=params)
        resp.raise_for_status()
        body = resp.json()
        if not body["data"]:
            assert body["next_cursor"] is None
            return out
        out.extend(body["data"])
        cursor = body["next_cursor"]
