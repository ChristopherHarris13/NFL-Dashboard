-- Silver: one row per real-world event, typed, deduplicated, unit-normalised,
-- every player resolved to player_sk. Every row carries bronze_id so it can
-- be traced to the exact payload it came from.
--
-- Also applied by the DAG's `silver_schema` task on every run (all statements
-- are IF NOT EXISTS), so a warehouse created before this file existed picks
-- it up without a volume reset.

-- ------------------------------------------------------------ identity

CREATE TABLE IF NOT EXISTS silver.dim_player_master (
    player_sk    serial PRIMARY KEY,
    gsis_id      text NOT NULL UNIQUE,
    nfl_id       bigint,
    espn_id      bigint,
    pfr_id       text,
    full_name    text NOT NULL,
    clean_name   text NOT NULL,      -- identity.clean_player_name(full_name); the name-join key
    first_name   text,               -- legal first name (what an EMR uses: "Robert" Dobbs)
    last_name    text,
    football_name text,              -- what the player goes by ("Joshua", "Hollywood")
    team         text,
    position     text,
    weight_lbs   numeric,            -- roster weight; nutrition uses it to infer lb vs kg
    height_in    numeric,
    valid_from   timestamptz NOT NULL DEFAULT now(),
    valid_to     timestamptz,        -- set when the player drops off the roster snapshot
    updated_at   timestamptz NOT NULL DEFAULT now(),
    bronze_id    bigint              -- roster row this version came from
);
CREATE INDEX IF NOT EXISTS dim_player_master_clean_name_idx ON silver.dim_player_master (clean_name);
CREATE INDEX IF NOT EXISTS dim_player_master_nfl_id_idx     ON silver.dim_player_master (nfl_id);

-- Vendor identifier -> player_sk, pinned the first time it resolves so
-- cat_<uuid>s, nflIds and every mangled name variant are resolved once.
CREATE TABLE IF NOT EXISTS silver.vendor_player_map (
    vendor            text NOT NULL,
    vendor_player_id  text NOT NULL,   -- the vendor's own id, or the raw name string for name-only vendors
    player_sk         int  NOT NULL REFERENCES silver.dim_player_master (player_sk),
    resolved_by       text NOT NULL,   -- method that established the mapping
    confidence        numeric,
    first_seen        timestamptz NOT NULL DEFAULT now(),
    last_seen         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vendor, vendor_player_id)
);

-- Identifiers the resolver could not pin. Rebuilt per vendor on each run.
CREATE TABLE IF NOT EXISTS silver.quarantine_identity (
    id                 bigserial PRIMARY KEY,
    vendor             text NOT NULL,
    vendor_player_id   text NOT NULL,
    raw_name           text,
    raw_team           text,
    reason             text NOT NULL,   -- 'unresolved' (no candidate) | 'ambiguous' (several)
    candidate_sks      int[],
    occurrences        int NOT NULL,
    example_bronze_id  bigint,
    seen_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vendor, vendor_player_id, reason)
);

-- ------------------------------------------------------------ facts

