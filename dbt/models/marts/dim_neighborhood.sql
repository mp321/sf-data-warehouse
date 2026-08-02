{% raw %}
-- dim_neighborhood
--
-- Grain: one row per analysis neighborhood. 41 rows.
--
-- The denominator table. Every rate in mart_activity_by_neighborhood divides
-- by a column that lives here, which makes this the model most worth reading
-- before trusting a number in this warehouse.
--
-- **How population gets here.** The Census publishes population by block
-- group, and block groups do not nest inside neighborhoods. Rather than clip
-- polygons, this walks the H3 cells: population was interpolated onto r10
-- cells in ingestion/spatial.py, each r10 cell has exactly one primary
-- neighborhood, so summing cells by neighborhood allocates every resident to
-- exactly one of them. The city total is conserved except for cells that no
-- neighborhood owns, which are almost entirely water; the shortfall is
-- reported by tests/assert_neighborhood_population_reconciles.sql rather than
-- left to be discovered.
--
-- That makes neighborhood population an estimate twice over: once because
-- residents are assumed uniform within a block group, and again because a
-- boundary cell's residents all go to whichever neighborhood owns the cell.
-- It is good to a percent or so at r10, and it is not a census count.
--
-- **Denominators that are not here.** The plan asked for rates per parcel and
-- per street mile. Neither dataset is in scope: parcels and street centrelines
-- are separate DataSF datasets and adding them is a scope decision (ADR-7),
-- not a modelling one. What is here instead is residents, housing units,
-- land area and registered businesses, which is enough to normalise every
-- count in this warehouse and enough to show that the choice of denominator
-- changes the ranking.
{% endraw %}

with boundaries as (

    select
        boundary_id as analysis_neighborhood,
        area_sq_km,
        geojson
    from {{ ref('stg_spatial__boundary') }}
    where boundary_set = 'analysis_neighborhood'

),

published as (

    select
        analysis_neighborhood,
        published_area_sq_mi
    from {{ ref('stg_datasf__analysis_neighborhoods') }}

),

cell_population as (

    -- One row per neighborhood. The join is an equality on a BIGINT cell id:
    -- this is the whole point of ADR-6, and it is what a query would look
    -- like if it wanted to do this itself.
    select
        bridge.boundary_id as analysis_neighborhood,
        sum(population.population) as population,
        sum(population.housing_units) as housing_units,
        count(*) as h3_cell_count
    from {{ ref('stg_spatial__h3_population') }} as population
    inner join {{ ref('stg_spatial__polygon_h3') }} as bridge
        on population.h3_cell = bridge.h3_cell
            and population.resolution = bridge.resolution
    where bridge.boundary_set = 'analysis_neighborhood'
        and bridge.resolution = {{ var('h3_membership_resolution') }}
        and bridge.is_primary
    group by bridge.boundary_id

),

businesses as (

    -- Registered businesses, as a denominator for anything commercial. Both
    -- counts are kept: the total is the right denominator for a historical
    -- rate and the active one for a current rate, and they differ by more
    -- than half.
    select
        analysis_neighborhood,
        count(*) as business_count,
        sum(case when is_active then 1 else 0 end) as active_business_count
    from {{ ref('stg_datasf__business_locations') }}
    where analysis_neighborhood is not null
    group by analysis_neighborhood

),

trees as (

    select
        analysis_neighborhood,
        count(*) as street_tree_count
    from {{ ref('stg_datasf__street_trees') }}
    where analysis_neighborhood is not null
    group by analysis_neighborhood

),

final as (

    select
        boundaries.analysis_neighborhood,

        -- area, computed on a sphere in ingestion/geometry.py. The published
        -- figure is carried beside it as an independent check rather than as
        -- an alternative: if the two ever diverge, the boundary changed.
        boundaries.area_sq_km,
        published.published_area_sq_mi,

        -- denominators. Rounded to whole people and homes: they are
        -- interpolated estimates and a fractional resident invites more
        -- confidence than the method supports.
        cast(round(coalesce(cell_population.population, 0)) as {{ x_type('int') }}) as population,
        cast(round(coalesce(cell_population.housing_units, 0)) as {{ x_type('int') }})
            as housing_units,
        coalesce(businesses.business_count, 0) as business_count,
        coalesce(businesses.active_business_count, 0) as active_business_count,
        coalesce(trees.street_tree_count, 0) as street_tree_count,

        -- densities, for reading rather than for dividing by
        {{ x_safe_divide('coalesce(cell_population.population, 0)', 'boundaries.area_sq_km') }}
            as population_per_sq_km,
        {{ x_safe_divide('coalesce(businesses.business_count, 0)', 'boundaries.area_sq_km') }}
            as businesses_per_sq_km,

        -- how many r10 cells this neighborhood owns. A coverage diagnostic:
        -- a neighborhood whose cell count is wildly out of line with its area
        -- has a boundary problem, not a population problem.
        coalesce(cell_population.h3_cell_count, 0) as h3_cell_count,

        boundaries.geojson

    from boundaries
    left join published on boundaries.analysis_neighborhood = published.analysis_neighborhood
    left join cell_population
        on boundaries.analysis_neighborhood = cell_population.analysis_neighborhood
    left join businesses on boundaries.analysis_neighborhood = businesses.analysis_neighborhood
    left join trees on boundaries.analysis_neighborhood = trees.analysis_neighborhood

)

select * from final
