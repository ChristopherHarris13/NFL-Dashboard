-- availability_pct per player per season week:
--   (practices + games the player was not DNP) / (practices + games scheduled)
-- Players with no injury on a day are available. LP counts as available.
with players as (
    select distinct player_sk from {{ ref('fact_training_load') }}
),
bounds as (
    select min(date_day) as start_date, max(date_day) as end_date from {{ ref('fact_training_load') }}
),
sched as (
    select d.season_week, d.date_day
    from {{ ref('dim_date') }} d cross join bounds b
    where d.counts_for_availability and d.date_day between b.start_date and b.end_date
),
grid as (
    select p.player_sk, s.season_week, s.date_day from players p cross join sched s
),
dnp as (
    select distinct player_sk, date_day
    from {{ ref('fact_injury_status') }}
    where is_unavailable
)
select
    {{ dbt_utils.generate_surrogate_key(['g.player_sk', 'g.season_week']) }} as availability_sk,
    g.player_sk, g.season_week,
    count(*)                                              as scheduled_sessions,
    count(*) - count(x.date_day)                          as available_sessions,
    count(x.date_day)                                     as missed_sessions,
    round((count(*) - count(x.date_day))::numeric / count(*), 3) as availability_pct
from grid g
left join dnp x on x.player_sk = g.player_sk and x.date_day = g.date_day
group by 1, 2, 3
