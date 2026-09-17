-- Staging views: Bronze payloads unpacked, deduplicated (latest ingest per
-- business key) and unit-converted, so Great Expectations validates typed
-- columns in canonical units — the range checks are meaningless on raw JSON
-- where a weight might be lb or kg. Views, so they are always current.
--
-- These conversions are the validation surface; silver/*.py is the source of
-- truth for Silver values (its nutrition inference also uses roster weight,
-- which needs identity and isn't available here). The DQ summary asserts the
-- two agree on row counts every run.
--
-- Also: per-source quarantine tables and the DQ scorecard tables.

-- ------------------------------------------------------------- forcedeck
CREATE OR REPLACE VIEW silver.stg_forcedeck AS
WITH d AS (
    SELECT id AS bronze_id, payload, _schema_version, _ingested_at,
           row_number() OVER (PARTITION BY payload->>'test_id' ORDER BY _ingested_at DESC, id DESC) AS rn
    FROM bronze.forcedeck WHERE _endpoint = '/v1/tests'
)
SELECT bronze_id,
       payload->>'test_id'                                   AS test_id,
       payload->>'athlete_gsis_id'                           AS athlete_gsis_id,
       to_timestamp((payload->>'test_ts')::bigint)           AS test_ts,
       payload->>'test_type'                                 AS test_type,
       (payload->>'rep')::int                                AS rep,
       (payload->>'jump_height_cm')::numeric                 AS jump_height_cm,
       (payload->>'peak_force')::numeric                     AS peak_force_raw,
       payload->>'force_unit'                                AS force_unit,
       -- lbf (337-1461) and N (1500-6500) never overlap: magnitude decides, labels lie.
       CASE WHEN (payload->>'peak_force')::numeric < 1500 THEN (payload->>'peak_force')::numeric * 4.4482216152605
            ELSE (payload->>'peak_force')::numeric END       AS peak_force_n,
       (payload->>'rfd')::numeric                            AS rfd,
       (payload->>'asymmetry_pct')::numeric                  AS asymmetry_pct,
       payload->>'device_id'                                 AS device_id,
       _schema_version, _ingested_at
FROM d WHERE rn = 1;

-- -------------------------------------------------------------- catapult
CREATE OR REPLACE VIEW silver.stg_catapult AS
WITH d AS (
    SELECT id AS bronze_id, payload, _schema_version, _ingested_at,
           row_number() OVER (PARTITION BY payload->>'session_id' ORDER BY _ingested_at DESC, id DESC) AS rn
    FROM bronze.catapult WHERE _endpoint = '/v1/sessions'
)
SELECT bronze_id,
       payload->>'session_id'                                AS session_id,
       payload->>'player_id'                                 AS player_id,
       CASE WHEN payload->>'session_ts' ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'
            THEN (payload->>'session_ts')::timestamptz END   AS session_ts,
       payload->>'session_type'                              AS session_type,
       (payload->>'duration_min')::int                       AS duration_min,
       payload->>'distance_unit'                             AS distance_unit,
       CASE WHEN payload->>'distance_unit' = 'yd' THEN (payload->>'total_distance')::numeric * 0.9144
            ELSE (payload->>'total_distance')::numeric END   AS total_distance_m,
       CASE WHEN payload->>'distance_unit' = 'yd' THEN (payload->>'high_speed_distance')::numeric * 0.9144
            ELSE (payload->>'high_speed_distance')::numeric END AS high_speed_distance_m,
       payload->>'speed_unit'                                AS speed_unit,
       CASE WHEN (payload->>'max_speed')::numeric > 12 THEN (payload->>'max_speed')::numeric / 2.2369362920544
            ELSE (payload->>'max_speed')::numeric END        AS max_speed_ms,
       (payload->>'sprint_count')::int                       AS sprint_count,
       (payload->>'accel_count')::int                        AS accel_count,
       (payload->>'decel_count')::int                        AS decel_count,
       (payload->>'player_load')::numeric                    AS player_load,
       _schema_version, _ingested_at
FROM d WHERE rn = 1;

-- -------------------------------------------------------------- wellness
CREATE OR REPLACE VIEW silver.stg_wellness AS
WITH d AS (
    SELECT id AS bronze_id, payload, _schema_version, _ingested_at,
           row_number() OVER (PARTITION BY payload->>'survey_id' ORDER BY _ingested_at DESC, id DESC) AS rn
    FROM bronze.ams_wellness WHERE _endpoint = '/v1/surveys'
), u AS (
    SELECT bronze_id, payload, _schema_version, _ingested_at,
           left(payload->>'submitted_at', 10)                            AS survey_date_str,
           (payload->>'scale_max')::int                                  AS scale_max_declared,
           greatest((payload->>'sleep_quality')::numeric, (payload->>'soreness')::numeric,
                    (payload->>'fatigue')::numeric, (payload->>'stress')::numeric,
                    (payload->>'mood')::numeric)                         AS likert_max
    FROM d WHERE rn = 1
)
SELECT bronze_id,
       payload->>'survey_id'                                              AS survey_id,
       COALESCE(payload->>'athlete_id', payload->>'player_id')::bigint    AS nfl_id,
       COALESCE(payload->>'name', concat_ws(' ', payload->>'first_name', payload->>'last_name')) AS player_name,
       CASE WHEN payload ? 'athlete_id' OR payload ? 'scale_max' THEN 'v2' ELSE 'v1' END AS schema_shape,
       payload->>'submitted_at'                                           AS submitted_at_raw,
       CASE WHEN payload->>'submitted_at' ~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}'
            THEN left(payload->>'submitted_at', 19)::timestamp AT TIME ZONE 'America/New_York' END AS submitted_at,
       (payload->>'submitted_at') ~ '\+00:00$'                            AS tz_claimed_utc,
       -- Scale: declared by v2; otherwise the day's cohort decides (any answer > 5 means 1-10).
       COALESCE(scale_max_declared,
                CASE WHEN max(likert_max) OVER (PARTITION BY survey_date_str) > 5 THEN 10 ELSE 5 END) AS scale_max,
       scale_max_declared IS NULL                                         AS scale_inferred,
       (payload->>'sleep_hours')::numeric                                 AS sleep_hours,
       (payload->>'sleep_quality')::numeric                               AS sleep_quality,
       (payload->>'soreness')::numeric                                    AS soreness,
       (payload->>'fatigue')::numeric                                     AS fatigue,
       (payload->>'stress')::numeric                                      AS stress,
       (payload->>'mood')::numeric                                        AS mood,
       (payload->>'srpe')::int                                            AS srpe,
       _schema_version, _ingested_at
FROM u;

-- ------------------------------------------------------------- nutrition
CREATE OR REPLACE VIEW silver.stg_nutrition AS
WITH d AS (
    SELECT id AS bronze_id, payload, _schema_version, _ingested_at,
           row_number() OVER (PARTITION BY payload->>'measurement_id' ORDER BY _ingested_at DESC, id DESC) AS rn
    FROM bronze.nutrition WHERE _endpoint = '/v1/measurements'
), u AS (
    SELECT *, (payload->>'weight')::numeric AS w, (payload->>'lean_mass')::numeric AS lean,
           payload->>'weight_unit' AS weight_unit
    FROM d WHERE rn = 1
)
SELECT bronze_id,
       payload->>'measurement_id'                            AS measurement_id,
       payload->>'player_name'                               AS player_name,
       payload->>'team'                                      AS team,
       CASE WHEN payload->>'measured_on' ~ '^\d{2}/\d{2}/\d{4}$'
            THEN to_date(payload->>'measured_on', 'MM/DD/YYYY') END AS measured_on,
       payload->>'method'                                    AS method,
       w                                                     AS weight_raw,
       weight_unit,
       -- Same evidence ladder as silver/nutrition.py minus the roster tiebreak.
       CASE WHEN weight_unit = 'kg' THEN w
            WHEN w < 130 THEN w
            WHEN w > 200 THEN w * 0.45359237
            WHEN lean >= 100 AND w < lean THEN w
            ELSE w * 0.45359237 END                          AS weight_kg,
       (payload->>'body_fat_pct')::numeric                   AS body_fat_pct,
       lean                                                  AS lean_mass_raw,
       lean < 100                                            AS lean_mass_is_pct,
       payload->>'hydration_status'                          AS hydration_status,
       _schema_version, _ingested_at
FROM u;

-- ------------------------------------------------------------------- emr
CREATE OR REPLACE VIEW silver.stg_emr_injuries AS
WITH d AS (
    SELECT id AS bronze_id, payload, _schema_version, _ingested_at,
           row_number() OVER (PARTITION BY payload->>'injury_id' ORDER BY _ingested_at DESC, id DESC) AS rn
    FROM bronze.emr WHERE _endpoint = '/v1/injuries'
)
SELECT bronze_id,
       payload->>'injury_id'                                 AS injury_id,
       payload->>'player'                                    AS player,
       payload->>'team'                                      AS team,
       CASE WHEN payload->>'event_date' ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$'
            THEN (payload->>'event_date')::timestamptz END   AS event_ts,
       CASE WHEN payload->>'event_date' ~ '^\d{4}-\d{2}-\d{2}'
            THEN left(payload->>'event_date', 10)::date END  AS event_date,
       payload->>'body_part'                                 AS body_part,
       payload->>'side'                                      AS side,
       payload->>'injury_type'                               AS injury_type,
       payload->>'severity'                                  AS severity,
       CASE WHEN payload->>'expected_rtp' ~ '^\d{4}-\d{2}-\d{2}$'
            THEN (payload->>'expected_rtp')::date END        AS expected_rtp,
       _schema_version, _ingested_at
FROM d WHERE rn = 1;

CREATE OR REPLACE VIEW silver.stg_emr_status_updates AS
WITH d AS (
    SELECT id AS bronze_id, payload, _schema_version, _ingested_at,
           -- A correction re-emits the update_id with a later updated_at: latest event time wins.
           row_number() OVER (PARTITION BY payload->>'update_id'
                              ORDER BY (payload->>'updated_at')::timestamptz DESC, _ingested_at DESC, id DESC) AS rn
    FROM bronze.emr WHERE _endpoint = '/v1/status_updates'
)
SELECT bronze_id,
       payload->>'update_id'                                 AS update_id,
       payload->>'injury_id'                                 AS injury_id,
       payload->>'player'                                    AS player,
       CASE WHEN payload->>'updated_at' ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$'
            THEN (payload->>'updated_at')::timestamptz END   AS updated_at,
       payload->>'practice_status'                           AS practice_status,
       payload->>'game_status'                               AS game_status,
       payload->>'note'                                      AS note,
       _schema_version, _ingested_at
FROM d WHERE rn = 1;

-- ------------------------------------------------------------ quarantine
-- One row per (bronze row, failed expectation). Replaced per run so it is the
-- current state; history is in dq_expectation_results. Identity failures
-- from silver/*.py land here too (expectation_name = 'player_resolved').
CREATE OR REPLACE FUNCTION silver.create_quarantine_table(source text) RETURNS void AS $$
BEGIN
    EXECUTE format($sql$
        CREATE TABLE IF NOT EXISTS silver.%1$I (
            id                bigserial PRIMARY KEY,
            bronze_id         bigint NOT NULL,
            endpoint          text NOT NULL,
            run_id            text NOT NULL,
            expectation_name  text NOT NULL,   -- e.g. expect_column_values_to_be_between
            column_name       text,
            observed_value    text,
            reason            text NOT NULL,   -- human-readable: "jump_height_cm = 113.8, expected 5..90"
            quarantined_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS %2$I ON silver.%1$I (run_id, bronze_id);
    $sql$, 'quarantine_' || source, 'quarantine_' || source || '_run_bronze_idx');
END;
$$ LANGUAGE plpgsql;

SELECT silver.create_quarantine_table('forcedeck');
SELECT silver.create_quarantine_table('catapult');
SELECT silver.create_quarantine_table('wellness');
SELECT silver.create_quarantine_table('nutrition');
SELECT silver.create_quarantine_table('emr');

-- ------------------------------------------------------------- scorecard
CREATE TABLE IF NOT EXISTS silver.dq_expectation_results (
    id                 bigserial PRIMARY KEY,
    run_id             text NOT NULL,
    source             text NOT NULL,
    suite              text NOT NULL,
    expectation_name   text NOT NULL,
    column_name        text,
    kwargs             jsonb,
    success            boolean NOT NULL,
    element_count      bigint,
    unexpected_count   bigint,
    unexpected_percent numeric,
    systemic           boolean NOT NULL DEFAULT false,   -- would have failed the task
    validated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, source, suite, expectation_name, column_name)
);

CREATE TABLE IF NOT EXISTS silver.dq_run_summary (
    run_id                     text NOT NULL,
    source                     text NOT NULL,
    rows_in                    bigint NOT NULL,   -- Bronze rows for the fact endpoint(s)
    duplicates_removed         bigint NOT NULL,
    rows_quarantined           bigint NOT NULL,   -- distinct bronze rows, DQ + identity
    rows_quarantined_dq        bigint NOT NULL,
    rows_quarantined_identity  bigint NOT NULL,
    rows_in_silver             bigint NOT NULL,
    reconciles                 boolean NOT NULL,  -- rows_in - duplicates - quarantined = rows_in_silver
    resolved_by                jsonb NOT NULL,    -- {"gsis_id": n, "vendor_map": n, ...}
    pct_units_inferred         numeric,
    pct_timestamps_reformatted numeric,
    schema_versions_seen       jsonb,
    expectations_run           int NOT NULL,
    expectations_failed        int NOT NULL,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, source)
);
