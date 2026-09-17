"""GridironOps medallion pipeline.

Bronze: land every source's raw payloads (cursor-walked mocks, snapshot real
feeds). Validate: Great Expectations suites against the unpacked staging
views route failing rows to silver.quarantine_<source> (the task fails only
on systemic breakage). Silver: resolve identity once (dim_player_master +
vendor_player_map), rebuild one typed, deduplicated table per source from the
rows that passed. dq_summary: one reconciled scorecard row per source per run.

    extract_* -> warehouse_schema -> validate_* -> silver_* -> dq_summary
                                                            -> dbt_seed -> dbt_snapshot -> dbt_run -> dbt_test

Gold is dbt (dbt/): dims, facts and mart_player_week, built from Silver only.

Mock vendors are walked by cursor (airflow/plugins/extract.py). The two real
feeds have no cursor: each run lands the full current snapshot and the
(_payload_hash, _source) unique index drops anything already seen.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

import httpx
from airflow.decorators import dag, task
from airflow.operators.bash import BashOperator

from extract import extract_resource, land_records

log = logging.getLogger(__name__)

# Compose service name -> (bronze source, resources). One extract task per resource.
MOCK_VENDORS = {
    "catapult_svc":     ("catapult",     8001, ["sessions", "athletes"]),
    "forcedeck_svc":    ("forcedeck",    8002, ["tests"]),
    "ams_wellness_svc": ("ams_wellness", 8003, ["surveys"]),
    "nutrition_svc":    ("nutrition",    8004, ["measurements"]),
    "emr_svc":          ("emr",          8005, ["injuries", "status_updates"]),
}

PBP_COLUMNS = ["play_id", "game_id", "season", "week", "game_date", "posteam", "defteam", "play_type",
               "passer_player_id", "rusher_player_id", "receiver_player_id", "epa", "success",
               "yards_gained", "touchdown", "qb_epa", "air_epa", "yac_epa"]

# GEHA Field at Arrowhead Stadium (SEED_TEAM=KC in the mocks).
STADIUM = {
    "stadium": "GEHA Field at Arrowhead Stadium",
    "team": "KC",
    "latitude": 39.0489,
    "longitude": -94.4839,
    "timezone": "America/Chicago",
}
OPEN_METEO_HOURLY = [
    "temperature_2m", "relative_humidity_2m", "apparent_temperature",
    "precipitation", "wind_speed_10m", "wind_gusts_10m", "weather_code",
]


@dag(
    dag_id="nfl_medallion",
    schedule="*/15 * * * *",
    start_date=datetime(2026, 9, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=1)},
    tags=["bronze"],
)
def nfl_medallion():

    extracts = []

    # ------------------------------------------------------------ mock vendors
    for service, (source, port, resources) in MOCK_VENDORS.items():
        for resource in resources:
            task_id = f"extract_{source}" if len(resources) == 1 else f"extract_{source}_{resource}"

            @task(task_id=task_id)
            def extract_mock(source: str = source, resource: str = resource,
                             base_url: str = f"http://{service}:{port}") -> dict:
                return extract_resource(source=source, base_url=base_url, resource=resource)

            extracts.append(extract_mock())

    # -------------------------------------------------------------- nflverse
    @task
    def extract_nflverse() -> dict:
        import nfl_data_py as nfl

        batch_id = uuid.uuid4()
        stats = {}

        rosters = None
        for season in (2026, 2025):
            try:
                rosters = nfl.import_seasonal_rosters([season])
                if len(rosters):
                    break
            except Exception as exc:  # nflverse 404s raise assorted errors
                log.warning("season %s rosters unavailable (%s)", season, exc)
        if rosters is None or not len(rosters):
            raise RuntimeError("no roster data from nflverse")
        stats["rosters"] = land_records(
            source="nflverse", endpoint="rosters", batch_id=batch_id,
            records=_df_records(rosters), schema_version=f"season={season}",
        )

        ids = nfl.import_ids()
        stats["ids"] = land_records(
            source="nflverse", endpoint="ids", batch_id=batch_id, records=_df_records(ids),
        )
        # Play-by-play is ~370 columns; land the ones Gold's fact_play needs.
        # Values are untouched, this is a column projection, not a transform.
        pbp = nfl.import_pbp_data([season], columns=PBP_COLUMNS, downcast=False)
        stats["pbp"] = land_records(
            source="nflverse", endpoint="pbp", batch_id=batch_id, records=_df_records(pbp),
            schema_version=f"season={season}",
        )
        snaps = nfl.import_snap_counts([season])
        stats["snap_counts"] = land_records(
            source="nflverse", endpoint="snap_counts", batch_id=batch_id, records=_df_records(snaps),
            schema_version=f"season={season}",
        )
        log.info("nflverse: %s new rows", stats)
        return stats

    # ------------------------------------------------------------ open-meteo
    @task
    def extract_open_meteo() -> dict:
        batch_id = uuid.uuid4()
        params = {
            "latitude": STADIUM["latitude"],
            "longitude": STADIUM["longitude"],
            "timezone": STADIUM["timezone"],
            "hourly": ",".join(OPEN_METEO_HOURLY),
            "past_days": 7,
            "forecast_days": 7,
        }
        body = httpx.get("https://api.open-meteo.com/v1/forecast", params=params,
                         timeout=30).raise_for_status().json()
        hourly, units = body["hourly"], body["hourly_units"]
        # One payload per hour; the API's array-of-columns shape is unwound
        # here but the values themselves are untouched.
        records = [
            {**STADIUM, "time": t,
             **{k: hourly[k][i] for k in OPEN_METEO_HOURLY},
             "units": {k: units[k] for k in OPEN_METEO_HOURLY}}
            for i, t in enumerate(hourly["time"])
        ]
        inserted = land_records(source="open_meteo", endpoint="forecast",
                                batch_id=batch_id, records=records)
        log.info("open_meteo: %d hours fetched, %d new", len(records), inserted)
        return {"fetched": len(records), "inserted": inserted}

    extracts += [extract_nflverse(), extract_open_meteo()]

    # ---------------------------------------------------------------- schema
    @task
    def warehouse_schema() -> None:
        """Apply warehouse/init/03_silver.sql + 04_staging.sql (all IF NOT EXISTS /
        OR REPLACE) so a warehouse created earlier picks up new objects without a reset."""
        from silver.common import warehouse_conn
        with warehouse_conn() as conn, conn.cursor() as cur:
            for f in ("03_silver.sql", "04_staging.sql"):
                cur.execute(open(f"/opt/airflow/warehouse/init/{f}").read())
            conn.commit()

    # -------------------------------------------------------------- validate
    def validate_task(name: str, source: str):
        @task(task_id=f"validate_{name}")
        def _run(run_id: str = None) -> dict:
            from airflow.providers.postgres.hooks.postgres import PostgresHook
            from dq.validate import validate
            hook = PostgresHook(postgres_conn_id="warehouse")
            c = hook.get_connection("warehouse")
            url = f"postgresql+psycopg2://{c.login}:{c.password}@{c.host}:{c.port or 5432}/{c.schema}"
            return validate(hook.get_conn(), source, run_id, url)
        return _run()

    # ---------------------------------------------------------------- silver
    @task
    def silver_player_master() -> dict:
        from silver import player_master
        from silver.common import warehouse_conn
        return player_master.build(warehouse_conn())

    def silver_task(name: str):
        @task(task_id=f"silver_{name}")
        def _run(run_id: str = None) -> dict:
            import importlib
            from silver.common import warehouse_conn
            return importlib.import_module(f"silver.{name}").build(warehouse_conn(), run_id=run_id)
        return _run()

    @task
    def dq_summary(validations: list, silvers: list, run_id: str = None) -> list:
        from dq.summary import write_summary
        from silver.common import warehouse_conn
        return write_summary(warehouse_conn(), run_id, validations, silvers)

    # source name in Bronze/identity -> (validate/silver task suffix)
    SOURCES = {"forcedeck": "forcedeck", "catapult": "catapult", "ams_wellness": "wellness",
               "nutrition": "nutrition", "emr": "emr"}

    schema = warehouse_schema()
    master = silver_player_master()
    extracts >> schema >> master
    validations, silvers = [], []
    for source, name in SOURCES.items():
        v = validate_task(name, source)
        s = silver_task(name)
        schema >> v
        [v, master] >> s
        validations.append(v)
        silvers.append(s)
    summary = dq_summary(validations, silvers)

    # ------------------------------------------------------------------ gold
    # dbt/profiles.yml reads DATABASE_URL; DBT_PROFILES_DIR / DBT_PROJECT_DIR
    # are set in docker-compose.yml. dbt deps runs once (dbt_packages/ is
    # bind-mounted and gitignored).
    dbt = "cd $DBT_PROJECT_DIR && (test -d dbt_packages/dbt_utils || dbt deps) && dbt"
    dbt_seed = BashOperator(task_id="dbt_seed", bash_command=f"{dbt} seed")
    dbt_snapshot = BashOperator(task_id="dbt_snapshot", bash_command=f"{dbt} snapshot")
    dbt_run = BashOperator(task_id="dbt_run", bash_command=f"{dbt} run")
    dbt_test = BashOperator(task_id="dbt_test", bash_command=f"{dbt} test")
    silvers >> dbt_seed >> dbt_snapshot >> dbt_run >> dbt_test


def _df_records(df) -> list[dict]:
    """DataFrame -> JSON-safe dicts (NaN -> null, dates -> ISO) without altering values."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


nfl_medallion()
