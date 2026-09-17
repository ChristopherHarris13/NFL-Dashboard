-- SCD-2 player dimension from the snapshot: one row per player *version*.
-- Join facts on player_sk (stable across versions) and pick the version
-- valid on the fact's date, or the is_current row for "now".
select
    {{ dbt_utils.generate_surrogate_key(['player_sk', 'dbt_valid_from']) }} as player_version_sk,
    player_sk, gsis_id, nfl_id, espn_id, pfr_id,
    full_name, clean_name, first_name, last_name, football_name,
    team, position, weight_lbs, height_in,
    roster_valid_to is null                  as on_roster,
    dbt_valid_from                           as valid_from,
    dbt_valid_to                             as valid_to,
    dbt_valid_to is null                     as is_current,
    count(*) over (partition by player_sk)   as versions
from {{ ref('dim_player_snapshot') }}
