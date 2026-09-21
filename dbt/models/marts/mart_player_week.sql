-- One row per tracked player per season week: everything the dashboard asks.
-- "End of week" values are taken from the last day of the week that has data.
with weeks as (
    select distinct season_week, min(date_day) as week_start, max(date_day) as week_end
    from {{ ref('fact_training_load') }} group by 1
),
players as (
    select distinct player_sk from {{ ref('fact_training_load') }}
),
grid as (
    select p.player_sk, w.season_week, w.week_start, w.week_end from players p cross join weeks w
),
load as (
    select player_sk, season_week,
           sum(external_load)                    as external_load_week,
           sum(internal_load)                    as internal_load_week,
           sum(sessions)                         as sessions_week,
           sum(total_distance_m)                 as distance_m_week,
           max(acwr_coupled)                     as acwr_max_week,
           sum(case when acwr_band = 'high' then 1 else 0 end) as days_acwr_high
    from {{ ref('fact_training_load') }} group by 1, 2
),
load_end as (
    select distinct on (player_sk, season_week) player_sk, season_week,
           acute_7d, chronic_28d, acwr_coupled, acwr_ewma, acwr_band, internal_acwr
    from {{ ref('fact_training_load') }}
    order by player_sk, season_week, date_day desc
),
well as (
    select w.player_sk, d.season_week,
           round(avg(readiness_score), 2) as readiness_avg,
           round(avg(soreness), 2)        as soreness_avg,
           round(avg(fatigue), 2)         as fatigue_avg,
           round(avg(sleep_hours), 2)     as sleep_hours_avg,
           count(*)                       as surveys
    from {{ ref('fact_wellness') }} w
    join {{ ref('dim_date') }} d on d.date_day = w.survey_date
    group by 1, 2
),
strength as (
    select distinct on (s.player_sk, d.season_week) s.player_sk, d.season_week,
           s.asymmetry_pct, s.asymmetry_4w_avg, s.asymmetry_trend, s.jump_height_4w_max, s.test_date as last_test_date
    from {{ ref('fact_strength') }} s
    join {{ ref('dim_date') }} d on d.date_day = s.test_date
    order by s.player_sk, d.season_week, s.test_ts desc
),
nutrition as (
    select distinct on (n.player_sk, d.season_week) n.player_sk, d.season_week,
           n.weight_lbs, n.body_fat_pct, n.method as weight_method, n.measured_on as last_weigh_in
    from {{ ref('fact_nutrition') }} n
    join {{ ref('dim_date') }} d on d.date_day = n.measured_on
    where n.is_preferred
    order by n.player_sk, d.season_week, n.measured_on desc, n.method_rank
),
avail as (
    select player_sk, season_week, availability_pct, scheduled_sessions, missed_sessions
    from {{ ref('fact_availability') }}
),
inj_new as (
    select i.player_sk, d.season_week, count(*) as new_injuries,
           string_agg(coalesce(i.body_part, i.body_part_raw), ', ' order by i.event_date) as new_injury_body_parts
    from {{ ref('fact_injury') }} i
    join {{ ref('dim_date') }} d on d.date_day = i.event_date
    group by 1, 2
),
inj_status as (
    -- status at the end of the week across the player's open injuries (worst wins)
    select s.player_sk, s.season_week,
           count(distinct s.injury_id)                                       as open_injuries,
           min(case s.practice_status when 'DNP' then 1 when 'LP' then 2 else 3 end) as worst_rank
    from {{ ref('fact_injury_status') }} s
    join weeks w on w.season_week = s.season_week and s.date_day = least(w.week_end, s.date_day)
    where s.date_day = (select max(date_day) from {{ ref('fact_injury_status') }} x
                        where x.injury_id = s.injury_id and x.season_week = s.season_week)
    group by 1, 2
),
play as (
    select player_sk, season_week,
           sum(plays) as plays, sum(epa_total) as epa_total, sum(offense_snaps) as offense_snaps,
           sum(defense_snaps) as defense_snaps, avg(offense_pct) as offense_pct, count(*) as games
    from {{ ref('fact_play') }} where season_week is not null group by 1, 2
)
select
    {{ dbt_utils.generate_surrogate_key(['g.player_sk', 'g.season_week']) }} as player_week_sk,
    g.player_sk, g.season_week, g.week_start, g.week_end,
    p.full_name, p.team, p.position,
    -- load
    l.external_load_week, l.internal_load_week, l.sessions_week, l.distance_m_week,
    round(le.acute_7d::numeric, 1) as acute_7d, round(le.chronic_28d::numeric, 1) as chronic_28d,
    round(le.acwr_coupled::numeric, 3) as acwr_coupled, round(le.acwr_ewma::numeric, 3) as acwr_ewma,
    round(le.internal_acwr::numeric, 3) as internal_acwr,
    le.acwr_band, round(l.acwr_max_week::numeric, 3) as acwr_max_week, l.days_acwr_high,
    -- wellness
    w.readiness_avg, w.soreness_avg, w.fatigue_avg, w.sleep_hours_avg, w.surveys,
    -- strength
    s.asymmetry_pct, round(s.asymmetry_4w_avg::numeric, 2) as asymmetry_4w_avg,
    round(s.asymmetry_trend::numeric, 2) as asymmetry_trend, s.jump_height_4w_max, s.last_test_date,
    -- body comp
    n.weight_lbs, n.body_fat_pct, n.weight_method, n.last_weigh_in,
    -- availability & injury
    a.availability_pct, a.scheduled_sessions, a.missed_sessions,
    coalesce(i.new_injuries, 0)                    as new_injuries,
    i.new_injury_body_parts,
    coalesce(st.open_injuries, 0)                  as open_injuries,
    case st.worst_rank when 1 then 'DNP' when 2 then 'LP' when 3 then 'FP' else 'FP' end as practice_status,
    -- games
    coalesce(pl.games, 0) as games, pl.plays, round(pl.epa_total::numeric, 2) as epa_total,
    pl.offense_snaps, pl.defense_snaps, round(pl.offense_pct::numeric, 3) as offense_pct
from grid g
left join {{ ref('dim_player') }} p on p.player_sk = g.player_sk and p.is_current
left join load      l  on l.player_sk  = g.player_sk and l.season_week  = g.season_week
left join load_end  le on le.player_sk = g.player_sk and le.season_week = g.season_week
left join well      w  on w.player_sk  = g.player_sk and w.season_week  = g.season_week
left join strength  s  on s.player_sk  = g.player_sk and s.season_week  = g.season_week
left join nutrition n  on n.player_sk  = g.player_sk and n.season_week  = g.season_week
left join avail     a  on a.player_sk  = g.player_sk and a.season_week  = g.season_week
left join inj_new   i  on i.player_sk  = g.player_sk and i.season_week  = g.season_week
left join inj_status st on st.player_sk = g.player_sk and st.season_week = g.season_week
left join play      pl on pl.player_sk = g.player_sk and pl.season_week = g.season_week
