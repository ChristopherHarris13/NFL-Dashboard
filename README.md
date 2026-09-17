# NFL-Dashboard

GridironOps: a Bronze/Silver/Gold medallion pipeline over deliberately dirty
NFL performance data, orchestrated by Airflow, landing in Postgres.

```
5 mock vendors (FastAPI)  ─┐
nflverse (nfl_data_py)    ─┼─▶  Airflow 2.11 ─▶  Postgres warehouse
Open-Meteo (Arrowhead)    ─┘    :8080              :5432  bronze / silver / gold
```

## Run the stack

```bash
cp .env.example .env          # optional; compose has the same defaults built in
docker compose up --build
```

| Service | URL | Notes |
|---|---|---|
| Airflow UI | http://localhost:8080 | `admin` / `admin` |
| Warehouse | `postgresql://warehouse:warehouse@localhost:5432/warehouse` | schemas `bronze`, `silver`, `gold` |
| Mock vendors | http://localhost:8001 … 8005 | see [`mock_vendors/README.md`](mock_vendors/README.md) |

Airflow's own metadata lives in a second Postgres (`airflow_db`, not exposed).

The `nfl_medallion` DAG runs every 15 minutes (unpause it in the UI, or
`docker compose exec airflow airflow dags unpause nfl_medallion`). Watch
Bronze fill:

```sql
select _source, _endpoint, count(*) from bronze.forcedeck group by 1, 2;
```

## Layout

| Path | What |
|---|---|
| `mock_vendors/` | five vendor simulators emitting dirty data on a shared simulated calendar |
| `warehouse/init/` | Postgres first-boot scripts: medallion schemas + the generic `bronze.<source>` tables |
| `airflow/plugins/extract.py` | the one write path into Bronze (`land_records`) and the cursor-walking mock extractor (`extract_resource`) |
| `airflow/dags/nfl_medallion.py` | the pipeline DAG — Bronze extracts only, for now |
| `airflow/Dockerfile` | `apache/airflow:2.11.2` + `dbt-postgres`, `great_expectations`, `nfl_data_py` (baked in now so the image isn't rebuilt per stage) |

## Bronze

One shape, one table per source (`catapult`, `forcedeck`, `ams_wellness`,
`nutrition`, `emr`, `nflverse`, `open_meteo`):

```sql
bronze.<source> (
  id bigserial, payload jsonb,
  _source text, _endpoint text, _ingested_at timestamptz,
  _batch_id uuid, _payload_hash text, _schema_version text
)
```

- `payload` is the record exactly as the source emitted it. Nothing is
  renamed, cast, or cleaned — dupes, unit swaps, drifted schemas, free-text
  body parts all land as-is. Silver's job.
- **Idempotency:** `UNIQUE (_payload_hash, _source)` + `ON CONFLICT DO NOTHING`.
  Re-running a task inserts zero rows. Byte-identical re-emissions (ForceDecks'
  `duplicate` dirt) collapse here; re-emissions that differ (the re-rounded
  copy, a corrected EMR update, a changed wellness resubmission) hash
  differently and are kept.
- `_schema_version` is the API envelope's version *at fetch time*, not the
  record's. After AMS drifts to v2, backfilled v1-shaped records still carry
  `v2` — Silver must infer shape from the keys.
- `_endpoint` distinguishes streams inside one source (`/v1/injuries` vs
  `/v1/status_updates` in `emr`; `rosters` vs `ids` in `nflverse`).

### Mock vendors: cursor walk

`extract_resource` reads the last cursor from the Airflow Variable
`bronze_cursor__<source>__<resource>`, pages `/v1/<resource>?since=…&limit=500`
until `data` is empty, lands each page, and saves the cursor after every page
(a crash mid-walk resumes, not restarts). Delete the Variable to re-walk from
the beginning — the hash index means that still inserts nothing new.

### Real feeds: snapshot + dedup

`nflverse` (seasonal rosters + the id crosswalk via `nfl_data_py`) and
`open_meteo` (hourly forecast for GEHA Field at Arrowhead, ±7 days, one row
per hour) have no cursor. Every run lands the full current snapshot and the
hash index drops what's already there. Roster rows stay flat between runs;
forecast rows grow a little each run as the forecast for a given hour changes.

## Reset

Init scripts only run against an empty volume:

```bash
docker compose down -v && docker compose up --build
```
