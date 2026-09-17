"""silver.dim_player_master from Bronze nflverse rosters + the id crosswalk.

Upsert keyed on gsis_id: a player gets one player_sk for life. Attributes
(team, position, ...) are updated in place (SCD1); a player missing from the
latest roster snapshot gets valid_to set, and reappearing clears it.
"""

from __future__ import annotations

import logging

from psycopg2.extras import execute_values

from identity import clean_player_name
from silver.common import fetch_bronze, latest_per_key, num

log = logging.getLogger(__name__)


def build(conn) -> dict:
    rosters = fetch_bronze(conn, "nflverse", "rosters")
    ids = fetch_bronze(conn, "nflverse", "ids")
    if not rosters:
        raise RuntimeError("no nflverse roster rows in Bronze yet")

    # Latest roster row per player (re-runs land newer weeks for the same gsis).
    latest, _ = latest_per_key(rosters, lambda r: r["payload"]["player_id"])
    # Crosswalk: one row per gsis, the most recently ingested wins.
    xwalk = {}
    for r in ids:
        g = r["payload"].get("gsis_id")
        if g:
            xwalk[g] = r["payload"]

    rows = []
    for r in latest:
        p, x = r["payload"], xwalk.get(r["payload"]["player_id"], {})
        full = p.get("player_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        rows.append((
            p["player_id"],
            _int(x.get("nfl_id")),
            _int(p.get("espn_id") or x.get("espn_id")),
            p.get("pfr_id") or x.get("pfr_id"),
            full, clean_player_name(full),
            p.get("first_name"), p.get("last_name"), p.get("football_name"),
            p.get("team"), p.get("position"),
            num(p.get("weight")), num(p.get("height")),
            r["id"],
        ))

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO silver.dim_player_master
                (gsis_id, nfl_id, espn_id, pfr_id, full_name, clean_name, first_name, last_name,
                 football_name, team, position, weight_lbs, height_in, bronze_id)
            VALUES %s
            ON CONFLICT (gsis_id) DO UPDATE SET
                nfl_id = EXCLUDED.nfl_id, espn_id = EXCLUDED.espn_id, pfr_id = EXCLUDED.pfr_id,
                full_name = EXCLUDED.full_name, clean_name = EXCLUDED.clean_name,
                first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name,
                football_name = EXCLUDED.football_name, team = EXCLUDED.team, position = EXCLUDED.position,
                weight_lbs = EXCLUDED.weight_lbs, height_in = EXCLUDED.height_in,
                bronze_id = EXCLUDED.bronze_id, valid_to = NULL, updated_at = now()
            WHERE (silver.dim_player_master.nfl_id, silver.dim_player_master.team,
                   silver.dim_player_master.position, silver.dim_player_master.full_name,
                   silver.dim_player_master.valid_to)
               IS DISTINCT FROM (EXCLUDED.nfl_id, EXCLUDED.team, EXCLUDED.position,
                                 EXCLUDED.full_name, NULL::timestamptz)
        """, rows, page_size=1000)
        cur.execute("""UPDATE silver.dim_player_master SET valid_to = now(), updated_at = now()
                       WHERE valid_to IS NULL AND gsis_id <> ALL(%s)""", ([r[0] for r in rows],))
        retired = cur.rowcount
        cur.execute("""SELECT count(*), count(*) FILTER (WHERE valid_to IS NULL),
                              count(*) FILTER (WHERE nfl_id IS NULL AND valid_to IS NULL)
                       FROM silver.dim_player_master""")
        total, active, no_nfl_id = cur.fetchone()
    conn.commit()
    stats = {"roster_rows": len(rosters), "players": len(rows), "total": total,
             "active": active, "retired_this_run": retired, "active_without_nfl_id": no_nfl_id}
    log.info("dim_player_master: %s", stats)
    return stats


def _int(v):
    try:
        return None if v in (None, "") else int(float(v))
    except (TypeError, ValueError):
        return None
