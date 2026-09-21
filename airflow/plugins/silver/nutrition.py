"""silver.nutrition_measurements.

Wrinkles: name-only identity, heavily mangled (the resolver's hardest
customer); weight is ALWAYS measured in lb, but the unit label is missing
40% of the time and wrong ('kg' on an lb value) 30% of the time — the label
is ignored, every value is taken as lb, and a 'kg' label is flagged as
mislabeled; lean_mass sometimes a percent with no indicator. Outliers are
quarantined by the nutrition suite first.
"""

from __future__ import annotations

import logging

from silver.common import (fetch_bronze, latest_per_key, load_resolver, num, parse_date_us, pct,
                           persist_resolver, quarantine_identity_failures, quarantined_ids, replace_table,
                           schema_versions)

log = logging.getLogger(__name__)

VENDOR = "nutrition"
ENDPOINT = "/v1/measurements"
QTABLE = "quarantine_nutrition"
COLUMNS = ["bronze_id", "measurement_id", "player_sk", "resolved_by", "player_name_raw", "team",
           "measured_on", "method", "weight_lbs", "weight_unit_raw", "weight_mislabeled",
           "body_fat_pct", "lean_mass_lbs", "lean_mass_raw", "lean_mass_was_pct",
           "hydration_status", "qc_flags"]


def build(conn, run_id: str = "manual") -> dict:
    bronze = fetch_bronze(conn, VENDOR, ENDPOINT)
    rows, dropped = latest_per_key(bronze, lambda r: r["payload"]["measurement_id"])
    dq = quarantined_ids(conn, QTABLE, run_id)
    rows = [r for r in rows if r["id"] not in dq]
    resolver = load_resolver(conn)

    out, identity_q, mislabeled_n = [], [], 0
    for r in rows:
        p = r["payload"]
        res = resolver.resolve(VENDOR, name=p.get("player_name"), team=p.get("team"), context={"bronze_id": r["id"]})
        if not res.ok:
            identity_q.append((r["id"], ENDPOINT, p.get("player_name"),
                               f"player {res.reason}: {p.get('player_name')!r} ({p.get('team')})"
                               + (f" candidates={list(res.candidates)}" if res.candidates else "")))
            continue
        flags = []
        w_lbs, lean_raw, bf = num(p.get("weight")), num(p.get("lean_mass")), num(p.get("body_fat_pct"))
        lean_was_pct = lean_raw is not None and lean_raw < 100
        # Weights are always taken in lb; the label is unreliable and ignored.
        mislabeled = p.get("weight_unit") == "kg"
        if mislabeled:
            mislabeled_n += 1
            flags.append("weight_mislabeled")
        if lean_raw is None:
            lean_lbs = None
        elif lean_was_pct:
            lean_lbs = w_lbs * lean_raw / 100
            flags.append("lean_mass_was_pct")
        else:
            lean_lbs = lean_raw  # absolute lean mass is reported in lb
        out.append((
            r["id"], p["measurement_id"], res.player_sk, res.resolved_by, p.get("player_name"),
            p.get("team"), parse_date_us(p["measured_on"]), p.get("method"),
            w_lbs, p.get("weight_unit"), mislabeled,
            bf, None if lean_lbs is None else round(lean_lbs, 2), lean_raw, lean_was_pct,
            p.get("hydration_status"), flags,
        ))

    n = replace_table(conn, "nutrition_measurements", COLUMNS, out)
    idstats = persist_resolver(conn, resolver, VENDOR)
    quarantine_identity_failures(conn, QTABLE, run_id, identity_q)
    conn.commit()
    stats = {"source": VENDOR, "rows_in": len(bronze), "duplicates_removed": dropped,
             "rows_quarantined_dq": len(dq), "rows_quarantined_identity": len(identity_q), "rows_in_silver": n,
             "resolved_by": dict(resolver.method_counts),
             "pct_weight_mislabeled": pct(mislabeled_n, n),
             "pct_timestamps_reformatted": 100.0,  # MM/DD/YYYY -> date
             "schema_versions_seen": schema_versions(bronze), **idstats}
    log.info("%s: %s", VENDOR, stats)
    return stats
