{{ config(materialized='view') }}
select run_id, source, suite, expectation_name, column_name, kwargs, success,
       element_count, unexpected_count, unexpected_percent, systemic, validated_at
from {{ source('silver', 'dq_expectation_results') }}
