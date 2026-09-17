-- Per player per game from nflverse: offensive EPA by role (passer / rusher /
-- receiver, GSIS-keyed) and snap counts (PFR-keyed, via the crosswalk).
with roles as (
    select game_id, game_date, nfl_week, posteam as team, passer_player_id   as gsis_id, 'pass' as role, epa, success from {{ ref('stg_pbp') }} where passer_player_id   is not null
    union all
    select game_id, game_date, nfl_week, posteam,         rusher_player_id,   'rush',        epa, success from {{ ref('stg_pbp') }} where rusher_player_id   is not null
    union all
    select game_id, game_date, nfl_week, posteam,         receiver_player_id, 'receive',     epa, success from {{ ref('stg_pbp') }} where receiver_player_id is not null
),
epa as (
    select r.game_id, r.game_date, r.nfl_week, r.team, m.player_sk,
           count(*)                                            as plays,
           sum(epa)                                            as epa_total,
           sum(case when role = 'pass'    then epa end)        as epa_pass,
           sum(case when role = 'rush'    then epa end)        as epa_rush,
           sum(case when role = 'receive' then epa end)        as epa_receive,
           avg(success)                                        as success_rate
    from roles r
    join {{ source('silver', 'dim_player_master') }} m on m.gsis_id = r.gsis_id
    group by 1, 2, 3, 4, 5
),
snaps as (
    select s.game_id, s.nfl_week, s.team, s.opponent, m.player_sk,
           s.offense_snaps, s.offense_pct, s.defense_snaps, s.defense_pct, s.st_snaps, s.st_pct
    from {{ ref('stg_snap_counts') }} s
    join {{ source('silver', 'dim_player_master') }} m on m.pfr_id = s.pfr_player_id
),
games as (
    select distinct game_id, game_date, nfl_week from {{ ref('stg_pbp') }}
)
select
    {{ dbt_utils.generate_surrogate_key(['coalesce(e.player_sk, s.player_sk)', 'coalesce(e.game_id, s.game_id)']) }} as play_sk,
    coalesce(e.player_sk, s.player_sk)      as player_sk,
    coalesce(e.game_id, s.game_id)          as game_id,
    g.game_date,
    d.season_week,
    coalesce(e.nfl_week, s.nfl_week)        as nfl_week,
    coalesce(e.team, s.team)                as team,
    s.opponent,
    coalesce(e.plays, 0)                    as plays,
    e.epa_total, e.epa_pass, e.epa_rush, e.epa_receive, e.success_rate,
    s.offense_snaps, s.offense_pct, s.defense_snaps, s.defense_pct, s.st_snaps, s.st_pct
from epa e
full outer join snaps s on s.player_sk = e.player_sk and s.game_id = e.game_id
left join games g on g.game_id = coalesce(e.game_id, s.game_id)
left join {{ ref('dim_date') }} d on d.date_day = g.game_date
