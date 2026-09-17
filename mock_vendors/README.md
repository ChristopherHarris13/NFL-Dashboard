# GridironOps mock vendors

Five FastAPI services that emit **deliberately dirty** NFL performance data for a
Bronze/Silver/Gold medallion pipeline. The dirt is intentional — never make the
data clean. Dial it up or down in [`dirt_config.yaml`](dirt_config.yaml)
(every defect has a probability; `0.0` disables it).

## Services

| Service | Port | Resource(s) | Mimics |
|---|---|---|---|
| `catapult_svc` | 8001 | `/v1/sessions`, `/v1/athletes` | Catapult GPS/LPS practice & game load; athlete list (names, no league id) emitted once on day 0 |
| `forcedeck_svc` | 8002 | `/v1/tests` | VALD ForceDecks dual force plates |
| `ams_wellness_svc` | 8003 | `/v1/surveys` | Teamworks/Kitman daily wellness |
| `nutrition_svc` | 8004 | `/v1/measurements` | Dietitian body-comp exports (DEXA/BIA/scale) |
| `emr_svc` | 8005 | `/v1/injuries`, `/v1/status_updates` | Team EMR / NFL injury report |

## Identity styles (on purpose, all different)

| Service | Player identity |
|---|---|
| catapult | Vendor UUID `cat_<8 hex>`; the gsis mapping is internal and **never exposed**. `/v1/athletes` gives `athlete_id` + first/last name + position (like OpenField's athlete export), so ids resolve by name |
| forcedeck | NFL GSIS id, `00-00xxxxx` |
| ams_wellness | Integer `nflId` (`player_id`, renamed `athlete_id` in schema v2) |
| nutrition | **Name only**, heavily mangled (`A.J.`/`AJ`, nicknames, `Jr.`/`III` come and go, occasional `Last First`) |
| emr | `Last, First` with suffixes preserved (`Beckham Jr., Odell`), no id field |

The pipeline must resolve all five to one `player_sk` via the nflverse crosswalk.

## Timestamp formats (on purpose, all different)

| Service | Format | Example |
|---|---|---|
| catapult | ISO 8601 UTC with Z | `2026-09-10T15:42:00Z` |
| forcedeck | Unix epoch seconds (int) | `1786454520` |
| ams_wellness | Naive local, no tz (really facility ET) | `2026-09-10 07:15:00` |
| nutrition | Date only, US style | `09/10/2026` |
| emr | ISO 8601 with US/Eastern offset | `2026-09-10T14:05:00-04:00` |

## API contract (all services)

- `GET /health` → `{"status": "ok", "service": "<name>", "records": <count>}`
- `GET /v1/<resource>?since=<cursor>&limit=<n>` → `{"data": [...], "next_cursor": <str|null>, "schema_version": "v1"|"v2"}`
- `limit` defaults to 100, max 500. Omit `since` to read from the beginning;
  walk `next_cursor` until `data` is empty (`next_cursor` becomes `null`).
- The cursor is an opaque string encoding `(emitted_at, record_seq)` and orders
  by **emission** time, not event time — late-arriving records appear at the
  end of the stream with old event timestamps.

## Simulated calendar

All five services share one simulated clock (`common/sim_clock.py`), starting
at `SIM_START_DATE` (default `2026-07-20`, training camp, a Monday):
Mon/Tue off · Wed/Thu/Fri practice · Sat walkthrough · Sun game.

On boot each service backfills `BACKFILL_DAYS` (default 45) so ACWR windows
have history, then advances one simulated day every `GEN_INTERVAL_SECONDS`
(default 300). The day index is anchored to UTC midnight (override with
`SIM_WALL_EPOCH`), and generation is a **pure function** of `GLOBAL_SEED` and
the day — so containers that boot minutes apart, or restart, produce identical
streams and agree on the simulated date for any emission.

That determinism also powers cross-service correlations without any
cross-service traffic (`common/simulation.py`):
- soreness/fatigue in wellness surveys rise after heavy Catapult load days;
- a few players' ForceDecks `asymmetry_pct` drifts toward +12 over the season;
- new EMR injuries are far more likely when 7-day load spiked >1.5× the 28-day
  average in the prior week, or asymmetry exceeds 10.

## Dirt tables

Probabilities below are the defaults in `dirt_config.yaml`.

### catapult_svc
| Defect | p | What it does |
|---|---|---|
| `unit_swap_distance` | 0.25 | m→yd; 40% keep stale `m` label, 30% omit `distance_unit`, 30% honest |
| `unit_swap_speed` | 0.25 | m/s→mph, same label behavior |
| `outlier_distance` | 0.02 | `total_distance` 30000–50000 |
| `late_emit` | 0.15 | emitted 1–3 sim days after the session |
| `missing_session` | 0.08 | player-day silently absent |
| `null_player_load` | 0.05 | `player_load: null` |

### forcedeck_svc
| Defect | p | What it does |
|---|---|---|
| `duplicate` | 0.12 | same `test_id` re-emitted 2–3×, one copy with `peak_force` re-rounded |
| `unit_swap_force` | 0.30 | N→lbf; `force_unit` omitted 50% of the time |
| `aborted_rep` | 0.04 | `jump_height_cm` < 5 or > 90 |
| `null_test_type` | 0.03 | `test_type: null` |
| `missing_device` | 0.05 | `device_id` key dropped |

### ams_wellness_svc
| Defect | p | What it does |
|---|---|---|
| `skipped_day` | 0.15 | no survey that day |
| `duplicate_submission` | 0.06 | second record 2–20 min later, 1–2 answers changed |
| `wrong_tz` | 0.05 | `+00:00` appended to a local time that is really ET |
| scale change | date | Likert 1–5 becomes 1–10 on `SCALE_CHANGE_DATE` (default 2026-08-25), unannounced until v2 |
| schema drift | date | on `SCHEMA_DRIFT_DATE` (default 2026-09-01): `player_id`→`athlete_id`, `name` splits to first/last, `scale_max` appears, `schema_version: v2` |

### nutrition_svc
| Defect | p | What it does |
|---|---|---|
| `unit_swap_weight` | 0.30 | lb→kg; `weight_unit` omitted 40% of the time |
| `method_disagreement` | 0.20 | DEXA and BIA same date, `body_fat_pct` differs 3–6 pts |
| `outlier` | 0.02 | weight 600 or 45; body_fat 1.5 |
| `lean_mass_ambiguity` | 0.35 | `lean_mass` as a percent instead of absolute, no indicator |
| `sparse` | 0.20 | scheduled weigh-in skipped |
| `name_mangle` | 0.50 | see identity table |

### emr_svc
| Defect | p | What it does |
|---|---|---|
| `free_text_body_part` | 1.0 | `hammy`, `Knee (R)`, `high ankle`, `MCL`, `concussion protocol`, … |
| `out_of_order_updates` | 0.20 | Thursday's update emitted before Wednesday's |
| `correction_overwrite` | 0.10 | existing `update_id` re-emitted with changed status, later `updated_at` |
| `side_embedded` | 0.30 | `side` key dropped, L/R embedded in `body_part` text |
| `name_mangle` | 0.30 | suffix stripped, order flipped, uppercased, periods removed |
| `stale_expected_rtp` | 0.15 | `expected_rtp` earlier than the last DNP update |

## Player seed

`common/seed/players.parquet` caches all active players (real 2026 rosters via
`nfl_data_py.import_seasonal_rosters` + `import_ids`) so services boot offline.
The roster is every active player on `SEED_TEAM` (default `KC`, capped at 55)
plus 20 random others — ~75 players, sampled deterministically from
`GLOBAL_SEED`. Rebuild with:

```bash
python mock_vendors/common/build_seed.py          # needs nfl_data_py
```

(`nfl_data_py` pins pandas<2; on modern Pythons install `pandas>=2` first,
then `pip install --no-deps nfl_data_py`.)

## Running

```bash
docker compose up --build      # from the repo root; all five on 8001-8005
```

or locally:

```bash
pip install -r mock_vendors/requirements.txt
uvicorn mock_vendors.catapult_svc.app:app --port 8001   # etc.
```

Env vars: `PORT`, `GEN_INTERVAL_SECONDS`, `BACKFILL_DAYS`, `SIM_START_DATE`,
`SCHEMA_DRIFT_DATE`, `SCALE_CHANGE_DATE`, `DIRT_CONFIG_PATH`, `SEED_TEAM`,
`GLOBAL_SEED`, `SIM_WALL_EPOCH`.

## Tests

```bash
pip install pytest httpx
python -m pytest mock_vendors
```

Per service: `/health` ok; cursor walk covers the full stream with no gaps or
repeats; all dirt probabilities at 0.0 → every record validates against the
clean Pydantic models in `common/clean_schemas.py`; duplicate p=1.0 → every
record has ≥2 copies. Plus cross-service tests for calendar agreement and the
load→soreness and ACWR/asymmetry→injury correlations.
