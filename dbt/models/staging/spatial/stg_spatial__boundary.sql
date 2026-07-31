{% raw %}
-- stg_spatial__boundary
--
-- Grain: one row per boundary, across every boundary set.
--
-- Names, areas and shapes for all three boundary sets in one table:
-- analysis_neighborhood (41), supervisor_district (11) and
-- census_block_group (681). The dimension models select from this and filter
-- to one set.
--
-- geojson is carried through as text rather than dropped. Nothing in the
-- warehouse joins on it, and by ADR-6 nothing should, but a map needs the
-- shape and regenerating it from the H3 cells would be lossy.
{% endraw %}

select
    boundary_set,
    boundary_id,
    boundary_name,
    area_sq_km,
    population,
    housing_units,
    geojson

from {{ source('derived_spatial', 'derived_boundary') }}
