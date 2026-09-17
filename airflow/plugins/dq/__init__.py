"""Data quality: Great Expectations checkpoints that route, and the scorecard.

validate.py  runs every suite for a source against its silver.stg_* view and
             writes failing rows to silver.quarantine_<source> — the task
             succeeds unless the failure is systemic.
summary.py   one silver.dq_run_summary row per source per run, reconciled:
             rows_in - duplicates - quarantined = rows_in_silver.
"""
