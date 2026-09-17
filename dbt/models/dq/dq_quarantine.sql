{{ config(materialized='view') }}
-- Current quarantine across every source, with readable reasons.
{% for src in ['forcedeck', 'catapult', 'wellness', 'nutrition', 'emr'] %}
select '{{ src }}' as source, bronze_id, endpoint, run_id, expectation_name, column_name,
       observed_value, reason, quarantined_at
from {{ source('silver', 'quarantine_' ~ src) }}
{% if not loop.last %}union all{% endif %}
{% endfor %}