CREATE TABLE IF NOT EXISTS silver.forcedeck_tests (
    bronze_id            bigint PRIMARY KEY,
    test_id              uuid NOT NULL UNIQUE,
    player_sk            int,
    resolved_by          text NOT NULL,
    athlete_gsis_id      text,
    test_ts              timestamptz NOT NULL,
    test_type            text,
    rep                  int,
    jump_height_cm       numeric,
    peak_force_n         numeric NOT NULL,
    peak_force_raw       numeric NOT NULL,
    force_unit_raw       text,
    force_unit_inferred  boolean NOT NULL,
    rfd                  numeric,
    asymmetry_pct        numeric,
    device_id            text,
    qc_flags             text[] NOT NULL DEFAULT '{}',
    _transformed_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.nutrition_measurements (
    bronze_id             bigint PRIMARY KEY,
    measurement_id        uuid NOT NULL UNIQUE,
    player_sk             int,
    resolved_by           text NOT NULL,
    player_name_raw       text NOT NULL,
    team                  text,
    measured_on           date NOT NULL,
    method                text NOT NULL,
    weight_kg             numeric,
    weight_raw            numeric NOT NULL,
    weight_unit_raw       text,
    weight_unit_inferred  boolean NOT NULL,
    weight_unit_evidence  text,          -- 'label' | 'magnitude' | 'lean_mass' | 'roster' | 'default'
    body_fat_pct          numeric,
    lean_mass_kg          numeric,
    lean_mass_raw         numeric,
    lean_mass_was_pct     boolean NOT NULL,
    hydration_status      text,
    qc_flags              text[] NOT NULL DEFAULT '{}',
    _transformed_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.wellness_surveys (
    bronze_id        bigint PRIMARY KEY,
    survey_id        uuid NOT NULL UNIQUE,
    player_sk        int,
    resolved_by      text NOT NULL,
    vendor_nfl_id    bigint,
    survey_date      date NOT NULL,        -- facility-local (ET) date
    submitted_at     timestamptz NOT NULL, -- UTC
    tz_corrected     boolean NOT NULL,     -- payload claimed +00:00 on a local time
    schema_shape     text NOT NULL,        -- 'v1' | 'v2', from the keys, not the envelope
    scale_max        int NOT NULL,
    scale_inferred   boolean NOT NULL,
    sleep_hours      numeric,
    sleep_quality    numeric,              -- all Likert fields rescaled to 1..10
    soreness         numeric,
    fatigue          numeric,
    stress           numeric,
    mood             numeric,
    srpe             int,
    is_resubmission  boolean NOT NULL,     -- an earlier survey exists for the same player-day
    qc_flags         text[] NOT NULL DEFAULT '{}',
    _transformed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.emr_injuries (
    bronze_id        bigint PRIMARY KEY,
    injury_id        uuid NOT NULL UNIQUE,
    player_sk        int,
    resolved_by      text NOT NULL,
    player_raw       text NOT NULL,
    team             text,
    event_ts         timestamptz NOT NULL,
    event_date       date NOT NULL,        -- ET
    body_part        text,                 -- canonical: hamstring|knee|ankle|shoulder|concussion
    body_part_raw    text NOT NULL,
    side             text,                 -- L | R
    side_source      text,                 -- 'field' | 'text' | null
    injury_type      text,
    severity         text,
    expected_rtp     date,
    opened_at        timestamptz,
    qc_flags         text[] NOT NULL DEFAULT '{}',
    _transformed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.emr_status_updates (
    bronze_id         bigint PRIMARY KEY,
    update_id         uuid NOT NULL UNIQUE,
    injury_id         uuid NOT NULL,
    player_sk         int,
    resolved_by       text NOT NULL,
    player_raw        text NOT NULL,
    updated_at        timestamptz NOT NULL,
    update_date       date NOT NULL,       -- ET
    practice_status   text,
    game_status       text,
    note              text,
    is_correction     boolean NOT NULL,    -- a different Bronze row carried the same update_id
    qc_flags          text[] NOT NULL DEFAULT '{}',
    _transformed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.catapult_sessions (
    bronze_id               bigint PRIMARY KEY,
    session_id              uuid NOT NULL UNIQUE,
    player_sk               int,
    resolved_by             text NOT NULL,
    vendor_player_id        text NOT NULL,
    session_ts              timestamptz NOT NULL,
    session_date            date NOT NULL,
    session_type            text,
    duration_min            int,
    total_distance_m        numeric,
    high_speed_distance_m   numeric,
    distance_unit_raw       text,
    distance_unit_inferred  boolean NOT NULL,
    max_speed_ms            numeric,
    speed_unit_raw          text,
    speed_unit_inferred     boolean NOT NULL,
    sprint_count            int,
    accel_count             int,
    decel_count             int,
    player_load             numeric,
    _ingested_at            timestamptz NOT NULL,  -- when Bronze got it; late arrival is judged across runs, not here
    qc_flags                text[] NOT NULL DEFAULT '{}',
    _transformed_at         timestamptz NOT NULL DEFAULT now()
);

-- Columns added after the table first shipped (IF NOT EXISTS keeps re-runs clean).
ALTER TABLE silver.dim_player_master ADD COLUMN IF NOT EXISTS football_name text;
ALTER TABLE silver.catapult_sessions DROP COLUMN IF EXISTS days_late;
ALTER TABLE silver.catapult_sessions ADD COLUMN IF NOT EXISTS _ingested_at timestamptz;
