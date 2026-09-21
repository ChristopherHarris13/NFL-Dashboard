# NFL-Dashboard

GridironOps: a Bronze/Silver/Gold medallion pipeline over deliberately dirty
NFL performance data, orchestrated by Airflow, landing in Postgres.

```
5 mock vendors (FastAPI)  ─┐
nflverse (nfl_data_py)    ─┼─▶  Airflow 2.11 ─▶  Postgres warehouse
Open-Meteo (Gillette)     ─┘    :8080              :5432  bronze / silver / gold

nfl_medallion DAG (every 15 min):
  extract_* (9) ─▶ warehouse_schema ─▶ validate_* (GX) ─▶ silver_* ─▶ dq_summary
                                    └▶ silver_player_master ─┘   └▶ dbt_seed ─▶ dbt_snapshot ─▶ dbt_run ─▶ dbt_test
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
| `airflow/plugins/silver/` | one transform module per source (`build(conn, run_id)`), plus `player_master.py` and shared `common.py` |
| `airflow/plugins/dq/` | `validate.py` runs a source's GX suites and routes failing rows to quarantine; `summary.py` writes the reconciled scorecard |
| `great_expectations/expectations/` | the six expectation suites as JSON — the source of truth, loaded at run time |
| `warehouse/init/04_staging.sql` | `silver.stg_*` views (Bronze unpacked, deduped, unit-converted) that the suites validate; quarantine + scorecard tables |
| `dbt/` | Gold: dims, facts, `mart_player_week`, snapshot, seeds, 114 tests; `dbt/docs/index.html` is the generated lineage/docs site |
| `powerbi/` | the report: `GridironOps.pbip` (Power BI project, text-based), `theme.json`, and the build plan in `docs/powerbi-build-plan.md` |
| `CONTEXT.md` | the glossary: Flag, Availability, Practice/Game status, As-of date, Silent player… |
| `docker-compose.clean.yml`, `mock_vendors/dirt_config.clean.yaml` | overlay that runs every mock with all dirt at 0.0 |
| `airflow/plugins/tests/` | 94 unit tests: name cleaning against real nflverse pairs, resolver rules, unit inference, body-part parsing, suite well-formedness, quarantine routing |
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
`open_meteo` (hourly forecast for Gillette Stadium, ±7 days, one row
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
select * from silver.quarantine_identity;   -- e.g. two active players sharing a name, no team in the feed
```

### Fact tables and their wrinkles

