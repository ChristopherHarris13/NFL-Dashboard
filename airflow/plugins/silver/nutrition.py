"""silver.nutrition_measurements.

Wrinkles: name-only identity, heavily mangled (the resolver's hardest
customer); weight in lb or kg with the label missing 40% of the time and
*stale* ('lb' on a kg value) 30% of the time; lean_mass sometimes a percent
with no indicator; obvious outliers.

Weight unit inference, in order of evidence strength:
  1. label says 'kg'            -> kg   (the swap never mislabels as kg)
  2. magnitude alone decides    -> <130 kg-only, >200 lb-only
  3. weight below lean mass     -> kg   (lean_mass stays in lb when absolute)
  4. closest to roster weight   -> whichever unit lands nearer
  5. default lb                 (US dietitian export)
"""

from __future__ import annotations

import logging

from silver.common import (KG_PER_LB, fetch_bronze, latest_per_key, load_resolver, num,
                           parse_date_us, persist_resolver, replace_table)

log = logging.getLogger(__name__)

VENDOR = "nutrition"
COLUMNS = ["bronze_id", "measurement_id", "player_sk", "resolved_by", "player_name_raw", "team",
           "measured_on", "method", "weight_kg", "weight_raw", "weight_unit_raw",
           "weight_unit_inferred", "weight_unit_evidence", "body_fat_pct", "lean_mass_kg",
           "lean_mass_raw", "lean_mass_was_pct", "hydration_status", "qc_flags"]


def infer_weight_unit(value: float, label: str | None, lean_mass: float | None,
                      roster_lbs: float | None) -> tuple[str, str]:
    """(unit, evidence) — see module docstring."""
    if label == "kg":
        return "kg", "label"
    if value < 130:
        return "kg", "magnitude"
    if value > 200:
        return "lb", "magnitude" if label != "lb" else "label"
    if lean_mass is not None and lean_mass >= 100 and value < lean_mass:
        return "kg", "lean_mass"
    if roster_lbs:
        as_lb, as_kg_in_lb = value, value / KG_PER_LB
        return ("lb", "roster") if abs(as_lb - roster_lbs) <= abs(as_kg_in_lb - roster_lbs) else ("kg", "roster")
    return "lb", "label" if label == "lb" else "default"


def build(conn) -> dict:
    bronze = fetch_bronze(conn, VENDOR, "/v1/measurements")
    rows, dropped = latest_per_key(bronze, lambda r: r["payload"]["measurement_id"])
    resolver = load_resolver(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT player_sk, weight_lbs FROM silver.dim_player_master")
        roster_weight = dict(cur.fetchall())

    out, evidence_counts = [], {}
    for r in rows:
        p = r["payload"]
        res = resolver.resolve(VENDOR, name=p.get("player_name"), team=p.get("team"),
                               context={"bronze_id": r["id"]})
        flags = []
        w_raw, lean_raw, bf = num(p.get("weight")), num(p.get("lean_mass")), num(p.get("body_fat_pct"))
        lean_was_pct = lean_raw is not None and lean_raw < 100
        unit, evidence = infer_weight_unit(
            w_raw, p.get("weight_unit"), None if lean_was_pct else lean_raw,
            float(roster_weight[res.player_sk]) if res.ok and roster_weight.get(res.player_sk) else None)
        evidence_counts[evidence] = evidence_counts.get(evidence, 0) + 1
        inferred = evidence != "label"
        w_kg = w_raw if unit == "kg" else w_raw * KG_PER_LB
        if lean_raw is None:
            lean_kg = None
        elif lean_was_pct:
            lean_kg = w_kg * lean_raw / 100
            flags.append("lean_mass_was_pct")
        else:
            lean_kg = lean_raw * KG_PER_LB  # absolute lean mass is always reported in lb
        if inferred:
            flags.append("weight_unit_inferred")
        if w_kg is not None and not 60 <= w_kg <= 200:
            flags.append("weight_outlier")
        if bf is not None and not 3 <= bf <= 35:
            flags.append("body_fat_outlier")
        if not res.ok:
            flags.append("unresolved_player")
        out.append((
            r["id"], p["measurement_id"], res.player_sk, res.resolved_by, p.get("player_name"),
            p.get("team"), parse_date_us(p["measured_on"]), p.get("method"),
            round(w_kg, 2), w_raw, p.get("weight_unit"), inferred, evidence,
            bf, None if lean_kg is None else round(lean_kg, 2), lean_raw, lean_was_pct,
            p.get("hydration_status"), flags,
        ))

    n = replace_table(conn, "nutrition_measurements", COLUMNS, out)
    idstats = persist_resolver(conn, resolver, VENDOR)
    conn.commit()
    stats = {"bronze_rows": len(bronze), "duplicates_dropped": dropped, "silver_rows": n,
             "resolved_by": dict(resolver.method_counts), "weight_unit_evidence": evidence_counts,
             **idstats}
    log.info("%s: %s", VENDOR, stats)
    return stats
