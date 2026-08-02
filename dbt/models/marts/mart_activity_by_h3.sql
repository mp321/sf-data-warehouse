{% raw %}
-- mart_activity_by_h3
--
-- Grain: one row per H3 cell per dataset per category per month.
--
-- The hexagon map. Counts of everything this warehouse tracks, bucketed into
-- H3 cells and months, with the population underneath each cell so the counts
-- can be normalised.
--
-- **Read the rate, not the count.** A raw count per cell is very close to a
-- map of where people are: the densest 311 cells are the densest residential
-- cells, and a chart of `event_count` mostly rediscovers the census. That is
-- why every count column here has a companion, and why `events_per_1000_
-- residents` is the column worth ranking on.
--
-- **The two normalisations are not equivalent.**
--
--   events_per_1000_residents  the useful one. Answers "is this cell noisier
--                              than its population explains". Null where the
--                              cell has no residents, which is correct and
--                              common: cells over the bay, the Presidio and
--                              the Financial District all have real activity
--                              and close to nobody living in them. Null, not
--                              zero, because the question does not apply.
--   events_per_sq_km           at a fixed resolution every cell has the same
--                              area, so this is the count rescaled by a
--                              constant and ranks identically to it. It is
--                              here because it is comparable with the
--                              neighborhood mart, where areas genuinely
--                              differ, and for no other reason.
--
-- Built at one resolution, var('h3_mart_resolution'), currently 9: about
-- 0.1 km per cell, roughly a couple of city blocks. Coarse enough that a map
-- of the city is a few thousand hexagons rather than tens of thousands, fine
-- enough that a neighborhood is many cells. Changing the var rebuilds this
-- at another resolution; the cells are already stored at 8, 9 and 10.
--
-- Named mart_ rather than agg_ following the plan that commissioned it. See
-- CLAUDE.md, where the naming rule was widened to match.
{% endraw %}

{%- set resolution = var('h3_mart_resolution') -%}

with activity as (

    select
        dataset,
        category,
        event_month,
        h3_r{{ resolution }} as h3_cell
    from {{ ref('int_point_activity') }}
    -- Events with no usable coordinate have no cell and cannot be placed on
    -- a map. They are counted in mart_pipeline_freshness, which is where the
    -- drop rate belongs; silently including them here as a null cell would
    -- put a hexagon-shaped hole in every total.
    where h3_r{{ resolution }} is not null

),

counted as (

    select
        h3_cell,
        dataset,
        category,
        event_month,
        count(*) as event_count
    from activity
    group by h3_cell, dataset, category, event_month

),

cell_population as (

    select
        h3_cell,
        population,
        housing_units
    from {{ ref('stg_spatial__h3_population') }}
    where resolution = {{ resolution }}

),

cell_neighborhood as (

    -- One label per cell, from the bridge. is_primary is what guarantees
    -- exactly one row per cell here; is_interior would drop the edges and
    -- plain covering membership would fan every edge cell out into two or
    -- three rows and double-count the events in it.
    select
        h3_cell,
        boundary_id as analysis_neighborhood
    from {{ ref('stg_spatial__polygon_h3') }}
    where boundary_set = 'analysis_neighborhood'
        and resolution = {{ resolution }}
        and is_primary

),

cell_district as (

    select
        h3_cell,
        boundary_id as supervisor_district_id
    from {{ ref('stg_spatial__polygon_h3') }}
    where boundary_set = 'supervisor_district'
        and resolution = {{ resolution }}
        and is_primary

),

final as (

    select
        -- identity
        counted.h3_cell,
        {{ resolution }} as h3_resolution,
        counted.dataset,
        counted.category,
        counted.event_month,

        -- where the cell is, for filtering and labelling. Null means the
        -- cell's centre is not inside any neighborhood, which is water or
        -- just outside the city line.
        cell_neighborhood.analysis_neighborhood,
        cell_district.supervisor_district_id,

        -- the count
        counted.event_count,

        -- the denominators, carried so the rates can be checked and
        -- re-derived without another join
        cell_population.population as cell_population,
        cell_population.housing_units as cell_housing_units,
        {{ var('h3_cell_area_sq_km')[resolution | string] }} as cell_area_sq_km,

        -- the normalised companions
        {{ x_safe_divide(
            '1000.0 * counted.event_count', 'cell_population.population'
        ) }} as events_per_1000_residents,
        {{ x_safe_divide(
            '1000.0 * counted.event_count', 'cell_population.housing_units'
        ) }} as events_per_1000_housing_units,
        counted.event_count / {{ var('h3_cell_area_sq_km')[resolution | string] }}
            as events_per_sq_km

    from counted
    left join cell_population on counted.h3_cell = cell_population.h3_cell
    left join cell_neighborhood on counted.h3_cell = cell_neighborhood.h3_cell
    left join cell_district on counted.h3_cell = cell_district.h3_cell

)

select * from final
