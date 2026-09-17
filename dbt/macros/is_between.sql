{# Generic test: every non-null value of the column is within [min_value, max_value].
   Usage in schema.yml:  - is_between: {min_value: 0, max_value: 1} #}
{% test is_between(model, column_name, min_value, max_value) %}
select {{ column_name }} as value
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})
{% endtest %}
