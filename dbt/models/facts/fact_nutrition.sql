-- One row per measurement. When a player has several methods on one date,
-- is_preferred marks DEXA over BIA over scale.
select
    bronze_id                                 as nutrition_sk,
    measurement_id, player_sk, measured_on, method,
    weight_lbs, weight_mislabeled,
    body_fat_pct, lean_mass_lbs, lean_mass_was_pct, hydration_status,
    case method when 'DEXA' then 1 when 'BIA' then 2 else 3 end                         as method_rank,
    row_number() over (partition by player_sk, measured_on
                       order by case method when 'DEXA' then 1 when 'BIA' then 2 else 3 end, bronze_id) = 1
                                                                                          as is_preferred
from {{ source('silver', 'nutrition_measurements') }}
