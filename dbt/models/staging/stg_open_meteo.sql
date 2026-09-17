-- Open-Meteo hourly forecast at Arrowhead, unpacked from Bronze. Each DAG run
-- lands ±7 days; the same hour recurs with revised values, latest ingest wins.
with d as (
    select id as bronze_id, payload, _ingested_at,
           row_number() over (partition by payload->>'stadium', payload->>'time' order by _ingested_at desc, id desc) as rn
    from {{ source('bronze', 'open_meteo') }}
    where _endpoint = 'forecast'
)
select bronze_id,
       payload->>'stadium'                                   as stadium,
       payload->>'team'                                      as team,
       (payload->>'time')::timestamp                         as local_hour,       -- stadium-local wall time
       ((payload->>'time')::timestamp at time zone (payload->>'timezone')) as hour_utc,
       (payload->>'temperature_2m')::numeric                 as temperature_c,
       (payload->>'apparent_temperature')::numeric           as apparent_temperature_c,
       (payload->>'relative_humidity_2m')::numeric           as relative_humidity_pct,
       (payload->>'precipitation')::numeric                  as precipitation_mm,
       (payload->>'wind_speed_10m')::numeric                 as wind_speed_kmh,
       (payload->>'wind_gusts_10m')::numeric                 as wind_gusts_kmh,
       (payload->>'weather_code')::int                       as weather_code,
       _ingested_at
from d where rn = 1
