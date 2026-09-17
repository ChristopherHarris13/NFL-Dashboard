-- One generic Bronze shape, one table per source. The record goes into
-- `payload` untouched; everything the pipeline adds is `_`-prefixed.
--
-- Idempotency: UNIQUE (_payload_hash, _source). Re-running an extract over
-- the same records inserts nothing (ON CONFLICT DO NOTHING in
-- airflow/plugins/extract.py). Genuinely different re-emissions (a corrected
-- EMR update, a second wellness submission with changed answers) hash
-- differently and are kept — Silver decides which one wins.

CREATE OR REPLACE FUNCTION bronze.create_source_table(source text) RETURNS void AS $$
BEGIN
    EXECUTE format($sql$
        CREATE TABLE IF NOT EXISTS bronze.%1$I (
            id               bigserial    PRIMARY KEY,
            payload          jsonb        NOT NULL,
            _source          text         NOT NULL,   -- e.g. catapult
            _endpoint        text         NOT NULL,   -- e.g. /v1/sessions, rosters, forecast
            _ingested_at     timestamptz  NOT NULL DEFAULT now(),
            _batch_id        uuid         NOT NULL,   -- one per task run
            _payload_hash    text         NOT NULL,   -- md5 of canonical JSON
            _schema_version  text                     -- from the API envelope where one exists
        );
        CREATE UNIQUE INDEX IF NOT EXISTS %2$I ON bronze.%1$I (_payload_hash, _source);
        CREATE INDEX IF NOT EXISTS %3$I ON bronze.%1$I (_ingested_at);
        CREATE INDEX IF NOT EXISTS %4$I ON bronze.%1$I (_endpoint);
    $sql$, source, source || '_hash_source_key', source || '_ingested_at_idx', source || '_endpoint_idx');
END;
$$ LANGUAGE plpgsql;

-- mock vendors
SELECT bronze.create_source_table('catapult');       -- /v1/sessions
SELECT bronze.create_source_table('forcedeck');      -- /v1/tests
SELECT bronze.create_source_table('ams_wellness');   -- /v1/surveys
SELECT bronze.create_source_table('nutrition');      -- /v1/measurements
SELECT bronze.create_source_table('emr');            -- /v1/injuries, /v1/status_updates
-- real feeds
SELECT bronze.create_source_table('nflverse');       -- rosters, ids (nfl_data_py)
SELECT bronze.create_source_table('open_meteo');     -- forecast (one stadium, hourly)
