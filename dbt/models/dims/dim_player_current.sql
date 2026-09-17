-- The current version of every player, one row each: the Player table the
-- report joins to. dim_player keeps the SCD-2 history for anyone who needs it.
{{ config(materialized='view') }}
select player_sk, gsis_id, nfl_id, espn_id, pfr_id,
       full_name, first_name, last_name, football_name, team, position,
       weight_lbs as roster_weight_lbs, height_in as roster_height_in, on_roster, valid_from
from {{ ref('dim_player') }}
where is_current
