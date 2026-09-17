"""Bronze landing helpers shared by every extract task.

`land_records` is the one write path into Bronze: it stamps the `_` metadata
columns, hashes the canonical JSON of each payload, and inserts with
ON CONFLICT DO NOTHING against the (_payload_hash, _source) unique index —
so any extract can be re-run safely.

`extract_resource` is the mock-vendor extractor: read the last cursor from an
Airflow Variable, walk `/v1/<resource>?since=...` until `next_cursor` is
null, land each page, and save the cursor after every page so a crash
mid-walk resumes instead of restarting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Iterable

import httpx
from airflow.models import Variable
from airflow.providers.postgres.hooks.postgres import PostgresHook
from psycopg2.extras import Json, execute_values

log = logging.getLogger(__name__)

WAREHOUSE_CONN_ID = "warehouse"
PAGE_SIZE = 500  # the mocks' max


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.md5(canonical.encode()).hexdigest()


def cursor_key(source: str, resource: str) -> str:
    return f"bronze_cursor__{source}__{resource}"


def land_records(
    *,
    source: str,
    endpoint: str,
    records: Iterable[dict[str, Any]],
    batch_id: uuid.UUID,
    schema_version: str | None = None,
    conn_id: str = WAREHOUSE_CONN_ID,
) -> int:
    """Insert records into bronze.<source>; returns how many were actually new."""
    rows = [
        (Json(r), source, endpoint, str(batch_id), payload_hash(r), schema_version)
        for r in records
    ]
    if not rows:
        return 0
    hook = PostgresHook(postgres_conn_id=conn_id)
    with hook.get_conn() as conn, conn.cursor() as cur:
        inserted = execute_values(
            cur,
            f"""
            INSERT INTO bronze.{source}
                (payload, _source, _endpoint, _batch_id, _payload_hash, _schema_version)
            VALUES %s
            ON CONFLICT (_payload_hash, _source) DO NOTHING
            RETURNING id
            """,
            rows,
            page_size=PAGE_SIZE,
            fetch=True,
        )
        conn.commit()
    return len(inserted)


def extract_resource(
    *,
    source: str,
    base_url: str,
    resource: str,
    conn_id: str = WAREHOUSE_CONN_ID,
    page_size: int = PAGE_SIZE,
) -> dict[str, Any]:
    """Walk one mock-vendor resource from the saved cursor to the end of the stream."""
    batch_id = uuid.uuid4()
    endpoint = f"/v1/{resource}"
    key = cursor_key(source, resource)
    cursor = Variable.get(key, default_var=None)
    stats = {"source": source, "endpoint": endpoint, "batch_id": str(batch_id),
             "pages": 0, "fetched": 0, "inserted": 0,
             "cursor_before": cursor, "cursor_after": cursor}

    with httpx.Client(base_url=base_url, timeout=30) as client:
        while True:
            params = {"limit": page_size}
            if cursor:
                params["since"] = cursor
            body = client.get(endpoint, params=params).raise_for_status().json()
            data = body["data"]
            if not data:
                break  # next_cursor is null here; keep the last real cursor
            stats["pages"] += 1
            stats["fetched"] += len(data)
            stats["inserted"] += land_records(
                source=source, endpoint=endpoint, records=data, batch_id=batch_id,
                schema_version=body.get("schema_version"), conn_id=conn_id,
            )
            cursor = body["next_cursor"]
            Variable.set(key, cursor)
            stats["cursor_after"] = cursor

    log.info("%s%s: %d pages, %d fetched, %d new", source, endpoint,
             stats["pages"], stats["fetched"], stats["inserted"])
    return stats
