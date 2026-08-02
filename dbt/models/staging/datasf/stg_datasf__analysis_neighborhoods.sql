{% raw %}
-- stg_datasf__analysis_neighborhoods
--
-- Grain: one row per analysis neighborhood. 41 of them.
--
-- The city's standard reporting geography, and the boundary set most DataSF
-- datasets already stamp onto rows as a free-text string. This model is the
-- authoritative list of those names; the strings on event rows are not,
-- because they were assigned when the row was created rather than recomputed
-- when boundaries moved (ADR-2).
--
-- The name is also the key. There is no numeric id upstream, so `nhood`
-- carries both roles and any rename upstream is a breaking change to every
-- join in the warehouse. That is a real fragility and it is upstream's, not
-- ours to fix by inventing a surrogate that nothing else would recognise.
--
-- The geometry is carried as GeoJSON text and is not parsed here. Cell
-- coverage is precomputed by ingestion/spatial.py and read through
-- stg_spatial__polygon_h3, which is what ADR-6 buys: no engine needs to know
-- what the_geom means.
{% endraw %}

with source as (

    select * from {{ source('raw_datasf', 'raw_analysis_neighborhoods') }}

),

deduplicated as (

    select *
    from source
    qualify row_number() over (
        partition by nhood
        order by
            {{ x_cast('_socrata_updated_at', 'timestamp') }} desc,
            {{ x_cast('_ingested_at', 'timestamp') }} desc
    ) = 1

),

renamed as (

    select
        nhood as analysis_neighborhood,

        -- Areas as the city publishes them. Kept because they are a free
        -- independent check on the spherical area this project computes in
        -- ingestion/geometry.py, and the two agreeing is worth more than
        -- either alone.
        {{ x_safe_cast('sum_sqmi', 'float') }} as published_area_sq_mi,
        {{ x_safe_cast('sum_acres', 'float') }} as published_area_acres,

        -- geometry, as text. Nothing joins on this.
        the_geom as geojson,

        {{ x_safe_cast('_socrata_updated_at', 'timestamp') }} as socrata_updated_at,
        {{ x_safe_cast('_ingested_at', 'timestamp') }} as ingested_at

    from deduplicated

)

select * from renamed
