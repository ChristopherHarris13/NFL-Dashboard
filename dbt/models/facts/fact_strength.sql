-- One row per force-plate test, with a rolling 4-week asymmetry view:
--   asymmetry_4w_avg   mean asymmetry_pct over the trailing 28 days
--   asymmetry_trend    trailing 4-week mean minus the previous 4-week mean (drift)
select
    bronze_id                                 as strength_sk,
    test_id, player_sk, test_ts, test_ts::date as test_date,
    test_type, rep, jump_height_cm, peak_force_n, force_unit_inferred, rfd, asymmetry_pct, device_id,
    avg(asymmetry_pct) over w4                                as asymmetry_4w_avg,
    avg(asymmetry_pct) over w4 - avg(asymmetry_pct) over w4_prior as asymmetry_trend,
    max(jump_height_cm) over w4                               as jump_height_4w_max
from {{ source('silver', 'forcedeck_tests') }}
window w4       as (partition by player_sk order by test_ts range between interval '27 days' preceding and current row),
       w4_prior as (partition by player_sk order by test_ts range between interval '55 days' preceding and interval '28 days' preceding)
