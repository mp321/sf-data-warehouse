{% raw %}
-- assert_point_boundary_is_exact
--
-- The companion to assert_h3_membership_matches_exact_pip, and the stricter
-- of the two. That test measures the coarse filter and tolerates a few
-- percent of disagreement. This one checks what the marts actually read, and
-- tolerates none.
--
-- Every neighborhood in mart_activity_by_neighborhood, and every
-- analysis_neighborhood on every point staging model, comes from
-- derived_point_boundary. ADR-6 claims that column is exact: interior-cell
-- points inherit their cell's boundary, which is exact by construction, and
-- boundary-cell points get a real point-in-polygon test. This compares it
-- against the independent oracle in stg_spatial__pip_sample and fails on a
-- single disagreement.
--
-- If this fails, the claim in ADR-6 is false and the marts are wrong. There
-- is no threshold to tune.
{% endraw %}

with sampled as (

    select
        source_table,
        row_key,
        boundary_set,
        exact_boundary_id
    from {{ ref('stg_spatial__pip_sample') }}

),

assigned as (

    select
        source_table,
        row_key,
        'analysis_neighborhood' as boundary_set,
        analysis_neighborhood as boundary_id
    from {{ ref('stg_spatial__point_geography') }}

    union all

    select
        source_table,
        row_key,
        'supervisor_district' as boundary_set,
        supervisor_district_id as boundary_id
    from {{ ref('stg_spatial__point_geography') }}

    union all

    select
        source_table,
        row_key,
        'census_block_group' as boundary_set,
        census_block_group_geoid as boundary_id
    from {{ ref('stg_spatial__point_geography') }}

)

select
    sampled.source_table,
    sampled.row_key,
    sampled.boundary_set,
    sampled.exact_boundary_id,
    assigned.boundary_id as assigned_boundary_id

from sampled
left join assigned
    on sampled.source_table = assigned.source_table
        and sampled.row_key = assigned.row_key
        and sampled.boundary_set = assigned.boundary_set

where sampled.exact_boundary_id is distinct from assigned.boundary_id
