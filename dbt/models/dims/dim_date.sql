-- One row per calendar day from SIM_START_DATE. day_type follows the mocks'
-- weekly schedule (Mon/Tue off, Wed-Fri practice, Sat walkthrough, Sun game);
-- season_week counts Monday-anchored weeks from the sim start (week 1 = camp).
with spine as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('" ~ var('sim_start_date') ~ "' as date)",
        end_date="cast('" ~ var('sim_end_date') ~ "' as date)"
    ) }}
)
select
    cast(date_day as date)                                            as date_day,
    extract(isodow from date_day)::int                                as iso_dow,
    to_char(date_day, 'Dy')                                           as day_name,
    ((cast(date_day as date) - cast('{{ var("sim_start_date") }}' as date)) / 7 + 1)::int as season_week,
    case extract(isodow from date_day)::int
        when 1 then 'off' when 2 then 'off'
        when 3 then 'practice' when 4 then 'practice' when 5 then 'practice'
        when 6 then 'walkthrough'
        when 7 then 'game' end                                        as day_type,
    extract(isodow from date_day)::int between 3 and 7                as is_scheduled,
    extract(isodow from date_day)::int in (3, 4, 5, 7)                as counts_for_availability,  -- practices + games
    extract(year from date_day)::int                                  as year,
    extract(month from date_day)::int                                 as month
from spine
