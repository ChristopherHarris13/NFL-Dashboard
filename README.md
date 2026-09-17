# NFL-Dashboard

GridironOps: a Bronze/Silver/Gold medallion pipeline over deliberately dirty
NFL performance data, orchestrated by Airflow, landing in Postgres.

```
mock vendors (5x FastAPI)  --->  Airflow  --->  Postgres warehouse
   8001-8005                      :8080         :5432  bronze / silver / gold
```

## Run the stack

```bash
cp .env.example .env          # optional; compose has the same defaults built in
docker compose up --build
```

| Service | URL | Notes |
|---|---|---|
| Airflow UI | http://localhost:8080 | no login locally (`SIMPLE_AUTH_MANAGER_ALL_ADMINS`) |
| Warehouse | `postgresql://warehouse:warehouse@localhost:5432/warehouse` | schemas `bronze`, `silver`, `gold`, `meta` |
| Mock vendors | http://localhost:8001 … 8005 | see [`mock_vendors/README.md`](mock_vendors/README.md) |

Sanity check the wiring: in the Airflow UI trigger `stack_healthcheck`, or

```bash
docker compose exec airflow airflow dags trigger stack_healthcheck
```

It hits every vendor's `/health` and confirms the four warehouse schemas exist.

## Layout

| Path | What |
|---|---|
| `mock_vendors/` | five vendor simulators emitting dirty data on a shared simulated calendar |
| `warehouse/init/` | Postgres first-boot scripts: Airflow metadata DB, medallion schemas, bronze landing tables, `meta.ingest_cursor` |
| `airflow/dags/` | DAGs (bind-mounted; edits show up without a rebuild) |
| `airflow/Dockerfile` | `apache/airflow:3.3.1` + `airflow/requirements.txt` |

## Warehouse

One Postgres 17 container, two databases:

- **`warehouse`** — the medallion layers.
  - `bronze.*` — one append-only JSONB landing table per vendor resource
    (`catapult_sessions`, `forcedeck_tests`, `ams_wellness_surveys`,
    `nutrition_measurements`, `emr_injuries`, `emr_status_updates`). Raw
    payload plus ingest metadata (`schema_version`, `source_cursor`,
    `ingested_at`, `dag_run_id`, `payload_hash`). No dedup here on purpose.
  - `silver`, `gold` — created empty; tables arrive with the transform stage.
  - `meta.ingest_cursor` — per `(source, resource)` `next_cursor`, pre-seeded
    with the six streams so ingest DAGs start from the beginning.
- **`airflow`** — Airflow's own metadata, separate role, never touched by DAGs.

Init scripts only run against an empty volume. To rebuild from scratch:

```bash
docker compose down -v && docker compose up --build
```

## Airflow

Airflow 3 `standalone` (api-server, scheduler, dag-processor, triggerer in one
container) on `LocalExecutor`. DAGs get a preconfigured connection id
**`warehouse`** (`PostgresHook(postgres_conn_id="warehouse")`) and reach the
vendors by compose service name (`http://catapult_svc:8001`). Add Python deps
to `airflow/requirements.txt` and `docker compose up --build airflow`.
