"""silver.wellness_surveys.

Wrinkles: schema drift (v1 player_id/name -> v2 athlete_id/first+last, and
the envelope's version lies about backfilled rows, so shape comes from the
keys); the Likert scale silently changes 1-5 -> 1-10 on one day, announced
only by v2's scale_max; naive local timestamps that are really ET, sometimes
with a bogus +00:00; duplicate submissions minutes apart with changed answers.

Scale inference for rows without scale_max: a survey day's cohort decides.
If any survey that day scores above 5, the whole day is on the 10-point
scale (1-5 answers can't exceed 5). Days with scale_max present use it.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from silver.common import (et_date, fetch_bronze, latest_per_key, load_resolver, num, parse_facility_local,
                           persist_resolver, replace_table)

log = logging.getLogger(__name__)

VENDOR = "ams_wellness"
LIKERT = ["sleep_quality", "soreness", "fatigue", "stress", "mood"]
COLUMNS = ["bronze_id", "survey_id", "player_sk", "resolved_by", "vendor_nfl_id", "survey_date",
           "submitted_at", "tz_corrected", "schema_shape", "scale_max", "scale_inferred",
           "sleep_hours", *LIKERT, "srpe", "is_resubmission", "qc_flags"]


def rescale(v: float | None, scale_max: int) -> float | None:
    """Map a 1..scale_max answer onto 1..10 (1 -> 1, scale_max -> 10)."""
    if v is None:
        return None
    if scale_max == 10:
        return float(v)
    return round(1 + (v - 1) * 9 / (scale_max - 1), 2)


def shape_of(p: dict) -> str:
    return "v2" if "athlete_id" in p or "scale_max" in p else "v1"


def build(conn) -> dict:
    bronze = fetch_bronze(conn, VENDOR, "/v1/surveys")
    rows, dropped = latest_per_key(bronze, lambda r: r["payload"]["survey_id"])
    resolver = load_resolver(conn)

    # Pass 1: parse timestamps, work out each day's scale from the cohort.
    parsed = []
    day_max: dict = defaultdict(float)
    day_declared: dict = {}
    for r in rows:
        p = r["payload"]
        ts, corrected = parse_facility_local(p["submitted_at"])
        day = p["submitted_at"][:10]
        parsed.append((r, p, ts, corrected, day))
        if p.get("scale_max"):
            day_declared[day] = int(p["scale_max"])
        for f in LIKERT:
            v = num(p.get(f))
            if v is not None:
                day_max[day] = max(day_max[day], v)

    def day_scale(day: str) -> tuple[int, bool]:
        if day in day_declared:
            return day_declared[day], False
        return (10 if day_max[day] > 5 else 5), True

    # Pass 2: rows. Sort by player-day then submission time to spot resubmissions.
    out, seen_player_day = [], set()
    shape_counts, scale_counts = defaultdict(int), defaultdict(int)
    for r, p, ts, corrected, day in sorted(parsed, key=lambda t: (t[4], t[2], t[0]["id"])):
        shape = shape_of(p)
        shape_counts[shape] += 1
        nfl_id = p.get("athlete_id", p.get("player_id"))
        name = p.get("name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        res = resolver.resolve(VENDOR, nfl_id=nfl_id, vendor_player_id=None if nfl_id is None else str(nfl_id),
                               name=name, context={"bronze_id": r["id"]})
        scale, inferred = day_scale(day)
        if p.get("scale_max"):
            scale, inferred = int(p["scale_max"]), False
        scale_counts[f"{scale}{'_inferred' if inferred else ''}"] += 1
        flags = []
        if corrected:
            flags.append("tz_corrected")
        if inferred:
            flags.append("scale_inferred")
        key = (res.player_sk if res.ok else f"raw:{nfl_id}", day)
        resub = key in seen_player_day
        seen_player_day.add(key)
        if resub:
            flags.append("resubmission")
        if not res.ok:
            flags.append("unresolved_player")
        out.append((
            r["id"], p["survey_id"], res.player_sk, res.resolved_by,
            None if nfl_id is None else int(nfl_id), et_date(ts),
            ts, corrected, shape, scale, inferred, num(p.get("sleep_hours")),
            *[rescale(num(p.get(f)), scale) for f in LIKERT],
            p.get("srpe"), resub, flags,
        ))

    n = replace_table(conn, "wellness_surveys", COLUMNS, out)
    idstats = persist_resolver(conn, resolver, VENDOR)
    conn.commit()
    stats = {"bronze_rows": len(bronze), "duplicates_dropped": dropped, "silver_rows": n,
             "resolved_by": dict(resolver.method_counts), "shape": dict(shape_counts),
             "scale": dict(scale_counts), "resubmissions": sum(1 for o in out if o[-2]),
             "tz_corrected": sum(1 for o in out if o[7]), **idstats}
    log.info("%s: %s", VENDOR, stats)
    return stats