| Table | Dedup key | What Silver had to handle |
|---|---|---|
| `forcedeck_tests` | `test_id`, latest ingest wins | `peak_force` in N or lbf with the label missing ⅓ of the time → `force_unit_inferred` (<1500 is lbf); epoch → UTC; aborted reps and null test types flagged in `qc_flags`, not dropped |
| `nutrition_measurements` | `measurement_id` | hardest names; weight always measured in lb but the label is missing *or wrong* (`kg` on an lb value) → label ignored, value taken as lb, `weight_mislabeled` flagged; `lean_mass` sometimes a percent (`lean_mass_was_pct`) |
| `wellness_surveys` | `survey_id`; resubmissions flagged per player-day | v1/v2 shape from the **keys** (the envelope lies about backfilled rows); Likert 1–5 → 1–10 with the scale **inferred from the day's cohort** (any answer > 5 means the 10-point scale that day) — it flips on exactly `SCALE_CHANGE_DATE` without being told; naive ET times, bogus `+00:00` corrected and flagged |
| `emr_injuries` / `emr_status_updates` | `injury_id` / `update_id`, latest `updated_at` wins | `"Last Jr., First"` names; free-text body parts → canonical + `side` pulled from `(R)` / `L mcl` / `left …` (`side_source`); out-of-order updates ordered by event time; corrections marked `is_correction`; `stale_expected_rtp` when RTP predates the last DNP |
| `catapult_sessions` | `session_id` | `cat_…` ids resolved via `/v1/athletes` names and pinned; speed unit by magnitude (m/s and mph don't overlap, so a stale label loses); distance trusts a `yd` label and flags a missing one — a stale `m` on a yd value is not detectable by magnitude and is left for GX to catch distributionally; late arrival is *not* computed (Bronze lacks emission time and the sim clock runs ahead of wall-clock) — `_ingested_at` is carried so Gold can compare across runs |

Every table has `qc_flags text[]` so nothing is silently fixed: Silver
records what it did. Rows that fail validation never reach Silver at all —
see below.

## Validation and the DQ scorecard

Gold only ever sees rows that passed. Between Bronze and Silver:

1. **Staging views** (`silver.stg_<source>`) unpack the JSON, deduplicate
   (latest ingest per business key) and convert units, so range checks run
   on typed columns in canonical units — a body weight is checked in lb
   regardless of what the vendor's unit label claims.
2. **Great Expectations suites** — one per source in
   `great_expectations/expectations/*.json`, 7–12 expectations each, mapped
   to the injected dirt (aborted reps, null test types, impossible
   distances, null player load, outlier weights and body-fat, Likert answers
   above `scale_max`, unparseable timestamps, `expected_rtp` before
   `event_date`, …). The JSON is loaded at run time; nothing is generated.
3. **`validate_<source>` routes, it doesn't fail.** Every row that fails an
   expectation is written to `silver.quarantine_<source>` with the
   expectation, the column, the observed value and a readable reason
   (`jump_height_cm outside 5..90: jump_height_cm=113.8`). The task succeeds.
   It fails — and stops that source's Silver — only on a *systemic* problem:
   an expectation with ≥ 50 % of the batch unexpected, or a column that has
   vanished. That's an alert, not dirt.
4. **`silver_<source>`** skips quarantined rows, resolves identity, and
   routes the unresolvable to the same quarantine table
   (`expectation_name = 'player_resolved'`), so "why isn't this row in
   Silver?" has exactly one place to look.
5. **`dq_summary`** writes one `silver.dq_run_summary` row per source per
   run and asserts `rows_in − duplicates − quarantined = rows_in_silver`.
   The quarantined count comes from the validator, the rest from Silver, so
   the two can't agree by construction — if the SQL view and the Python
   transform ever dedupe differently, the run fails.
   `silver.dq_expectation_results` keeps one row per expectation per run
   for the trend chart.

```sql
select source, rows_in, duplicates_removed, rows_quarantined, rows_in_silver, reconciles
from silver.dq_run_summary order by created_at desc limit 5;
select expectation_name, column_name, unexpected_count, unexpected_percent
from silver.dq_expectation_results where not success order by validated_at desc;
select reason, count(*) from silver.quarantine_forcedeck group by 1;
```

### What it found

With the default dirt, the observed failure rates match the injected
probabilities to the decimal: catapult `player_load is null` 5.0 % (p = 0.05),
`total_distance_m` outliers 2.0 % (0.02), forcedeck `test_type is null` 3.1 %
(0.03), `jump_height_cm` aborted reps 4.0 % (0.04), nutrition outliers
1.8 % (0.02).

It also found a Silver bug on its first run: 2,203 ForceDecks rows at
450–800 "newtons". The mock mislabels a quarter of its lbf swaps as `N`, and
Silver had trusted the label. lbf (337–1461) and N (1500–6500) never
overlap, so magnitude now decides and the label is only evidence of whether
the vendor got it right.

### The two experiments

**One dirt to 1.0.** `forcedeck_svc.aborted_rep.p: 1.0`, rebuild the mock,
delete the `bronze_cursor__forcedeck__tests` Variable so the re-dirtied
stream lands, run the DAG. `jump_height_cm` failed on 57.3 % of the batch →
`validate_forcedeck` **failed** (`systemic = true` in
`dq_expectation_results`), `silver_forcedeck` did not run and kept the last
good table, 28,331 rows sat in `quarantine_forcedeck` with reasons, and the
other four sources completed normally.

Reverting the config and re-walking does **not** undo it, and that's worth
understanding: the reverted stream is byte-identical to the original rows,
so the hash index lands nothing, and "latest ingest wins" keeps the bad
versions on top. Bronze cannot represent a revert to a payload it has
already seen. The recovery is to delete the experiment's `_batch_id` from
Bronze (or `down -v`); in production the equivalent is a re-emission with
any difference at all, or a dedup rule that prefers the latest *valid*
version — an open design choice noted below.

**All dirt to 0.0.** `docker compose down -v && docker compose -f
docker-compose.yml -f docker-compose.clean.yml up -d`, run the DAG:
61 / 61 expectations pass, 0 duplicates, 0 rows DQ-quarantined across
~70 k rows. The only quarantine entries are the 160 Catapult sessions of the
one genuinely ambiguous name (two active "Marcus Harris", no team in the
feed) — identity, not dirt.

### Tests

```bash
python -m venv .venv && .venv/bin/pip install pytest psycopg2-binary
.venv/bin/python -m pytest airflow/plugins/tests     # 94 tests, no DB needed
.venv/bin/python -m pytest mock_vendors               # the vendors themselves
docker compose exec -w /opt/airflow/dbt airflow dbt test   # 86 Gold tests against the warehouse
```

## Gold (dbt)

`dbt/` — profile reads `DATABASE_URL`; target schema `gold`; sources are
`silver.*` (plus `bronze.nflverse` for the two clean real feeds). `dbt build`
runs 2 seeds, 1 snapshot, 23 models and 114 tests. The Airflow tasks
`dbt_seed → dbt_snapshot → dbt_run → dbt_test` follow every Silver rebuild.

```bash
docker compose exec -w /opt/airflow/dbt airflow dbt build
open dbt/docs/index.html          # lineage graph + column docs (dbt docs generate --static)
```

### Dimensions

| Model | What |
|---|---|
| `dim_date` | `dbt_utils.date_spine` from `SIM_START_DATE`; `season_week` = Monday-anchored weeks since camp; `day_type` off/practice/walkthrough/game from the mocks' schedule |
| `dim_team` | teams on the nflverse roster + `seeds/stadiums.csv` (lat/long, `is_dome`, surface) |
| `dim_player` | **SCD-2** from `snapshots/dim_player_snapshot.sql` (check strategy on name/team/position/weight/height/roster status) over `silver.dim_player_master`; one row per version with `valid_from`/`valid_to`/`is_current`, stable `player_sk` across versions |
| `dim_player_current` | the `is_current` row of `dim_player`, one per player — the report's Player table |
| `dim_injury_type` | `seeds/injury_body_parts.csv`: EMR free text → `body_part`, `body_region`, `side`. Edit the CSV to teach it a new phrasing |

### Facts

| Model | Grain | Notable columns |
|---|---|---|
| `fact_training_load` | player × day, complete zero-filled grid | `external_load` (Catapult player_load), `internal_load` (sRPE × session minutes), `acute_7d`, `chronic_28d` (28-day sum / 4), `acwr_coupled`, `acwr_ewma` (EWMA 7 / EWMA 28, normalised weights, 56-day lookback), `internal_acwr`, `acwr_band` low <0.8 · sweet 0.8–1.3 · elevated 1.3–1.5 · high >1.5. ACWR is null until 28 days of history |
| `fact_wellness` | player × day (latest submission) | Likert on 1–10, `readiness_score` = 0.20 sleep_quality + 0.15 sleep_hours + 0.25 (11−soreness) + 0.20 (11−fatigue) + 0.10 (11−stress) + 0.10 mood |
| `fact_strength` | one per force-plate test | `asymmetry_4w_avg` (trailing 28 days), `asymmetry_trend` (trailing 4 weeks minus the 4 before) |
| `fact_nutrition` | one per measurement | `method_rank`, `is_preferred` — DEXA over BIA over scale when a date has several |
| `fact_injury` | one per injury | `days_out` to the first FP status (or to today if `is_open`), `body_region`/`side` via the seed |
| `fact_injury_status` | injury × day | status carried forward from the latest update on or before the day; `is_unavailable` = DNP |
| `fact_availability` | player × season week | `availability_pct` = (practices + games not DNP) ÷ scheduled |
| `fact_player_day` | player × day | the report's as-of row: availability, game status, readiness vs own 28-day mean, days since survey, ACWR, asymmetry, weight, open injuries, `flag` + `flag_reasons` (see `CONTEXT.md`) |
| `fact_play` | player × game (nflverse) | EPA by role (passer/rusher/receiver, GSIS-keyed), snap counts (PFR-keyed via the crosswalk), mapped onto `season_week` by game date |

### `mart_player_week`

One row per tracked player (anyone with Silver activity — 72) per season
week (33): end-of-week ACWR and band, weekly load, readiness, asymmetry,
preferred weight, availability, new and open injuries with practice status,
EPA and snaps. A player's slice reads like a season log:

```
 wk | full_name  | load_wk |  acwr | band     | ready | weight_lbs | avail | inj | body_part | status | epa
  3 | Joe Burrow |  1409.1 |       |          |  6.10 |      212.3 | 1.000 |   0 |           | FP     |
  4 | Joe Burrow |  2720.3 | 1.243 | sweet    |  6.78 |      214.4 | 0.750 |   1 | hamstring | DNP    |
  5 | Joe Burrow |  3408.5 | 1.364 | elevated |  5.61 |            | 0.000 |   0 |           | DNP    |
  6 | Joe Burrow |  2745.7 | 1.068 | sweet    |  6.45 |      219.1 | 0.500 |   0 |           | LP     |
  7 | Joe Burrow |  2736.3 | 0.943 | sweet    |  6.42 |            | 1.000 |   0 |           | LP     |
  8 | Joe Burrow |  2693.7 | 0.930 | sweet    |  6.55 |      221.8 | 1.000 |   0 |           | FP     | -2.14
```

### Does the injected load → injury correlation flow through?

Partly, and the reason is instructive. The simulator raises injury risk 6×
when its hidden load *driver* has ACWR > 1.5 three days earlier (spike
weeks are ×2.2). But the Catapult mock maps that driver onto `player_load`
through a clamp at 1.7× and a linear map with a 200 offset, which
arithmetically turns a ×2.2 spike week into an observed ACWR of ≈ 1.2. In
the feed the pipeline actually receives, weekly load never exceeds 1.55× a
player's own mean (p95 = 1.25×), so `acwr_band = 'high'` is essentially
unpopulated — 0.2 % of player-days — whatever the pipeline does.

What *is* visible: next-week injury rate by end-of-week band is low 3.3 % →
sweet 5.0 % → elevated 8.6 %, and daily hazard is 15.6 vs 11.5 injuries per
1,000 player-days above/below ACWR 1.15 at d−3 (where the mock evaluates
it). To make the >1.5 band light up as the simulator's docstring intends,
the Catapult generator would need to preserve spike magnitude in
`total_distance`/`player_load` rather than compress it — a mock change,
not a Gold one.

## Report (Power BI)

The reader is performance and medical staff: who's red, who's amber, who
plays. Five pages over `gold.*`, built in Power BI Desktop against the local
warehouse — the step-by-step is [`docs/powerbi-build-plan.md`](docs/powerbi-build-plan.md),
the vocabulary is [`CONTEXT.md`](CONTEXT.md), the files land in [`powerbi/`](powerbi/).

| Page | Answers |
|---|---|
| **Today** | the roster board on the as-of day: Flag (red / amber / green with reasons), availability and game status in NFL injury-report terms, readiness vs the player's own 28-day mean, ACWR, 7-day load, days since survey, open injury and expected return. Drill to Player. |
| **Load** | 7-day load vs each player's own norm; players by ACWR band by day |
| **Injury report** | open injuries as the department files them: body part, side, practice/game status, expected return |
| **Data** | last run, reconciles, quarantined %, expectations passed — why the numbers can be trusted |
| **Player** | one player's trailing 28 days (or season): ACWR, load, readiness, asymmetry, weight, injuries |

The Flag lives in `gold.fact_player_day` (one row per player per day, dbt-tested):
red = Out, game status Out/Doubtful, or ACWR high · amber = Limited, any other
open injury, ACWR elevated, readiness 1.5+ below own 28-day mean, asymmetry
> 10 %, or no survey in 3 days · green otherwise, with `flag_reasons` spelled out.
The report's as-of day defaults to the latest data day and steps back with a
"days back" control, so every page is a filter on that table.

## Reset

Init scripts only run against an empty volume:

```bash
docker compose down -v && docker compose up --build
```
