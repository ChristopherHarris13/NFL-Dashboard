"""silver.forcedeck_tests.

Wrinkles: byte-different re-emissions of the same test_id (keep latest
ingested); peak_force in N or lbf with the unit label often missing (infer
from magnitude: real peak forces are 1500-6500 N, so anything under 1500 is
almost certainly lbf); epoch timestamps; aborted reps and null test types
flagged, not dropped.
"""

from __future__ import annotations

import logging

from identity import UNRESOLVED
from silver.common import (N_PER_LBF, fetch_bronze, latest_per_key, load_resolver, num,
                           parse_epoch_utc, persist_resolver, replace_table)

log = logging.getLogger(__name__)

VENDOR = "forcedeck"
COLUMNS = ["bronze_id", "test_id", "player_sk", "resolved_by", "athlete_gsis_id", "test_ts",
           "test_type", "rep", "jump_height_cm", "peak_force_n", "peak_force_raw", "force_unit_raw",
           "force_unit_inferred", "rfd", "asymmetry_pct", "device_id", "qc_flags"]

LBF_THRESHOLD_N = 1500.0


def normalise_force(value: float, unit: str | None) -> tuple[float, bool]:
    """(newtons, unit_was_inferred)."""
    if unit == "N":
        return value, False
    if unit == "lbf":
        return value * N_PER_LBF, False
    # No usable label: infer from magnitude.
    return (value * N_PER_LBF if value < LBF_THRESHOLD_N else value), True


def build(conn) -> dict:
    bronze = fetch_bronze(conn, VENDOR, "/v1/tests")
    rows, dropped = latest_per_key(bronze, lambda r: r["payload"]["test_id"])
    resolver = load_resolver(conn)

    out = []
    for r in rows:
        p = r["payload"]
        res = resolver.resolve(VENDOR, gsis_id=p.get("athlete_gsis_id"),
                               vendor_player_id=p.get("athlete_gsis_id"),
                               context={"bronze_id": r["id"]})
        flags = []
        force_raw = num(p.get("peak_force"))
        force_n, inferred = normalise_force(force_raw, p.get("force_unit"))
        if inferred:
            flags.append("force_unit_inferred")
        jh = num(p.get("jump_height_cm"))
        if jh is not None and (jh < 5 or jh > 90):
            flags.append("aborted_rep")
        if p.get("test_type") is None:
            flags.append("null_test_type")
        if "device_id" not in p:
            flags.append("missing_device")
        if not res.ok:
            flags.append("unresolved_player")
        out.append((
            r["id"], p["test_id"], res.player_sk, res.resolved_by, p.get("athlete_gsis_id"),
            parse_epoch_utc(p["test_ts"]), p.get("test_type"), p.get("rep"), jh,
            round(force_n, 2), force_raw, p.get("force_unit"), inferred,
            num(p.get("rfd")), num(p.get("asymmetry_pct")), p.get("device_id"), flags,
        ))

    n = replace_table(conn, "forcedeck_tests", COLUMNS, out)
    idstats = persist_resolver(conn, resolver, VENDOR)
    conn.commit()
    stats = {"bronze_rows": len(bronze), "duplicates_dropped": dropped, "silver_rows": n,
             "resolved_by": dict(resolver.method_counts),
             "force_unit_inferred": sum(1 for o in out if o[12]), **idstats}
    log.info("%s: %s", VENDOR, stats)
    return stats
