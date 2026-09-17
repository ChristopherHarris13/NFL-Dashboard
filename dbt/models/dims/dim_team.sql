-- Every team seen on the nflverse roster, with its stadium (seed).
with teams as (
    select distinct team from {{ source('silver', 'dim_player_master') }} where team is not null
)
select
    t.team                                   as team_sk,
    t.team,
    s.stadium, s.city, s.state, s.latitude, s.longitude,
    coalesce(s.is_dome, false)               as is_dome,
    s.surface,
    (select count(*) from {{ source('silver', 'dim_player_master') }} m
      where m.team = t.team and m.valid_to is null) as active_players
from teams t
left join {{ ref('stadiums') }} s on s.team = t.team
