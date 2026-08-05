{% raw %}
-- assert_h3_membership_matches_exact_pip
--
-- The test ADR-6 exists to be held to. It measures how often deciding a
-- point's neighborhood from its H3 cell alone agrees with an exact
-- point-in-polygon test, and fails if agreement falls below a floor.
--
-- **What it compares.** stg_spatial__pip_sample holds the exact answer for a
-- deterministic sample of points, computed in Python by an implementation
-- separate from the one that assigns boundaries. This joins each sampled
-- point's cell to the bridge's primary boundary for that cell, which is what
-- a query would get if it used cells alone, and compares.
--
-- **Why it is allowed to fail below 100 percent.** Cell-based membership is
-- approximate by construction: a cell that straddles a boundary belongs to
-- one side, and points on the other side of the line inside that cell get the
-- wrong answer. The error is bounded by cell size, which is why the floor
-- rises with resolution. This is not what the marts use. The marts use
-- derived_point_boundary, which refines boundary cells with an exact test and
-- is checked separately by assert_point_boundary_is_exact.sql at 100 percent.
--
-- So this test is measuring the coarse filter, not the answer. It earns its
-- place because the coarse filter is what makes the exact step affordable: if
-- agreement at r10 collapses, the resolution has stopped being fine enough
-- for the boundary set, refinement is doing all the work, and ADR-6's
-- performance argument has quietly stopped being true.
--
-- Measured 2026-07-31 on 10,000 sampled points per boundary set:
--
--   analysis_neighborhood   r8 72.6%   r9 88.2%   r10 94.7%
--   supervisor_district     r8 83.6%   r9 92.7%   r10 96.8%
--
-- The r9 column is history: ADR-10 stopped computing that resolution. It is
-- kept here because the point of the table is the trend, and three points
-- make the case for the finest resolution that two do not.
--
-- The floors below sit a few points under those, so ordinary boundary churn
-- does not fail the build but a real regression does.
{% endraw %}

{%- set floors = {
    'analysis_neighborhood': 0.90,
    'supervisor_district': 0.94
} -%}

{%- set resolution = var('h3_membership_resolution') -%}

with sampled as (

    select
        boundary_set,
        exact_boundary_id,
        h3_r{{ resolution }} as h3_cell
    from {{ ref('stg_spatial__pip_sample') }}
    -- Single quotes, spelled out. Jinja's `tojson` filter renders these with
    -- double quotes, which SQL reads as identifiers, so the first version of
    -- this line asked for a column named analysis_neighborhood.
    where boundary_set in (
        {%- for name in floors %}
        '{{ name }}'{% if not loop.last %},{% endif %}
        {%- endfor %}
    )

),

cell_membership as (

    select
        boundary_set,
        h3_cell,
        boundary_id
    from {{ ref('stg_spatial__polygon_h3') }}
    where resolution = {{ resolution }}
        and is_primary

),

compared as (

    select
        sampled.boundary_set,
        count(*) as sampled_points,
        -- `is not distinct from` so that two nulls count as agreement: a
        -- point outside every neighborhood should be outside every
        -- neighborhood by both methods, and that is a correct answer worth
        -- crediting rather than a pair of nulls to be dropped.
        sum(
            case
                when sampled.exact_boundary_id is not distinct from cell_membership.boundary_id
                    then 1
                else 0
            end
        ) as agreeing_points
    from sampled
    left join cell_membership
        on sampled.boundary_set = cell_membership.boundary_set
            and sampled.h3_cell = cell_membership.h3_cell
    group by sampled.boundary_set

),

final as (

    select
        boundary_set,
        sampled_points,
        agreeing_points,
        {{ x_safe_divide('1.0 * agreeing_points', 'sampled_points') }} as agreement_rate,
        case
            {%- for boundary_set, floor in floors.items() %}
            when boundary_set = '{{ boundary_set }}' then {{ floor }}
            {%- endfor %}
        end as required_agreement_rate
    from compared

)

-- A dbt singular test fails on any row it returns, so this returns the
-- boundary sets that fell short. An empty result is a pass. The measured and
-- required rates are both selected so the failure message says how far off it
-- was rather than only that it was.
select *
from final
where agreement_rate < required_agreement_rate
    -- An empty sample would otherwise pass silently, and a test that cannot
    -- fail is worse than no test. This fires if `make spatial` never ran.
    or sampled_points = 0
