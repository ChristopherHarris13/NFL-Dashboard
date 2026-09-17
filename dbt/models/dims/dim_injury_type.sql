-- The EMR's free-text body-part vocabulary -> canonical body part, region, side.
-- Edit dbt/seeds/injury_body_parts.csv to teach it new phrasings.
select
    {{ dbt_utils.generate_surrogate_key(['raw_text']) }} as injury_type_sk,
    lower(raw_text)  as raw_text,
    body_part,
    body_region,
    nullif(side, '') as side
from {{ ref('injury_body_parts') }}
