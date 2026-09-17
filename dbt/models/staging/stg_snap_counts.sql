-- nflverse snap counts (PFR-keyed), unpacked from Bronze.
with d as (
    select id as bronze_id, payload,
           row_number() over (partition by payload->>'pfr_game_id', payload->>'pfr_player_id' order by _ingested_at desc, id desc) as rn
    from {{ source('bronze', 'nflverse') }}
    where _endpoint = 'snap_counts'
)
select bronze_id,
       payload->>'game_id'                         as game_id,
       payload->>'pfr_game_id'                     as pfr_game_id,
       (payload->>'season')::int                   as season,
       (payload->>'week')::int                     as nfl_week,
       payload->>'game_type'                       as game_type,
       payload->>'player'                          as player_name,
       payload->>'pfr_player_id'                   as pfr_player_id,
       payload->>'position'                        as position,
       payload->>'team'                            as team,
       payload->>'opponent'                        as opponent,
       (payload->>'offense_snaps')::numeric::int   as offense_snaps,
       (payload->>'offense_pct')::numeric          as offense_pct,
       (payload->>'defense_snaps')::numeric::int   as defense_snaps,
       (payload->>'defense_pct')::numeric          as defense_pct,
       (payload->>'st_snaps')::numeric::int        as st_snaps,
       (payload->>'st_pct')::numeric               as st_pct
from d where rn = 1
