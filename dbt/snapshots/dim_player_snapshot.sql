{% snapshot dim_player_snapshot %}
{# SCD-2 over silver.dim_player_master. Silver keeps one row per player and
   updates it in place; this snapshot turns each change of the tracked
   columns into a new version with dbt_valid_from / dbt_valid_to. #}
{{ config(
    unique_key='player_sk',
    strategy='check',
    check_cols=['full_name', 'team', 'position', 'weight_lbs', 'height_in', 'roster_valid_to'],
) }}
select player_sk, gsis_id, nfl_id, espn_id, pfr_id, full_name, clean_name, first_name, last_name,
       football_name, team, position, weight_lbs, height_in, valid_to as roster_valid_to
from {{ source('silver', 'dim_player_master') }}
{% endsnapshot %}
