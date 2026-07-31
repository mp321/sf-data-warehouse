{% raw %}
-- stg_census__block_groups
--
-- Grain: one row per census block group. 681 of them in San Francisco.
--
-- The population denominator. Every "per 1000 residents" rate in this
-- warehouse is ultimately divided by a number that starts here, so the two
-- things worth knowing before using it are both about vintage and both
-- recorded in ADR-7:
--
--   1. This is the 2020 Decennial enumeration (POP100), not an ACS 5-year
--      estimate. It is an actual count with no margin of error, and it is
--      fixed at April 2020. San Francisco's population has moved since, so
--      a 2026 rate is being divided by a 2020 denominator.
--   2. It comes from the Census TIGERweb service rather than the ACS API,
--      because api.census.gov now requires a key and ADR-1 keeps credentials
--      off the ingestion path.
--
-- The city total is 873,965, which matches the published 2020 count exactly,
-- and ingestion/spatial.py asserts that total survives interpolation onto H3
-- cells at every resolution.
--
-- Prefixed stg_census__ rather than stg_datasf__ because the source is a
-- different publisher with a different update cadence and a different
-- licence, and the prefix is the only thing that says so at a glance.
{% endraw %}

with source as (

    select * from {{ source('raw_datasf', 'raw_census_block_groups') }}

),

deduplicated as (

    select *
    from source
    qualify row_number() over (
        partition by geoid
        order by
            {{ x_cast('_socrata_updated_at', 'timestamp') }} desc,
            {{ x_cast('_ingested_at', 'timestamp') }} desc
    ) = 1

),

renamed as (

    select
        -- The 12-character GEOID: state, county, tract, block group. Stays a
        -- string, because it has meaningful leading zeros ("06" is
        -- California) and casting it to a number loses them silently.
        geoid as block_group_geoid,
        state as state_fips,
        county as county_fips,
        tract as tract_code,
        blkgrp as block_group_number,
        name as block_group_name,

        -- the denominators
        {{ x_safe_int('pop100') }} as population,
        {{ x_safe_int('hu100') }} as housing_units,

        -- Land and water area in square metres, as the Census publishes them.
        -- The split matters for density: several San Francisco block groups
        -- are mostly bay, and dividing by total area understates them.
        {{ x_safe_cast('arealand', 'float') }} / 1000000.0 as land_area_sq_km,
        {{ x_safe_cast('areawater', 'float') }} / 1000000.0 as water_area_sq_km,

        -- An interior point the Census guarantees is inside the polygon,
        -- published as "+37.7966229" with an explicit leading sign.
        {{ x_safe_cast('intptlat', 'float') }} as internal_point_latitude,
        {{ x_safe_cast('intptlon', 'float') }} as internal_point_longitude,

        the_geom as geojson,

        {{ x_safe_cast('_socrata_updated_at', 'timestamp') }} as socrata_updated_at,
        {{ x_safe_cast('_ingested_at', 'timestamp') }} as ingested_at

    from deduplicated

)

select * from renamed
