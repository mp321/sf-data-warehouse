{% raw %}
-- dim_supervisor_district
--
-- Grain: one row per supervisor district. 11 rows.
--
-- The same construction as dim_neighborhood, over the 2022 district
-- boundaries. Population comes from summing H3 cell population over the cells
-- each district owns at the membership resolution, so it is an interpolated
-- estimate rather than a census count; dim_neighborhood's header has the full
-- argument and it applies unchanged here.
--
-- The vintage warning is worth repeating because it bites differently here.
-- These are the boundaries drawn in 2022. Every event row from before then
-- carries an upstream_supervisor_district assigned under the 2012 lines, so
-- a query that joins on the upstream column will get a different answer from
-- one that joins on the computed supervisor_district_id, and neither is
-- wrong: they are answering "which district was this in at the time" and
-- "which district is this in now".
{% endraw %}

with boundaries as (

    select
        -- x_safe_int, not a direct int cast. DataSF publishes the district
        -- number as "1.0", and the two engines disagree about what that is:
        -- DuckDB's try_cast truncates it to 1, BigQuery's safe_cast refuses a
        -- fractional string and returns null. A direct cast therefore passes
        -- every local test and nulls all 11 districts on BigQuery, which is
        -- what PLAN-4 step 3 found the first time the BigQuery target ran.
        -- Routing through float parses on both.
        {{ x_safe_int('boundary_id') }} as supervisor_district,
        boundary_id as supervisor_district_id,
        area_sq_km,
        geojson
    from {{ ref('stg_spatial__boundary') }}
    where boundary_set = 'supervisor_district'

),

names as (

    select
        supervisor_district,
        district_name,
        supervisor_name
    from {{ ref('stg_datasf__supervisor_districts') }}

),

cell_population as (

    select
        bridge.boundary_id as supervisor_district_id,
        sum(population.population) as population,
        sum(population.housing_units) as housing_units,
        count(*) as h3_cell_count
    from {{ ref('stg_spatial__h3_population') }} as population
    inner join {{ ref('stg_spatial__polygon_h3') }} as bridge
        on population.h3_cell = bridge.h3_cell
            and population.resolution = bridge.resolution
    where bridge.boundary_set = 'supervisor_district'
        and bridge.resolution = {{ var('h3_membership_resolution') }}
        and bridge.is_primary
    group by bridge.boundary_id

),

businesses as (

    select
        supervisor_district_id,
        count(*) as business_count,
        sum(case when is_active then 1 else 0 end) as active_business_count
    from {{ ref('stg_datasf__business_locations') }}
    where supervisor_district_id is not null
    group by supervisor_district_id

),

final as (

    select
        boundaries.supervisor_district,
        boundaries.supervisor_district_id,
        names.district_name,
        names.supervisor_name,

        boundaries.area_sq_km,
        cast(round(coalesce(cell_population.population, 0)) as {{ x_type('int') }}) as population,
        cast(round(coalesce(cell_population.housing_units, 0)) as {{ x_type('int') }})
            as housing_units,
        coalesce(businesses.business_count, 0) as business_count,
        coalesce(businesses.active_business_count, 0) as active_business_count,

        {{ x_safe_divide('coalesce(cell_population.population, 0)', 'boundaries.area_sq_km') }}
            as population_per_sq_km,
        coalesce(cell_population.h3_cell_count, 0) as h3_cell_count,

        boundaries.geojson

    from boundaries
    left join names on boundaries.supervisor_district = names.supervisor_district
    left join cell_population
        on boundaries.supervisor_district_id = cell_population.supervisor_district_id
    left join businesses on boundaries.supervisor_district_id = businesses.supervisor_district_id

)

select * from final
