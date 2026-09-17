-- Daily practice status per open injury: one row per injury per calendar day
-- from the event to the return (or the latest known date), status carried
-- forward from the most recent update on or before that day. This is the
-- as-of view availability needs; the raw updates stay in silver.
with inj as (
    select injury_id, player_sk, event_date, coalesce(return_date, event_date + days_out) as end_date
    from {{ ref('fact_injury') }}
),
days as (
    select i.injury_id, i.player_sk, d.date_day, d.season_week, d.day_type, d.counts_for_availability
    from inj i
    join {{ ref('dim_date') }} d on d.date_day between i.event_date and i.end_date
),
asof as (
    select dy.*,
           (select u.practice_status from {{ source('silver', 'emr_status_updates') }} u
             where u.injury_id = dy.injury_id and u.update_date <= dy.date_day
             order by u.updated_at desc limit 1)                                   as practice_status,
           (select u.game_status from {{ source('silver', 'emr_status_updates') }} u
             where u.injury_id = dy.injury_id and u.update_date <= dy.date_day
             order by u.updated_at desc limit 1)                                   as game_status
    from days dy
)
select
    {{ dbt_utils.generate_surrogate_key(['injury_id', 'date_day']) }} as injury_status_sk,
    injury_id, player_sk, date_day, season_week, day_type, counts_for_availability,
    coalesce(practice_status, 'DNP')          as practice_status,   -- no update yet on the day of injury: out
    game_status,
    coalesce(practice_status, 'DNP') = 'DNP'  as is_unavailable
from asof
