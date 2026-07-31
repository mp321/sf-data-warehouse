{% raw %}
-- stg_datasf__film_locations
--
-- Grain: one row per published film location record.
--
-- Not one row per film, and not one row per film per location either. A
-- title appears once per place it was shot (350 titles across 2,214 rows),
-- and the obvious composite key of title, release_year and location is still
-- not unique: DataSF publishes the same title at the same address more than
-- once, up to three times. Those repeats carry no distinguishing field, so
-- there is nothing to deduplicate them by and collapsing them would be a
-- guess. The grain key is Socrata's row id.
--
-- Demoted source under ADR-3: this is the pipeline canary. It is small
-- enough to ingest end to end in seconds, which is what makes it the smoke
-- test in SETUP.md. Its locations are free text, so ADR-2 does not apply
-- even though it happens to carry coordinates.
--
-- Follows the shape of stg_datasf__311_cases: source / deduplicated /
-- renamed. See that model for why this header is wrapped in {% raw %}.
{% endraw %}

with source as (

    select * from {{ source('raw_datasf', 'raw_film_locations') }}

),

deduplicated as (

    select *
    from source
    qualify row_number() over (
        partition by _socrata_id
        order by
            {{ x_cast('_socrata_updated_at', 'timestamp') }} desc,
            {{ x_cast('_ingested_at', 'timestamp') }} desc
    ) = 1

),

renamed as (

    select
        -- identifiers
        _socrata_id as film_location_id,

        -- the film
        title as film_title,
        {{ x_safe_int('release_year') }} as release_year,
        production_company,
        distributor,
        director,
        writer,

        -- Cast, as three positional columns rather than a list, because that
        -- is how DataSF publishes it and reshaping is a mart's job. actor_1
        -- is close to always present, actor_3 is often null.
        actor_1 as actor_1_name,
        actor_2 as actor_2_name,
        actor_3 as actor_3_name,

        -- where it was shot. Free text as written by the submitter, e.g.
        -- "1201 California St. at Jones St.", so it does not join to
        -- anything and is not an address.
        locations as location_description,
        analysis_neighborhood,
        {{ x_safe_int('supervisor_district') }} as supervisor_district,

        -- Flat lat/long columns exist here, unlike permits, so no JSON
        -- extraction is needed. They are null for roughly one row in twenty.
        {{ x_safe_cast('latitude', 'float') }} as latitude,
        {{ x_safe_cast('longitude', 'float') }} as longitude,

        -- the reason this dataset is in scope at all
        fun_facts,

        -- pipeline metadata
        {{ x_safe_cast('_socrata_updated_at', 'timestamp') }} as socrata_updated_at,
        {{ x_safe_cast('_ingested_at', 'timestamp') }} as ingested_at

    from deduplicated

)

select * from renamed
