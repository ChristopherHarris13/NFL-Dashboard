"""silver.forcedeck_tests.

Wrinkles: byte-different re-emissions of the same test_id (keep latest
ingested); peak_force in N or lbf with the unit label missing half the time
and *stale* ('N' on an lbf value) a quarter of the time; epoch timestamps.
Aborted reps and null test types are quarantined by the forcedeck suite
before this runs.

Force unit: real peak forces are 1500-6500 N, which is 337-1461 lbf — the
ranges never overlap, so magnitude decides and the label is only evidence
of whether the vendor got it right (`force_unit_inferred` = label absent or
contradicted). Validation caught this: trusting the label let 2,203 stale
'N' rows through at 450-800 "newtons".
"""

from __future__ import annotations

import logging

from silver.common import (N_PER_LBF, fetch_bronze, latest_per_key, load_resolver, num, parse_epoch_utc, pct,
                           persist_resolver, quarantine_identity_failures, quarantined_ids, replace_table,
                           schema_versions)

log = logging.getLogger(__name__)

VENDOR = "forcedeck"
ENDPOINT = "/v1/tests"
QTABLE = "quarantine_forcedeck"
COLUMNS = ["bronze_id", "test_id", "player_sk", "resolved_by", "athlete_gsis_id", "test_ts",
           "test_type", "rep", "jump_height_cm", "peak_force_n", "peak_force_raw", "force_unit_raw",
           "force_unit_inferred", "rfd", "asymmetry_pct", "device_id", "qc_flags"]

LBF_THRESHOLD_N = 1500.0


def normalise_force(value: float, unit: str | None) -> tuple[float, bool]:
    """(newtons, unit_was_inferred). Magnitude decides; the label just tells
    us whether we had to overrule it."""
    is_lbf = value < LBF_THRESHOLD_N
    newtons = value * N_PER_LBF if is_lbf else value
    return newtons, unit != ("lbf" if is_lbf else "N")


def build(conn, run_id: str = "manual") -> dict:
    bronze = fetch_bronze(conn, VENDOR, ENDPOINT)
    rows, dropped = latest_per_key(bronze, lambda r: r["payload"]["test_id"])
    dq = quarantined_ids(conn, QTABLE, run_id)
    rows = [r for r in rows if r["id"] not in dq]
    resolver = load_resolver(conn)

    out, identity_q = [], []
    for r in rows:
        p = r["payload"]
        res = resolver.resolve(VENDOR, gsis_id=p.get("athlete_gsis_id"), vendor_player_id=p.get("athlete_gsis_id"),
                               context={"bronze_id": r["id"]})
        if not res.ok:
            identity_q.append((r["id"], ENDPOINT, p.get("athlete_gsis_id"), f"player {res.reason}: gsis_id={p.get('athlete_gsis_id')!r}"))
            continue
        flags = []
        force_raw = num(p.get("peak_force"))
        force_n, inferred = normalise_force(force_raw, p.get("force_unit"))
        if inferred:
            flags.append("force_unit_inferred")
        if "device_id" not in p:
            flags.append("missing_device")
        out.append((
            r["id"], p["test_id"], res.player_sk, res.resolved_by, p.get("athlete_gsis_id"),
            parse_epoch_utc(p["test_ts"]), p.get("test_type"), p.get("rep"), num(p.get("jump_height_cm")),
            round(force_n, 2), force_raw, p.get("force_unit"), inferred,
            num(p.get("rfd")), num(p.get("asymmetry_pct")), p.get("device_id"), flags,
        ))

    n = replace_table(conn, "forcedeck_tests", COLUMNS, out)
    idstats = persist_resolver(conn, resolver, VENDOR)
    quarantine_identity_failures(conn, QTABLE, run_id, identity_q)
    conn.commit()
    stats = {"source": VENDOR, "rows_in": len(bronze), "duplicates_removed": dropped,
             "rows_quarantined_dq": len(dq), "rows_quarantined_identity": len(identity_q), "rows_in_silver": n,
             "resolved_by": dict(resolver.method_counts),
             "pct_units_inferred": pct(sum(1 for o in out if o[12]), n),
             "pct_timestamps_reformatted": 100.0,  # epoch seconds -> UTC timestamptz
             "schema_versions_seen": schema_versions(bronze), **idstats}
    log.info("%s: %s", VENDOR, stats)
    return stats
