-- One row per player per survey day (latest submission wins), Likert on 1-10.
-- readiness_score: weighted 1-10, soreness/fatigue/stress inverted so higher = readier.
with latest as (
    select distinct on (player_sk, survey_date) *
    from {{ source('silver', 'wellness_surveys') }}
    order by player_sk, survey_date, submitted_at desc
)
select
    {{ dbt_utils.generate_surrogate_key(['player_sk', 'survey_date']) }} as wellness_sk,
    player_sk, survey_date, submitted_at, bronze_id,
    sleep_hours, sleep_quality, soreness, fatigue, stress, mood, srpe,
    scale_max, scale_inferred, tz_corrected, is_resubmission,
    round(
        0.20 * sleep_quality
      + 0.15 * least(10, greatest(1, sleep_hours))          -- hours map ~1:1 onto 1-10
      + 0.25 * (11 - soreness)
      + 0.20 * (11 - fatigue)
      + 0.10 * (11 - stress)
      + 0.10 * mood, 2)                                       as readiness_score
from latest
