{% raw %}
-- assert_neighborhood_population_reconciles
--
-- Every per-capita rate in this warehouse divides by
-- dim_neighborhood.population, which is built by summing H3 cell population
-- over the cells each neighborhood owns. Residents in cells that no
-- neighborhood owns are lost on the way, and losing them is invisible: the
-- rates just come out slightly high, uniformly, forever.
--
-- So this checks the sum against the block group total and fails if too much
-- has gone missing. Some shortfall is expected and correct: cells over the
-- bay and just outside the city line belong to no neighborhood, and the
-- census block groups extend into water the neighborhood boundaries do not
-- cover.
--
-- The tolerance is a judgement call rather than a measurement, set a little
-- above what was observed when this was written. A threshold sitting exactly
-- on the current value fails on noise.
{% endraw %}

{%- set tolerance_pct = 5.0 -%}

with expected as (

    select sum(population) as city_population
    from {{ ref('stg_census__block_groups') }}

),

allocated as (

    select sum(population) as allocated_population
    from {{ ref('dim_neighborhood') }}

),

compared as (

    select
        expected.city_population,
        allocated.allocated_population,
        expected.city_population - allocated.allocated_population as unallocated_population,
        100.0 * {{ x_safe_divide(
            'expected.city_population - allocated.allocated_population',
            'expected.city_population'
        ) }} as unallocated_pct
    from expected
    cross join allocated

)

select *
from compared
where unallocated_pct > {{ tolerance_pct }}
    -- Negative means more residents were allocated than exist, which would
    -- mean a cell is counted by two neighborhoods and the bridge's
    -- one-owner-per-cell rule has broken. That is a worse failure than a
    -- shortfall and is deliberately not given a tolerance.
    or unallocated_pct < 0
    or city_population = 0
