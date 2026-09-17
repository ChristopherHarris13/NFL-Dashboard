-- Game-day external load and EPA next to the weather at the stadium. Weather
-- comes from Open-Meteo at the mocks' home stadium (Arrowhead, outdoor); a game
-- session is matched to the forecast hour it started in. Coverage is limited
-- to the forecast window (±7 days around each DAG run), so this is sparse by
-- design — the page says how many games have weather.
with games as (
    select s.player_sk, s.session_id, s.session_ts, s.session_date, s.duration_min,
           s.total_distance_m, s.high_speed_distance_m, s.max_speed_ms, s.player_load,
           date_trunc('hour', s.session_ts) as hour_utc
    from {{ source('silver', 'catapult_sessions') }} s
    where s.session_type = 'game'
),
stadium as (
    select t.team_sk as team, t.stadium, t.is_dome, t.latitude, t.longitude
    from {{ ref('dim_team') }} t
    where t.team_sk = (select max(team) from {{ ref('stg_open_meteo') }})   -- the one stadium we have weather for
),
epa as (
    select player_sk, game_date, sum(epa_total) as epa_total, sum(plays) as plays, sum(offense_snaps) as offense_snaps
    from {{ ref('fact_play') }} group by 1, 2
)
select
    {{ dbt_utils.generate_surrogate_key(['g.session_id']) }} as game_weather_sk,
    g.player_sk, g.session_id, g.session_ts, g.session_date, d.season_week,
    st.team as stadium_team, st.stadium, st.is_dome,
    g.duration_min, g.total_distance_m, g.high_speed_distance_m, g.max_speed_ms, g.player_load,
    e.epa_total, e.plays, e.offense_snaps,
    w.temperature_c, w.apparent_temperature_c, w.relative_humidity_pct, w.precipitation_mm,
    w.wind_speed_kmh, w.wind_gusts_kmh, w.weather_code,
    w.temperature_c is not null as has_weather
from games g
cross join stadium st
left join {{ ref('stg_open_meteo') }} w on w.hour_utc = g.hour_utc and w.team = st.team
left join {{ ref('dim_date') }} d on d.date_day = g.session_date
left join epa e on e.player_sk = g.player_sk and e.game_date = g.session_date
