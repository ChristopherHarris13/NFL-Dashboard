# NFL-Dashboard

GridironOps: a Bronze/Silver/Gold medallion pipeline over deliberately dirty
NFL performance data, orchestrated by Airflow, landing in Postgres.

```
5 mock vendors (FastAPI)  ─┐
nflverse (nfl_data_py)    ─┼─▶  Airflow 2.11 ─▶  Postgres warehouse
Open-Meteo (Arrowhead)    ─┘    :8080              :5432  bronze / silver / gold

nfl_medallion DAG (every 15 min):
  extract_* (9)  ─▶  silver_schema  ─▶  silver_player_master  ─▶  silver_{forcedeck,nutrition,wellness,emr,catapult}
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
| `airflow/plugins/identity.py` | `clean_player_name()` and the six-rule `PlayerResolver` — pure Python, no DB |
| `airflow/plugins/silver/` | one transform module per source (`build(conn)`), plus `player_master.py` and shared `common.py` |
| `airflow/plugins/tests/` | unit tests: name cleaning against real nflverse pairs, resolver rules, unit inference, body-part parsing |
| `airflow/dags/nfl_medallion.py` | the pipeline DAG: Bronze extracts → Silver |
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

## Silver

Typed, deduplicated, unit-normalised, one row per real-world event, every
row carrying `bronze_id` (the exact payload it came from) and `resolved_by`
(how its player was identified). Fact tables are **rebuilt from all of Bronze
on every run** — Bronze is small, and a full refresh makes the output a pure
function of Bronze: re-running on the same Bronze produces identical tables.
`dim_player_master` and `vendor_player_map` persist, so `player_sk` never
changes and a vendor id is resolved once.

### Identity

`silver.dim_player_master` — one row per active player from the nflverse
roster snapshot joined to the `import_ids()` crosswalk: `gsis_id`, `nfl_id`,
`espn_id`, `pfr_id`, names, team, position, roster weight/height, and
`clean_name`, the join key for name-only vendors.

`identity.clean_player_name()` mirrors nflverse's `merge_name` mechanics
(lowercase, ASCII, `Jr/Sr/II/III/IV/V` stripped, periods and apostrophes
removed, hyphens kept — asserted in tests against real crosswalk pairs), then
adds what the vendors need: `"Last, First"` is flipped (suffix before the
comma handled: `"Beckham Jr., Odell"`), and a small nickname map collapses
`Mitch/Mitchell`, `Josh/Joshua`, `Ken/Kenneth`… Both sides of every join go
through the same function. Legal first names and football names from the
roster are indexed as aliases, which is how an EMR's `"Dobbs, Robert"`
resolves to the roster's `Joshua Dobbs`.

`PlayerResolver.resolve()` tries, in order, and records which rule won:

| # | `resolved_by` | confidence | used by |
|---|---|---|---|
| 1 | `gsis_id` | 1.0 | forcedeck |
| 2 | `nfl_id` | 1.0 | ams_wellness (`player_id` / `athlete_id`) |
| 3 | `vendor_map` | (stored) | any id seen before — `cat_…` UUIDs, nflIds, every mangled name string |
| 4 | `name_team` | 0.9 | nutrition, emr |
| 5 | `name_unique` | 0.75 | catapult athletes (no team in the feed), wellness players missing from the crosswalk |
| 6 | `unresolved` | — | → `silver.quarantine_identity` with reason `unresolved` (no candidate) or `ambiguous` (several, listed in `candidate_sks`) |

Rules 1, 2, 4, 5 pin their answer into `silver.vendor_player_map (vendor,
vendor_player_id → player_sk, resolved_by, confidence)`. For name-only
vendors the raw name string *is* the vendor id, so each mangled variant is
resolved once. That table is the per-player "resolution by method" source;
`resolved_by` on fact rows is the per-row one.

```sql
select resolved_by, count(*) from silver.forcedeck_tests group by 1;
select vendor, resolved_by, count(*) from silver.vendor_player_map group by 1, 2;
select * from silver.quarantine_identity;   -- currently: one "Marcus Harris" (KC DL vs TEN DB)
```

### Fact tables and their wrinkles

| Table | Dedup key | What Silver had to handle |
|---|---|---|
| `forcedeck_tests` | `test_id`, latest ingest wins | `peak_force` in N or lbf with the label missing ⅓ of the time → `force_unit_inferred` (<1500 is lbf); epoch → UTC; aborted reps and null test types flagged in `qc_flags`, not dropped |
| `nutrition_measurements` | `measurement_id` | hardest names; weight in lb/kg with the label missing *or stale* → `weight_unit_evidence` ∈ label / magnitude / lean_mass (weight can't be below lean mass) / roster (closest to roster weight) / default; `lean_mass` sometimes a percent (`lean_mass_was_pct`) |
| `wellness_surveys` | `survey_id`; resubmissions flagged per player-day | v1/v2 shape from the **keys** (the envelope lies about backfilled rows); Likert 1–5 → 1–10 with the scale **inferred from the day's cohort** (any answer > 5 means the 10-point scale that day) — it flips on exactly `SCALE_CHANGE_DATE` without being told; naive ET times, bogus `+00:00` corrected and flagged |
| `emr_injuries` / `emr_status_updates` | `injury_id` / `update_id`, latest `updated_at` wins | `"Last Jr., First"` names; free-text body parts → canonical + `side` pulled from `(R)` / `L mcl` / `left …` (`side_source`); out-of-order updates ordered by event time; corrections marked `is_correction`; `stale_expected_rtp` when RTP predates the last DNP |
| `catapult_sessions` | `session_id` | `cat_…` ids resolved via `/v1/athletes` names and pinned; speed unit by magnitude (m/s and mph don't overlap, so a stale label loses); distance trusts a `yd` label and flags a missing one — a stale `m` on a yd value is not detectable by magnitude and is left for GX to catch distributionally; late arrival is *not* computed (Bronze lacks emission time and the sim clock runs ahead of wall-clock) — `_ingested_at` is carried so Gold can compare across runs |

Every table has `qc_flags text[]` so nothing is silently dropped or fixed:
Silver records what it did, Gold decides what to trust.

### Tests

```bash
python -m venv .venv && .venv/bin/pip install pytest psycopg2-binary
.venv/bin/python -m pytest airflow/plugins/tests     # 81 tests, no DB needed
.venv/bin/python -m pytest mock_vendors               # the vendors themselves
```

## Reset

Init scripts only run against an empty volume:

```bash
docker compose down -v && docker compose up --build
```
