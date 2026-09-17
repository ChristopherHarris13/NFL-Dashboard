-- Bronze landing tables: one per vendor resource, matching the API contract
-- in mock_vendors/README.md. The whole record goes into `payload` untouched so
-- schema drift (AMS v1 -> v2) and unit swaps are preserved for Silver to sort
-- out. No uniqueness on payload keys on purpose: duplicate emissions and
-- correction overwrites are real data and Silver needs to see all of them.

CREATE OR REPLACE FUNCTION bronze.create_landing_table(tbl text) RETURNS void AS $$
BEGIN
    EXECUTE format($sql$
        CREATE TABLE IF NOT EXISTS bronze.%I (
            ingest_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            payload         jsonb        NOT NULL,
            schema_version  text         NOT NULL,           -- "v1" | "v2" from the API envelope
            source_cursor   text,                            -- next_cursor of the page this came from
            page_offset     integer      NOT NULL,           -- position within that page
            ingested_at     timestamptz  NOT NULL DEFAULT now(),
            dag_run_id      text,                            -- Airflow run that landed it
            payload_hash    text GENERATED ALWAYS AS (md5(payload::text)) STORED
        );
        CREATE INDEX IF NOT EXISTS %I ON bronze.%I (ingested_at);
        CREATE INDEX IF NOT EXISTS %I ON bronze.%I (payload_hash);
    $sql$, tbl, tbl || '_ingested_at_idx', tbl, tbl || '_payload_hash_idx', tbl);
END;
$$ LANGUAGE plpgsql;

SELECT bronze.create_landing_table('catapult_sessions');       -- catapult_svc     /v1/sessions
SELECT bronze.create_landing_table('forcedeck_tests');         -- forcedeck_svc    /v1/tests
SELECT bronze.create_landing_table('ams_wellness_surveys');    -- ams_wellness_svc /v1/surveys
SELECT bronze.create_landing_table('nutrition_measurements');  -- nutrition_svc    /v1/measurements
SELECT bronze.create_landing_table('emr_injuries');            -- emr_svc          /v1/injuries
SELECT bronze.create_landing_table('emr_status_updates');      -- emr_svc          /v1/status_updates

-- Where each (service, resource) stream was last read. Ingest DAGs read the
-- cursor, walk pages until `data` is empty, then write next_cursor back.
CREATE TABLE IF NOT EXISTS meta.ingest_cursor (
    source        text        NOT NULL,   -- e.g. catapult_svc
    resource      text        NOT NULL,   -- e.g. sessions
    landing_table text        NOT NULL,   -- bronze.<table>
    next_cursor   text,                   -- NULL = never read / read from start
    last_run_id   text,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, resource)
);

INSERT INTO meta.ingest_cursor (source, resource, landing_table) VALUES
    ('catapult_svc',     'sessions',       'bronze.catapult_sessions'),
    ('forcedeck_svc',    'tests',          'bronze.forcedeck_tests'),
    ('ams_wellness_svc', 'surveys',        'bronze.ams_wellness_surveys'),
    ('nutrition_svc',    'measurements',   'bronze.nutrition_measurements'),
    ('emr_svc',          'injuries',       'bronze.emr_injuries'),
    ('emr_svc',          'status_updates', 'bronze.emr_status_updates')
ON CONFLICT DO NOTHING;
