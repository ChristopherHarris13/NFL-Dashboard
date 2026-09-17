-- One row per injury. return_date = first FP status after the event;
-- days_out counts to the return, or to the latest known date if still open.
with updates as (
    select injury_id, update_date, practice_status, updated_at
    from {{ source('silver', 'emr_status_updates') }}
),
first_fp as (
    select u.injury_id, min(u.update_date) as return_date
    from updates u
    join {{ source('silver', 'emr_injuries') }} i on i.injury_id = u.injury_id
    where u.practice_status = 'FP' and u.update_date >= i.event_date
    group by 1
),
last_seen as (
    select greatest((select max(update_date) from updates), (select max(event_date) from {{ source('silver', 'emr_injuries') }})) as as_of
)
select
    i.bronze_id                                          as injury_sk,
    i.injury_id, i.player_sk, i.team,
    i.event_ts, i.event_date, i.opened_at,
    coalesce(t.body_part, i.body_part)                   as body_part,
    t.body_region,
    coalesce(i.side, t.side)                             as side,
    i.body_part_raw,
    i.injury_type, i.severity, i.expected_rtp,
    f.return_date,
    f.return_date is null                                as is_open,
    coalesce(f.return_date, ls.as_of) - i.event_date     as days_out,
    (select count(*) from updates u where u.injury_id = i.injury_id) as status_updates,
    i.qc_flags
from {{ source('silver', 'emr_injuries') }} i
left join {{ ref('dim_injury_type') }} t on t.raw_text = lower(i.body_part_raw)
left join first_fp f on f.injury_id = i.injury_id
cross join last_seen ls
