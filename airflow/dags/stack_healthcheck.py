"""Smoke test for the stack wiring: every mock vendor answers /health and the
warehouse has its medallion schemas. Manual trigger only; run it after
`docker compose up` to confirm Airflow can reach both sides of the pipeline.
"""

from __future__ import annotations

import httpx
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task

VENDORS = {
    "catapult_svc": 8001,
    "forcedeck_svc": 8002,
    "ams_wellness_svc": 8003,
    "nutrition_svc": 8004,
    "emr_svc": 8005,
}


@dag(schedule=None, catchup=False, tags=["ops"])
def stack_healthcheck():
    @task
    def vendor_health(service: str, port: int) -> dict:
        body = httpx.get(f"http://{service}:{port}/health", timeout=10).raise_for_status().json()
        assert body["status"] == "ok", body
        return body

    @task
    def warehouse_schemas() -> list[str]:
        hook = PostgresHook(postgres_conn_id="warehouse")
        rows = hook.get_records(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name IN ('bronze', 'silver', 'gold', 'meta') ORDER BY 1"
        )
        found = [r[0] for r in rows]
        assert found == ["bronze", "gold", "meta", "silver"], found
        return found

    vendor_health.expand_kwargs([{"service": s, "port": p} for s, p in VENDORS.items()])
    warehouse_schemas()


stack_healthcheck()
