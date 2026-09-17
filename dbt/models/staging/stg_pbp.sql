-- nflverse play-by-play, unpacked from Bronze (column projection landed by extract_nflverse).
with d as (
    select id as bronze_id, payload,
           row_number() over (partition by payload->>'game_id', payload->>'play_id' order by _ingested_at desc, id desc) as rn
    from {{ source('bronze', 'nflverse') }}
    where _endpoint = 'pbp'
)
select bronze_id,
       payload->>'game_id'                         as game_id,
       (payload->>'play_id')::numeric::bigint      as play_id,
       (payload->>'season')::int                   as season,
       (payload->>'week')::int                     as nfl_week,
       (payload->>'game_date')::date               as game_date,
       payload->>'posteam'                         as posteam,
       payload->>'defteam'                         as defteam,
       payload->>'play_type'                       as play_type,
       payload->>'passer_player_id'                as passer_player_id,
       payload->>'rusher_player_id'                as rusher_player_id,
       payload->>'receiver_player_id'              as receiver_player_id,
       (payload->>'epa')::numeric                  as epa,
       (payload->>'qb_epa')::numeric               as qb_epa,
       (payload->>'success')::numeric              as success,
       (payload->>'yards_gained')::numeric         as yards_gained,
       (payload->>'touchdown')::numeric            as touchdown
from d where rn = 1
