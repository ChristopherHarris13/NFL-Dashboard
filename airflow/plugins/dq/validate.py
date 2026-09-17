"""Run a source's expectation suites and route, don't fail.

Suites live in great_expectations/expectations/<suite>.json (checked in; the
JSON is the source of truth). Each names the staging view it validates.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from psycopg2.extras import Json, execute_values

log = logging.getLogger(__name__)

SUITES_DIR = Path(os.environ.get("GX_SUITES_DIR", "/opt/airflow/great_expectations/expectations"))

# A failed expectation is dirt: its rows go to quarantine and the task passes.
# Above this share of a batch it's not dirt, it's a broken feed: fail the task.
SYSTEMIC_UNEXPECTED_PCT = 50.0

# Source -> quarantine table suffix. (Bronze calls wellness "ams_wellness".)
QUARANTINE_TABLE = {"forcedeck": "quarantine_forcedeck", "catapult": "quarantine_catapult",
                    "ams_wellness": "quarantine_wellness", "nutrition": "quarantine_nutrition",
                    "emr": "quarantine_emr"}


class SystemicValidationFailure(Exception):
    """>50% of a batch failed an expectation, or a required column is gone."""


def load_suites(source: str) -> list[dict[str, Any]]:
    suites = [json.loads(p.read_text()) for p in sorted(SUITES_DIR.glob("*.json"))]
    return [s for s in suites if s["meta"]["source"] == source]


def describe(exp_type: str, kwargs: dict[str, Any]) -> str:
    col = kwargs.get("column") or f"{kwargs.get('column_A')} vs {kwargs.get('column_B')}"
    if exp_type == "expect_column_values_to_be_between":
        return f"{col} outside {kwargs.get('min_value')}..{kwargs.get('max_value')}"
    if exp_type == "expect_column_values_to_be_in_set":
        return f"{col} not in {kwargs.get('value_set')}"
    if exp_type == "expect_column_values_to_not_be_null":
        return f"{col} is null"
    if exp_type == "expect_column_values_to_be_unique":
        return f"{col} is duplicated"
    if exp_type == "expect_column_values_to_match_regex":
        return f"{col} does not match {kwargs.get('regex')}"
    if exp_type == "expect_column_pair_values_a_to_be_greater_than_b":
        return f"{kwargs.get('column_A')} < {kwargs.get('column_B')}"
    return f"{col}: {exp_type}"


def _plain(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, float) or type(v).__name__ == "Decimal":
        return f"{float(v):g}"
    return str(v)


def observed(row: dict[str, Any], kwargs: dict[str, Any]) -> str:
    cols = [c for c in (kwargs.get("column"), kwargs.get("column_A"), kwargs.get("column_B")) if c]
    return ", ".join(f"{c}={_plain(row.get(c))}" for c in cols)


def unexpected_rows(conn, res: dict[str, Any]) -> list[dict[str, Any]]:
    """Every failing row with its bronze_id. GX caps unexpected_index_list at 20
    on SQL datasources but returns the full query; run it ourselves."""
    rows = res.get("unexpected_index_list") or []
    query = res.get("unexpected_index_query")
    if query and (res.get("unexpected_count") or 0) > len(rows):
        with conn.cursor() as cur:
            cur.execute(query)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return rows


def run_suite(ctx, datasource, suite_doc: dict[str, Any]):
    """Validate one suite against its staging view; return the GX validation result."""
    import great_expectations as gx

    name = suite_doc["name"]
    asset = datasource.add_table_asset(name=f"{name}_asset", table_name=suite_doc["meta"]["table"], schema_name="silver")
    batch = asset.add_batch_definition_whole_table(f"{name}_whole")
    suite = ctx.suites.add(gx.ExpectationSuite(name=name, expectations=suite_doc["expectations"]))
    vd = ctx.validation_definitions.add(gx.ValidationDefinition(name=name, data=batch, suite=suite))
    cp = ctx.checkpoints.add(gx.Checkpoint(
        name=name, validation_definitions=[vd],
        result_format={"result_format": "COMPLETE", "unexpected_index_column_names": ["bronze_id"],
                       "exclude_unexpected_values": False},
    ))
    result = cp.run(run_id=gx.RunIdentifier(run_name=name))
    return next(iter(result.run_results.values()))


def validate(conn, source: str, run_id: str, sqlalchemy_url: str) -> dict[str, Any]:
    import great_expectations as gx
    from great_expectations.data_context.types.base import ProgressBarsConfig

    ctx = gx.get_context(mode="ephemeral")
    ctx.variables.progress_bars = ProgressBarsConfig(globally=False)
    ds = ctx.data_sources.add_postgres(name="warehouse", connection_string=sqlalchemy_url)

    qtable = QUARANTINE_TABLE[source]
    quarantine_rows: list[tuple] = []
    result_rows: list[tuple] = []
    systemic: list[str] = []
    stats: dict[str, Any] = {"source": source, "run_id": run_id, "suites": {},
                             "expectations_run": 0, "expectations_failed": 0}

    for doc in load_suites(source):
        endpoint = doc["meta"]["endpoint"]
        vr = run_suite(ctx, ds, doc)
        suite_stats = {"element_count": None, "failed": []}
        for r in vr.results:
            cfg, res = r.expectation_config, r.result or {}
            col = cfg.kwargs.get("column") or cfg.kwargs.get("column_B")
            raised = bool((r.exception_info or {}).get("raised_exception")) if isinstance(r.exception_info, dict) \
                else any(v.get("raised_exception") for v in (r.exception_info or {}).values())
            pct = res.get("unexpected_percent")
            is_systemic = raised or (pct is not None and pct >= SYSTEMIC_UNEXPECTED_PCT)
            stats["expectations_run"] += 1
            if not r.success:
                stats["expectations_failed"] += 1
                suite_stats["failed"].append(f"{describe(cfg.type, cfg.kwargs)} ({res.get('unexpected_count')})")
            if is_systemic:
                systemic.append(f"{doc['name']}: {describe(cfg.type, cfg.kwargs)} — "
                                f"{'raised ' + str(r.exception_info) if raised else f'{pct:.1f}% unexpected'}")
            suite_stats["element_count"] = res.get("element_count", suite_stats["element_count"])
            result_rows.append((run_id, source, doc["name"], cfg.type, col, Json(dict(cfg.kwargs)), r.success,
                                res.get("element_count"), res.get("unexpected_count"), pct, is_systemic))
            if not r.success:
                reason = describe(cfg.type, cfg.kwargs)
                for row in unexpected_rows(conn, res):
                    quarantine_rows.append((row["bronze_id"], endpoint, run_id, cfg.type, col,
                                            observed(row, cfg.kwargs), f"{reason}: {observed(row, cfg.kwargs)}"))
        stats["suites"][doc["name"]] = suite_stats

    with conn.cursor() as cur:
        # Quarantine is current state: this run's DQ rows replace the last run's.
        # (silver/*.py appends identity failures for the same run_id afterwards.)
        cur.execute(f"DELETE FROM silver.{qtable}")
        if quarantine_rows:
            execute_values(cur, f"""INSERT INTO silver.{qtable}
                (bronze_id, endpoint, run_id, expectation_name, column_name, observed_value, reason) VALUES %s""",
                           quarantine_rows, page_size=1000)
        execute_values(cur, """INSERT INTO silver.dq_expectation_results
            (run_id, source, suite, expectation_name, column_name, kwargs, success, element_count,
             unexpected_count, unexpected_percent, systemic) VALUES %s
            ON CONFLICT (run_id, source, suite, expectation_name, column_name) DO UPDATE SET
              success = EXCLUDED.success, element_count = EXCLUDED.element_count,
              unexpected_count = EXCLUDED.unexpected_count, unexpected_percent = EXCLUDED.unexpected_percent,
              systemic = EXCLUDED.systemic, validated_at = now()""", result_rows)
        cur.execute(f"SELECT count(DISTINCT bronze_id) FROM silver.{qtable} WHERE run_id = %s", (run_id,))
        stats["rows_quarantined_dq"] = cur.fetchone()[0]
    conn.commit()
    stats["quarantine_rows"] = len(quarantine_rows)
    log.info("validate %s: %s", source, stats)
    if systemic:
        raise SystemicValidationFailure("; ".join(systemic))
    return stats
