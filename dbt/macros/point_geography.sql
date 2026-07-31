{#
    join_point_geography: attach coordinates, H3 cells and boundaries to a
    point staging model.

    Every point-bearing staging model ends with this, so all five expose an
    identical set of geography columns under identical names. Written as a
    macro rather than copied because the alternative is five places that have
    to agree about a join key, a filter and eleven column names, and they
    would stop agreeing the first time a sixth dataset arrived.

    The upstream model must NOT select its own latitude and longitude. The
    parsed coordinates come from here, and they are the same values the H3
    cells in the same row were computed from. A model that parsed its own
    would eventually disagree with its own cell, which is the one inconsistency
    in this design that nothing downstream could detect.

    Arguments:
      upstream_cte  name of the CTE to attach geography to
      source_table  the raw table name from ingestion/datasets.py. This is the
                    string spatial.py wrote into derived_point_h3, so it has
                    to match the registry's `table` value exactly.
      key_column    the column in upstream_cte holding the row key, which must
                    be the registry's `grain_key` after renaming.

    The join is a LEFT join on purpose. A point row with no entry in the
    derived zone gets null geography rather than disappearing, so a staging
    model's row count never depends on whether `make spatial` has run.
#}

{%- macro join_point_geography(upstream_cte, source_table, key_column) -%}
select
    {{ upstream_cte }}.*,

    -- coordinates, parsed once in ingestion/spatial.py. Null unless
    -- coordinate_status is 'ok' or 'out_of_bounds': a value that could not be
    -- parsed at all is not a coordinate and is not worth carrying as one.
    geography.latitude,
    geography.longitude,
    geography.coordinate_status,
    geography.is_usable_coordinate,

    -- H3 cells as BIGINTs (ADR-5). These are what boundary joins and cell
    -- aggregations use; nothing downstream needs a geometry function.
    geography.h3_r8,
    geography.h3_r9,
    geography.h3_r10,

    -- exact boundary membership (ADR-6), null where the point is outside
    -- every boundary in that set.
    geography.analysis_neighborhood,
    geography.supervisor_district_id,
    geography.census_block_group_geoid

from {{ upstream_cte }}
left join {{ ref('stg_spatial__point_geography') }} as geography
    on geography.source_table = '{{ source_table }}'
        and geography.row_key = {{ upstream_cte }}.{{ key_column }}
{%- endmacro -%}
