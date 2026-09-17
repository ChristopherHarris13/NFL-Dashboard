"""silver.catapult_sessions.

Wrinkles: vendor UUID identity resolved through /v1/athletes names and
pinned in vendor_player_map; distance in m or yd and speed in m/s or mph,
with labels missing (30%) or stale (40% of swaps keep the old label); late
arrivals (a session emitted 1-3 sim days after it happened); null player_load
and impossible distances.

Late arrival is *not* derived here: Bronze keeps ingest time but not the
vendor's emission time, and the simulated calendar runs ahead of wall-clock,
so "ingested minus session date" is meaningless. Silver carries
_ingested_at; a session that appears in a later run than its date implies
is Gold's comparison to make.

Unit inference:
  speed    — ranges don't overlap (m/s 6-10.5, mph 13-24): magnitude decides,
             even over a label.
  distance — m and yd overlap completely, so the label is trusted when present
             ('yd' -> convert) and absence means metres. A stale 'm' label on
             a yd value is indistinguishable by magnitude; flagged for GX to
             catch distributionally, not guessed here.
"""

from __future__ import annotations

import logging
from silver.common import (M_PER_YD, MPH_PER_MS, et_date, fetch_bronze, latest_per_key, load_resolver,
                           num, parse_iso_utc, persist_resolver, replace_table)

log = logging.getLogger(__name__)

VENDOR = "catapult"
COLUMNS = ["bronze_id", "session_id", "player_sk", "resolved_by", "vendor_player_id", "session_ts",
           "session_date", "session_type", "duration_min", "total_distance_m", "high_speed_distance_m",
           "distance_unit_raw", "distance_unit_inferred", "max_speed_ms", "speed_unit_raw",
           "speed_unit_inferred", "sprint_count", "accel_count", "decel_count", "player_load",
           "_ingested_at", "qc_flags"]

MPH_THRESHOLD = 12.0  # nothing on a football field runs 12 m/s; anything above is mph


def normalise_speed(value: float | None, label: str | None) -> tuple[float | None, bool]:
    if value is None:
        return None, False
    is_mph = value > MPH_THRESHOLD
    return (value / MPH_PER_MS if is_mph else value), (label != ("mph" if is_mph else "m/s"))


def normalise_distance(value: float | None, label: str | None) -> tuple[float | None, bool]:
    if value is None:
        return None, False
    if label == "yd":
        return value * M_PER_YD, False
    return value, label is None


def build(conn) -> dict:
    resolver = load_resolver(conn)

    # Athlete list: pins cat_<id> -> player_sk by name (no team in the feed).
    athletes = fetch_bronze(conn, VENDOR, "/v1/athletes")
    athlete_rows, _ = latest_per_key(athletes, lambda r: r["payload"]["athlete_id"])
    athlete_name = {r["payload"]["athlete_id"]: f"{r['payload'].get('first_name', '')} {r['payload'].get('last_name', '')}"
                    for r in athlete_rows}
    for aid, name in athlete_name.items():
        resolver.resolve(VENDOR, vendor_player_id=aid, name=name,
                         context={"bronze_id": next(r["id"] for r in athlete_rows if r["payload"]["athlete_id"] == aid)})
    athlete_methods = dict(resolver.method_counts)

    bronze = fetch_bronze(conn, VENDOR, "/v1/sessions")
    rows, dropped = latest_per_key(bronze, lambda r: r["payload"]["session_id"])
    out = []
    for r in rows:
        p = r["payload"]
        # Sessions carry only the vendor id; the athlete list supplies the name
        # so an unpinned id quarantines with the same reason/candidates.
        res = resolver.resolve(VENDOR, vendor_player_id=p["player_id"],
                               name=athlete_name.get(p["player_id"]), context={"bronze_id": r["id"]})
        flags = []
        dist_m, dist_inf = normalise_distance(num(p.get("total_distance")), p.get("distance_unit"))
        hsd_m, _ = normalise_distance(num(p.get("high_speed_distance")), p.get("distance_unit"))
        speed_ms, speed_inf = normalise_speed(num(p.get("max_speed")), p.get("speed_unit"))
        if dist_inf:
            flags.append("distance_unit_inferred")
        if speed_inf:
            flags.append("speed_unit_inferred")
        if dist_m is not None and dist_m > 15000:
            flags.append("distance_outlier")
        if p.get("player_load") is None:
            flags.append("null_player_load")
        ts = parse_iso_utc(p["session_ts"])
        if not res.ok:
            flags.append("unresolved_player")
        out.append((
            r["id"], p["session_id"], res.player_sk, res.resolved_by, p["player_id"], ts, et_date(ts),
            p.get("session_type"), p.get("duration_min"),
            None if dist_m is None else round(dist_m, 1), None if hsd_m is None else round(hsd_m, 1),
            p.get("distance_unit"), dist_inf,
            None if speed_ms is None else round(speed_ms, 2), p.get("speed_unit"), speed_inf,
            p.get("sprint_count"), p.get("accel_count"), p.get("decel_count"), num(p.get("player_load")),
            r["_ingested_at"], flags,
        ))

    n = replace_table(conn, "catapult_sessions", COLUMNS, out)
    idstats = persist_resolver(conn, resolver, VENDOR)
    conn.commit()
    stats = {"athletes": len(athlete_rows), "athlete_resolution": athlete_methods,
             "bronze_rows": len(bronze), "duplicates_dropped": dropped, "silver_rows": n,
             "resolved_by": dict(resolver.method_counts),
             "distance_unit_inferred": sum(1 for o in out if o[12]),
             "speed_unit_inferred": sum(1 for o in out if o[15]), **idstats}
    log.info("%s: %s", VENDOR, stats)
    return stats
