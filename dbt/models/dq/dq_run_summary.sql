{{ config(materialized='view') }}
-- Gold-facing view of the DQ scorecard so the dashboard never reads silver.*.
select run_id, source, rows_in, duplicates_removed, rows_quarantined, rows_quarantined_dq,
       rows_quarantined_identity, rows_in_silver, reconciles, resolved_by,
       pct_units_inferred, pct_timestamps_reformatted, schema_versions_seen,
       expectations_run, expectations_failed, created_at,
       dense_rank() over (order by created_at desc) as run_recency   -- 1 = latest run
from {{ source('silver', 'dq_run_summary') }}
