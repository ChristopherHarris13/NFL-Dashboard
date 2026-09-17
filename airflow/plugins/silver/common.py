"""Shared plumbing for the Silver transforms."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from psycopg2.extras import Json, execute_values

from identity import PlayerResolver

log = logging.getLogger(__name__)

WAREHOUSE_CONN_ID = "warehouse"
ET = ZoneInfo("America/New_York")

# Unit factors (same constants the vendors use, so round-trips are exact).
M_PER_YD = 0.9144
MPH_PER_MS = 2.2369362920544
N_PER_LBF = 4.4482216152605
KG_PER_LB = 0.45359237


def warehouse_conn():
    from airflow.providers.postgres.hooks.postgres import PostgresHook
    return PostgresHook(postgres_conn_id=WAREHOUSE_CONN_ID).get_conn()


# ------------------------------------------------------------------ bronze

def fetch_bronze(conn, source: str, endpoint: str | None = None) -> list[dict[str, Any]]:
    """All Bronze rows for a source (optionally one endpoint), oldest ingest first."""
    sql = f"SELECT id, payload, _endpoint, _ingested_at, _batch_id, _schema_version FROM bronze.{source}"
    params: list[Any] = []
    if endpoint:
        sql += " WHERE _endpoint = %s"
        params.append(endpoint)
    sql += " ORDER BY _ingested_at, id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def latest_per_key(rows: list[dict[str, Any]], key) -> tuple[list[dict[str, Any]], int]:
    """Dedupe: keep the last row per key. Rows are ingest-ordered (see
    fetch_bronze), so the latest _ingested_at wins, ties broken by Bronze id.
    Returns (survivors in original order, dropped_count)."""
    kept: dict[Any, dict[str, Any]] = {}
    for r in rows:
        kept[key(r)] = r
    return list(kept.values()), len(rows) - len(kept)


# ---------------------------------------------------------------- resolver

def load_resolver(conn) -> PlayerResolver:
    with conn.cursor() as cur:
        cur.execute("""SELECT player_sk, gsis_id, nfl_id, clean_name, full_name, team,
                              first_name, last_name, football_name
                       FROM silver.dim_player_master WHERE valid_to IS NULL""")
        players = []
        for r in cur.fetchall():
            d = dict(zip(("player_sk", "gsis_id", "nfl_id", "clean_name", "full_name", "team",
                          "first_name", "last_name", "football_name"), r))
            # Legal first name (EMR style) and football name are both aliases.
            d["alt_names"] = [f"{fn} {d['last_name']}" for fn in (d["first_name"], d["football_name"])
                              if fn and d["last_name"]]
            players.append(d)
        cur.execute("SELECT vendor, vendor_player_id, player_sk, resolved_by, confidence FROM silver.vendor_player_map")
        vmap = [dict(zip(("vendor", "vendor_player_id", "player_sk", "resolved_by", "confidence"), r))
                for r in cur.fetchall()]
    return PlayerResolver(players, vmap)


def persist_resolver(conn, resolver: PlayerResolver, vendor: str) -> dict[str, int]:
    """Write what this run learned: new vendor_player_map pins, and this
    vendor's quarantine (replaced wholesale so it reflects the current state)."""
    with conn.cursor() as cur:
        pins = [(v, vid, sk, method, conf)
                for (v, vid), (sk, method, conf) in resolver.new_map_entries.items() if v == vendor]
        if pins:
            execute_values(cur, """
                INSERT INTO silver.vendor_player_map (vendor, vendor_player_id, player_sk, resolved_by, confidence)
                VALUES %s
                ON CONFLICT (vendor, vendor_player_id) DO UPDATE SET last_seen = now()""", pins)
        cur.execute("DELETE FROM silver.quarantine_identity WHERE vendor = %s", (vendor,))
        qrows = [(q.vendor, q.vendor_player_id, q.raw_name, q.raw_team, q.reason,
                  list(q.candidates) or None, q.occurrences,
                  (q.example_context or {}).get("bronze_id"))
                 for q in resolver.quarantine.values() if q.vendor == vendor]
        if qrows:
            execute_values(cur, """
                INSERT INTO silver.quarantine_identity
                    (vendor, vendor_player_id, raw_name, raw_team, reason, candidate_sks, occurrences, example_bronze_id)
                VALUES %s""", qrows)
    return {"new_map_entries": len(pins), "quarantined_identifiers": len(qrows)}


# ------------------------------------------------------------------ write

def replace_table(conn, table: str, columns: Sequence[str], rows: list[tuple]) -> int:
    """Full refresh: TRUNCATE + INSERT, in the caller's transaction."""
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE silver.{table}")
        if rows:
            execute_values(cur, f"INSERT INTO silver.{table} ({', '.join(columns)}) VALUES %s",
                           rows, page_size=1000)
    return len(rows)


# ---------------------------------------------------------------- parsing

def parse_iso_utc(s: str) -> datetime:
    """'2026-09-10T15:42:00Z' or with an explicit offset -> aware UTC."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_epoch_utc(v: int | float | str) -> datetime:
    return datetime.fromtimestamp(int(float(v)), tz=timezone.utc)


def parse_facility_local(s: str) -> tuple[datetime, bool]:
    """Wellness timestamps are facility-local (ET) whether or not the payload
    says so; '+00:00' is a known lie. Returns (UTC, tz_was_corrected)."""
    corrected = False
    if s.endswith("+00:00"):
        s, corrected = s[:-6], True
    naive = datetime.fromisoformat(s)
    if naive.tzinfo is not None:  # any other offset: trust it
        return naive.astimezone(timezone.utc), False
    return naive.replace(tzinfo=ET).astimezone(timezone.utc), corrected


def parse_date_us(s: str) -> date:
    m, d, y = s.split("/")
    return date(int(y), int(m), int(d))


def parse_iso_date(s: str | None) -> date | None:
    return None if not s else date.fromisoformat(s[:10])


def et_date(ts: datetime) -> date:
    return ts.astimezone(ET).date()


def num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------- quarantine

def quarantined_ids(conn, qtable: str, run_id: str) -> set[int]:
    """Bronze ids the validate task quarantined in this run; Silver skips them."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT bronze_id FROM silver.{qtable} WHERE run_id = %s", (run_id,))
        return {r[0] for r in cur.fetchall()}


def quarantine_identity_failures(conn, qtable: str, run_id: str, rows: list[tuple]) -> int:
    """rows: (bronze_id, endpoint, raw_identifier, reason). Same table as the DQ
    failures so 'why is this row not in Silver' has exactly one answer."""
    if rows:
        execute_values(conn.cursor(), f"""INSERT INTO silver.{qtable}
            (bronze_id, endpoint, run_id, expectation_name, column_name, observed_value, reason)
            VALUES %s""", [(b, e, run_id, "player_resolved", "player", raw, reason) for b, e, raw, reason in rows])
    return len(rows)


def pct(n: int, d: int) -> float | None:
    return None if not d else round(100.0 * n / d, 2)


def schema_versions(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = r.get("_schema_version") or "none"
        out[k] = out.get(k, 0) + 1
    return out
