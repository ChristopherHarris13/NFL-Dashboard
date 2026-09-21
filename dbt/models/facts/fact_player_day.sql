-- One row per tracked player per calendar day: the as-of reading the report
-- opens on. Everything the roster board shows is a column here, so a page
-- is a filter on one table.
--
--   availability      Out (DNP) | Limited (LP) | Available, worst across open injuries
--   game_status       Out | Doubtful | Questionable | null, worst across open injuries
--   readiness_delta   today's readiness minus the player's own mean over the prior 28 days
--   days_since_survey days since the last wellness survey on or before the day
--   is_silent         no survey in the last 3 days (CONTEXT.md: Silent player)
--   flag              red | amber | green, with flag_reasons spelled out (CONTEXT.md: Flag)
--
-- Flag rules:
--   red    Out, or game status Out/Doubtful, or ACWR high
--   amber  Limited, or any other open injury, or ACWR elevated, or readiness_delta <= -1.5,
--          or |asymmetry_4w_avg| > 10, or silent
--   green  none of the above
-- (An open injury at full practice is amber, not red: "open injury → red"
--  would make "Limited → amber" unreachable, since every LP player has one.)

with grid as (
    select training_load_sk, player_sk, date_day, season_week, day_type,
           sessions, external_load, internal_load,
           acute_7d as load_7d, chronic_28d as load_28d_avg_week, acwr_coupled, acwr_ewma, acwr_band, history_days
    from {{ ref('fact_training_load') }}
),

-- wellness: the day's survey, the player's prior-28-day mean, and the last survey on or before the day
well as (
    select player_sk, survey_date, readiness_score, soreness, fatigue, sleep_hours, stress, mood,
           avg(readiness_score) over w28 as readiness_28d_mean,
           count(*)               over w28 as surveys_28d
    from {{ ref('fact_wellness') }}
    window w28 as (partition by player_sk order by survey_date
                   range between interval '28 days' preceding and interval '1 day' preceding)
),
well_day as (
    select g.player_sk, g.date_day,
           w.readiness_score, w.soreness, w.fatigue, w.sleep_hours, w.stress, w.mood,
           case when w.surveys_28d >= 7 then round(w.readiness_28d_mean::numeric, 2) end as readiness_28d_mean,
           max(w.survey_date) over (partition by g.player_sk order by g.date_day rows unbounded preceding) as last_survey_date
    from grid g
    left join well w on w.player_sk = g.player_sk and w.survey_date = g.date_day
),

-- force plates: the latest test on or before the day carries the 4-week asymmetry
strength_day as (
    select distinct on (player_sk, test_date) player_sk, test_date, asymmetry_4w_avg, asymmetry_trend, jump_height_4w_max
    from {{ ref('fact_strength') }}
    order by player_sk, test_date, test_ts desc
),
strength_asof as (
    -- the date of the latest test on or before the day, then that test's values
    select x.player_sk, x.date_day, x.last_test_date, s.asymmetry_4w_avg, s.jump_height_4w_max
    from (
        select g.player_sk, g.date_day,
               max(s.test_date) over (partition by g.player_sk order by g.date_day rows unbounded preceding) as last_test_date
        from grid g
        left join strength_day s on s.player_sk = g.player_sk and s.test_date = g.date_day
    ) x
    left join strength_day s on s.player_sk = x.player_sk and s.test_date = x.last_test_date
),

-- body comp: the latest preferred weigh-in on or before the day
weight_day as (
    select player_sk, measured_on, weight_lbs, body_fat_pct, method
    from {{ ref('fact_nutrition') }} where is_preferred
),
weight_asof as (
    select x.player_sk, x.date_day, x.last_weigh_in, n.weight_lbs, n.body_fat_pct
    from (
        select g.player_sk, g.date_day,
               max(n.measured_on) over (partition by g.player_sk order by g.date_day rows unbounded preceding) as last_weigh_in
        from grid g
        left join weight_day n on n.player_sk = g.player_sk and n.measured_on = g.date_day
    ) x
    left join weight_day n on n.player_sk = x.player_sk and n.measured_on = x.last_weigh_in
),

