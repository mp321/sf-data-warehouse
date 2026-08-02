{% raw %}
-- stg_spatial__polygon_h3
--
-- Grain: one row per boundary per resolution per covering H3 cell.
--
-- The bridge table, and the reason no query in this warehouse needs a
-- geometry engine. Three boolean flags, each answering a different question,
-- and using the wrong one is the easiest mistake available here:
--
--   is_interior        the cell lies entirely inside this boundary. Any point
--                      in it is inside the boundary, with no test needed.
--   is_primary         this boundary owns the cell. At most one boundary per
--                      (boundary_set, resolution, cell) has it, so joining on
--                      it cannot fan out. Use this to label a cell.
--   is_allocation_cell the cell's centre is in this boundary, before the
--                      one-owner-per-cell rule was applied. Use this, and
--                      only this, to spread a measure like population across
--                      cells: is_primary discards the losing boundaries and
--                      would discard their residents with them.
--
-- Passthrough from the derived zone; ingestion/spatial.py does the work.
{% endraw %}

select
    boundary_set,
    boundary_id,
    resolution,
    h3_cell,
    is_interior,
    is_primary,
    is_allocation_cell

from {{ source('derived_spatial', 'derived_polygon_h3') }}
