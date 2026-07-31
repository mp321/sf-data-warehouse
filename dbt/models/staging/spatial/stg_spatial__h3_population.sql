{% raw %}
-- stg_spatial__h3_population
--
-- Grain: one row per H3 cell per resolution.
--
-- The denominator behind every per-capita rate in the marts. Population and
-- housing units from the 2020 Census, areally interpolated from block groups
-- onto H3 cells under the assumption that people are spread evenly within a
-- block group. They are not, so a cell covering half a park and half a tower
-- block gets an average of the two.
--
-- These are fractional on purpose and are not rounded here. Rounding per cell
-- and then summing loses residents at a rate proportional to how many cells
-- you summed, which is worst exactly where the aggregation is coarsest.
{% endraw %}

select
    resolution,
    h3_cell,
    population,
    housing_units

from {{ source('derived_spatial', 'derived_h3_population') }}
