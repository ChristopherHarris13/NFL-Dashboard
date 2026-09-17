"""silver.dq_run_summary: one row per source per run, reconciled."""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import Json

log = logging.getLogger(__name__)


class ReconciliationError(Exception):
    """rows_in - duplicates - quarantined != rows_in_silver: the staging view
    and the Silver transform disagree about which rows exist."""


def write_summary(conn, run_id: str, validations: list[dict[str, Any]], silvers: list[dict[str, Any]]) -> list[dict]:
    v_by = {v["source"]: v for v in validations}
    out = []
    with conn.cursor() as cur:
        for s in silvers:
            v = v_by.get(s["source"], {})
            quarantined = s["rows_quarantined_dq"] + s["rows_quarantined_identity"]
            reconciles = s["rows_in"] - s["duplicates_removed"] - quarantined == s["rows_in_silver"]
            row = {
                "run_id": run_id, "source": s["source"], "rows_in": s["rows_in"],
                "duplicates_removed": s["duplicates_removed"], "rows_quarantined": quarantined,
                "rows_quarantined_dq": s["rows_quarantined_dq"],
                "rows_quarantined_identity": s["rows_quarantined_identity"],
                "rows_in_silver": s["rows_in_silver"], "reconciles": reconciles,
                "resolved_by": s["resolved_by"], "pct_units_inferred": s.get("pct_units_inferred"),
                "pct_timestamps_reformatted": s.get("pct_timestamps_reformatted"),
                "schema_versions_seen": s.get("schema_versions_seen"),
                "expectations_run": v.get("expectations_run", 0),
                "expectations_failed": v.get("expectations_failed", 0),
            }
            cur.execute("""
                INSERT INTO silver.dq_run_summary
                    (run_id, source, rows_in, duplicates_removed, rows_quarantined, rows_quarantined_dq,
                     rows_quarantined_identity, rows_in_silver, reconciles, resolved_by, pct_units_inferred,
                     pct_timestamps_reformatted, schema_versions_seen, expectations_run, expectations_failed)
                VALUES (%(run_id)s, %(source)s, %(rows_in)s, %(duplicates_removed)s, %(rows_quarantined)s,
                        %(rows_quarantined_dq)s, %(rows_quarantined_identity)s, %(rows_in_silver)s, %(reconciles)s,
                        %(resolved_by)s, %(pct_units_inferred)s, %(pct_timestamps_reformatted)s,
                        %(schema_versions_seen)s, %(expectations_run)s, %(expectations_failed)s)
                ON CONFLICT (run_id, source) DO UPDATE SET
                    rows_in = EXCLUDED.rows_in, duplicates_removed = EXCLUDED.duplicates_removed,
                    rows_quarantined = EXCLUDED.rows_quarantined, rows_quarantined_dq = EXCLUDED.rows_quarantined_dq,
                    rows_quarantined_identity = EXCLUDED.rows_quarantined_identity,
                    rows_in_silver = EXCLUDED.rows_in_silver, reconciles = EXCLUDED.reconciles,
                    resolved_by = EXCLUDED.resolved_by, pct_units_inferred = EXCLUDED.pct_units_inferred,
                    pct_timestamps_reformatted = EXCLUDED.pct_timestamps_reformatted,
                    schema_versions_seen = EXCLUDED.schema_versions_seen,
                    expectations_run = EXCLUDED.expectations_run, expectations_failed = EXCLUDED.expectations_failed,
                    created_at = now()
            """, {**row, "resolved_by": Json(row["resolved_by"]), "schema_versions_seen": Json(row["schema_versions_seen"])})
            out.append(row)
    conn.commit()
    bad = [r for r in out if not r["reconciles"]]
    for r in out:
        log.info("dq_summary %s: in=%d dup=%d quarantined=%d silver=%d reconciles=%s", r["source"], r["rows_in"],
                 r["duplicates_removed"], r["rows_quarantined"], r["rows_in_silver"], r["reconciles"])
    if bad:
        raise ReconciliationError(", ".join(
            f"{r['source']}: {r['rows_in']} - {r['duplicates_removed']} - {r['rows_quarantined']} != {r['rows_in_silver']}"
            for r in bad))
    return out
