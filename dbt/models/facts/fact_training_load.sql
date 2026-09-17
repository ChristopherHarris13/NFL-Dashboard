-- One row per tracked player per calendar day (complete grid, zero-filled),
-- so the rolling windows are true time windows.
--
--   external_load  Catapult player_load summed over the day's sessions
--   internal_load  wellness sRPE x that day's session minutes (Foster)
--   acute_7d       7-day rolling sum of external_load
--   chronic_28d    28-day rolling sum / 4  (average week)
--   acwr_coupled   acute_7d / chronic_28d  (the acute week is inside the chronic window)
--   acwr_ewma      EWMA(7) / EWMA(28) over a 56-day lookback, weights normalised
--   acwr_band      low <0.8 | sweet 0.8-1.3 | elevated 1.3-1.5 | high >1.5
-- ACWR is null until a player has 28 days of history.

{% set lam_a = 2.0 / 8 %}
{% set lam_c = 2.0 / 29 %}

with players as (
    select player_sk from {{ source('silver', 'catapult_sessions') }}
    union
    select player_sk from {{ source('silver', 'wellness_surveys') }}
),
bounds as (
    select cast('{{ var("sim_start_date") }}' as date) as start_date,
           greatest((select max(session_date) from {{ source('silver', 'catapult_sessions') }}),
                    (select max(survey_date)  from {{ source('silver', 'wellness_surveys') }})) as end_date
),
grid as (
    select p.player_sk, d.date_day, d.season_week, d.day_type
    from players p
    cross join {{ ref('dim_date') }} d
    cross join bounds b
    where d.date_day between b.start_date and b.end_date
),
ext as (
    select player_sk, session_date,
           sum(player_load)              as external_load,
           sum(duration_min)             as duration_min,
           count(*)                      as sessions,
           sum(total_distance_m)         as total_distance_m,
           sum(high_speed_distance_m)    as high_speed_distance_m,
           max(max_speed_ms)             as max_speed_ms,
           sum(sprint_count)             as sprint_count
    from {{ source('silver', 'catapult_sessions') }}
    group by 1, 2
),
srpe as (
    -- latest survey of the day carries the sRPE
    select distinct on (player_sk, survey_date) player_sk, survey_date, srpe
    from {{ source('silver', 'wellness_surveys') }}
    order by player_sk, survey_date, submitted_at desc
),
daily as (
    select g.player_sk, g.date_day, g.season_week, g.day_type,
           coalesce(e.external_load, 0)                         as external_load,
           coalesce(e.duration_min, 0)                          as duration_min,
           coalesce(e.sessions, 0)                              as sessions,
           e.total_distance_m, e.high_speed_distance_m, e.max_speed_ms, e.sprint_count,
           s.srpe,
           coalesce(s.srpe * e.duration_min, 0)                 as internal_load
    from grid g
    left join ext  e on e.player_sk = g.player_sk and e.session_date = g.date_day
    left join srpe s on s.player_sk = g.player_sk and s.survey_date  = g.date_day
),
rolling as (
    select *,
        sum(external_load) over w7                                          as acute_7d,
        sum(external_load) over w28 / 4.0                                   as chronic_28d,
        sum(internal_load) over w7                                          as internal_acute_7d,
        sum(internal_load) over w28 / 4.0                                   as internal_chronic_28d,
        count(*) over w28                                                   as history_days
    from daily
    window w7  as (partition by player_sk order by date_day rows between 6  preceding and current row),
           w28 as (partition by player_sk order by date_day rows between 27 preceding and current row)
),
ewma as (
    -- 56-day lookback, weights (1-lambda)^age normalised so early days aren't biased low
    select d.player_sk, d.date_day,
           sum(p.external_load * power(1 - {{ lam_a }}, d.date_day - p.date_day))
             / nullif(sum(power(1 - {{ lam_a }}, d.date_day - p.date_day)), 0)  as ewma_acute,
           sum(p.external_load * power(1 - {{ lam_c }}, d.date_day - p.date_day))
             / nullif(sum(power(1 - {{ lam_c }}, d.date_day - p.date_day)), 0)  as ewma_chronic
    from daily d
    join daily p on p.player_sk = d.player_sk
                and p.date_day between d.date_day - 55 and d.date_day
    group by 1, 2
)
select
    {{ dbt_utils.generate_surrogate_key(['r.player_sk', 'r.date_day']) }} as training_load_sk,
    r.player_sk, r.date_day, r.season_week, r.day_type,
    r.sessions, r.duration_min,
    r.external_load, r.internal_load, r.srpe,
    r.total_distance_m, r.high_speed_distance_m, r.max_speed_ms, r.sprint_count,
    r.acute_7d, r.chronic_28d,
    case when r.history_days >= 28 then r.acute_7d / nullif(r.chronic_28d, 0) end       as acwr_coupled,
    r.internal_acute_7d, r.internal_chronic_28d,
    case when r.history_days >= 28 then r.internal_acute_7d / nullif(r.internal_chronic_28d, 0) end as internal_acwr,
    e.ewma_acute, e.ewma_chronic,
    case when r.history_days >= 28 then e.ewma_acute / nullif(e.ewma_chronic, 0) end   as acwr_ewma,
    case when r.history_days < 28 or nullif(r.chronic_28d, 0) is null then null
         when r.acute_7d / r.chronic_28d <  {{ var('acwr_low') }}           then 'low'
         when r.acute_7d / r.chronic_28d <= {{ var('acwr_sweet_high') }}    then 'sweet'
         when r.acute_7d / r.chronic_28d <= {{ var('acwr_elevated_high') }} then 'elevated'
         else 'high' end                                                             as acwr_band,
    r.history_days
from rolling r
join ewma e on e.player_sk = r.player_sk and e.date_day = r.date_day
