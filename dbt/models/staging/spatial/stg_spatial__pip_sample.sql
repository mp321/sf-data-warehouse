{% raw %}
-- stg_spatial__pip_sample
--
-- Grain: one row per sampled point per boundary set.
--
-- The test oracle, and an input to nothing else. For a deterministic sample
-- of points, exact_boundary_id is the answer an exact point-in-polygon test
-- gives, computed in Python by an implementation separate from the one that
-- produced derived_point_boundary.
--
-- Its only consumer is tests/assert_h3_membership_matches_exact_pip.sql,
-- which uses it to measure how well cell-based membership agrees with the
-- truth at each resolution. Do not join a mart to this: the sample is a few
-- thousand rows chosen by hashing the row key, so any aggregate over it is an
-- aggregate over an arbitrary subset of the city.
{% endraw %}

select
    source_table,
    row_key,
    boundary_set,
    latitude,
    longitude,
    exact_boundary_id,
    h3_r8,
    h3_r9,
    h3_r10

from {{ source('derived_spatial', 'derived_pip_sample') }}
