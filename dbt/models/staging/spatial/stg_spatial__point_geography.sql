{% raw %}
-- stg_spatial__point_geography
--
-- Grain: one row per point-bearing raw row, across every point dataset.
--
-- The one place any point staging model has to join to get its geography.
-- Every point dataset joins here once on (source_table, row_key) and comes
-- away with coordinates, H3 cells at three resolutions, and the neighborhood,
-- supervisor district and block group the point is exactly inside.
--
-- One deliberate departure from the staging rules in CLAUDE.md, called out
-- because staging is supposed to be rename-and-cast with no logic: this model
-- pivots derived_point_boundary from long to wide. That table is one row per
-- (point, boundary set), which is the right shape to store and the wrong
-- shape to join, and the alternative is three left joins in every one of the
-- five point staging models. Pivoting once here means the reshape is written
-- down once and every point table gets an identical set of columns.
--
-- No coalescing to a fallback. Where the derived zone has no assignment the
-- columns are null, and null means "this point is not inside any boundary in
-- that set", which is a real answer for a case in the bay or a business
-- registered in Oakland. Filling it in from the upstream supervisor_district
-- string would reintroduce exactly the column ADR-2 rejected as unusable.
{% endraw %}

with point_h3 as (

    select * from {{ source('derived_spatial', 'derived_point_h3') }}

),

assignments as (

    select * from {{ source('derived_spatial', 'derived_point_boundary') }}

),

pivoted as (

    -- max() over a case is the portable pivot: both engines have it, and
    -- derived_point_boundary is unique on (source_table, row_key,
    -- boundary_set), so each branch has at most one non-null value to pick
    -- and max() is choosing rather than aggregating.
    select
        source_table,
        row_key,
        max(case when boundary_set = 'analysis_neighborhood' then boundary_id end)
            as analysis_neighborhood,
        max(case when boundary_set = 'supervisor_district' then boundary_id end)
            as supervisor_district_id,
        max(case when boundary_set = 'census_block_group' then boundary_id end)
            as census_block_group_geoid,
        max(case when boundary_set = 'analysis_neighborhood' then assignment_method end)
            as neighborhood_assignment_method
    from assignments
    group by source_table, row_key

),

final as (

    select
        -- identity. source_table is the raw table name from
        -- ingestion/datasets.py, so it is the same string on both sides of
        -- every join a point staging model makes.
        point_h3.source_table,
        point_h3.row_key,

        -- coordinates, already parsed and judged by ingestion/spatial.py.
        point_h3.latitude,
        point_h3.longitude,
        point_h3.coordinate_status,
        point_h3.is_usable_coordinate,

        -- H3 cells as BIGINTs, which is what makes membership an integer
        -- predicate rather than a string compare (ADR-5).
        point_h3.h3_r8,
        point_h3.h3_r9,
        point_h3.h3_r10,

        -- exact boundary membership (ADR-6). Null where the point falls
        -- outside every boundary in that set.
        pivoted.analysis_neighborhood,
        pivoted.supervisor_district_id,
        pivoted.census_block_group_geoid,
        pivoted.neighborhood_assignment_method

    from point_h3
    left join pivoted
        on point_h3.source_table = pivoted.source_table
        and point_h3.row_key = pivoted.row_key

)

select * from final
