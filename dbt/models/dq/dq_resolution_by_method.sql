{{ config(materialized='view') }}
-- How each vendor's identifiers were pinned to a player (the durable, per-identifier view).
select vendor, resolved_by, count(*) as identifiers, round(avg(confidence), 2) as avg_confidence
from {{ source('silver', 'vendor_player_map') }}
group by 1, 2