-- injuries open on the day, worst status wins
inj_day as (
    select s.player_sk, s.date_day,
           count(distinct s.injury_id)                                                   as open_injuries,
           min(case s.practice_status when 'DNP' then 1 when 'LP' then 2 else 3 end)     as practice_rank,
           min(case s.game_status when 'Out' then 1 when 'Doubtful' then 2 when 'Questionable' then 3 end) as game_rank,
           string_agg(distinct coalesce(i.body_part, i.body_part_raw), ', ')             as open_injury_body_parts,
           min(i.expected_rtp)                                                           as expected_return,
           max(s.date_day - i.event_date)                                                as days_since_injury
    from {{ ref('fact_injury_status') }} s
    join {{ ref('fact_injury') }} i on i.injury_id = s.injury_id
    group by 1, 2
),

joined as (
    select
        g.*,
        wd.readiness_score, wd.soreness, wd.fatigue, wd.sleep_hours, wd.stress, wd.mood,
        wd.readiness_28d_mean,
        case when wd.readiness_score is not null and wd.readiness_28d_mean is not null
             then round(wd.readiness_score - wd.readiness_28d_mean, 2) end               as readiness_delta,
        wd.last_survey_date,
        g.date_day - wd.last_survey_date                                                 as days_since_survey,
        coalesce(g.date_day - wd.last_survey_date >= 3, true)                            as is_silent,
        sa.last_test_date, sa.asymmetry_4w_avg, sa.jump_height_4w_max,
        wa.last_weigh_in, wa.weight_lbs, wa.body_fat_pct,
        coalesce(ij.open_injuries, 0)                                                    as open_injuries,
        case ij.practice_rank when 1 then 'DNP' when 2 then 'LP' when 3 then 'FP' end     as practice_status,
        case ij.practice_rank when 1 then 'Out' when 2 then 'Limited' else 'Available' end as availability,
        case ij.game_rank when 1 then 'Out' when 2 then 'Doubtful' when 3 then 'Questionable' end as game_status,
        ij.open_injury_body_parts, ij.expected_return, ij.days_since_injury
    from grid g
    left join well_day      wd on wd.player_sk = g.player_sk and wd.date_day = g.date_day
    left join strength_asof sa on sa.player_sk = g.player_sk and sa.date_day = g.date_day
    left join weight_asof   wa on wa.player_sk = g.player_sk and wa.date_day = g.date_day
    left join inj_day       ij on ij.player_sk = g.player_sk and ij.date_day = g.date_day
),

reasons as (
    select *,
        array_remove(array[
            case when availability = 'Out'                          then 'out (DNP)' end,
            case when game_status in ('Out', 'Doubtful')            then 'game status ' || lower(game_status) end,
            case when acwr_band = 'high'                            then 'ACWR high' end
        ], null)                                                                         as red_reasons,
        array_remove(array[
            case when availability = 'Limited'                      then 'limited (LP)' end,
            case when availability = 'Available' and open_injuries > 0 then 'open injury at full practice' end,
            case when acwr_band = 'elevated'                        then 'ACWR elevated' end,
            case when readiness_delta <= -1.5                       then 'readiness ' || readiness_delta || ' vs own 28-day mean' end,
            case when abs(asymmetry_4w_avg) > 10                    then 'asymmetry ' || round(asymmetry_4w_avg, 1) || '%' end,
            case when is_silent                                     then 'no wellness survey in 3 days' end
        ], null)                                                                         as amber_reasons
    from joined
)

select
    training_load_sk                                                    as player_day_sk,
    player_sk, date_day, season_week, day_type,
    sessions, external_load, internal_load, load_7d, load_28d_avg_week, acwr_coupled, acwr_ewma, acwr_band, history_days,
    readiness_score, readiness_28d_mean, readiness_delta, soreness, fatigue, sleep_hours, stress, mood,
    last_survey_date, days_since_survey, is_silent,
    last_test_date, asymmetry_4w_avg, jump_height_4w_max,
    last_weigh_in, weight_lbs, body_fat_pct,
    open_injuries, practice_status, availability, game_status, open_injury_body_parts, expected_return, days_since_injury,
    case when cardinality(red_reasons) > 0 then 'red'
         when cardinality(amber_reasons) > 0 then 'amber'
         else 'green' end                                               as flag,
    case when cardinality(red_reasons) > 0 then 1
         when cardinality(amber_reasons) > 0 then 2
         else 3 end                                                     as flag_rank,
    nullif(array_to_string(red_reasons || amber_reasons, '; '), '')     as flag_reasons
from reasons
