-- Medallion layout. Runs once, against the `warehouse` database, on first
-- boot of an empty data volume (`docker compose down -v` resets).
--
--   bronze  raw vendor payloads, append-only, exactly as the API emitted them
--           (dirt included: dupes, unit swaps, drifted schemas, late arrivals)
--   silver  one row per real-world event: typed, deduped, unit-normalised,
--           timestamps in UTC, every player resolved to a single player_sk
--   gold    what the dashboard reads: per-player-day load, ACWR, wellness,
--           body-comp trends, injury status

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

COMMENT ON SCHEMA bronze IS 'Raw source payloads as JSONB, append-only. Never cleaned in place.';
COMMENT ON SCHEMA silver IS 'Typed, deduplicated, unit-normalised events keyed on player_sk.';
COMMENT ON SCHEMA gold   IS 'Dashboard-facing aggregates (per player-day).';
