{% raw %}
-- stg_datasf__311_cases
--
-- This is the worked example for the staging layer. When you hand-write
-- staging models for the other sources, follow this same shape:
--
--   1. source        pull the raw table via {{ source(...) }}
--   2. deduplicated  raw tables are append-only, so keep only the latest
--                    version of each record
--   3. renamed       rename to clear names, cast STRINGs to real types
--
-- NOTE on the {% raw %} wrapper around this comment block: Jinja runs
-- before SQL is parsed, so it does not know that -- starts a comment. The
-- {{ source(...) }} on line 6 above was previously evaluated as real Jinja
-- and failed with "unexpected '.'", which meant this model never parsed at
-- all. If you write Jinja-looking syntax in a comment, keep it inside the
-- raw block.
--
-- Cross-engine note (ADR-1): this project targets both DuckDB and
-- BigQuery, so casts go through the x_safe_cast macro in
-- macros/cross_engine.sql rather than calling safe_cast or try_cast
-- directly. Ask for a logical type ('timestamp', 'float', 'int') and the
-- macro emits the right function and type name for the current target.
-- Do not reintroduce safe_cast or float64 here; they are BigQuery only.
--
-- Tip before writing your own: run
--   select * from <raw table> limit 10
-- to see the real column names, then rename and cast them here. If DataSF
-- ever changes a column name upstream, this is the one place you fix it.
{% endraw %}

with source as (

    select * from {{ source('raw_datasf', 'raw_311_cases') }}

),

deduplicated as (

    -- Each ingestion run appends any row whose :updated_at changed, so a
    -- case that was opened and later closed exists twice in raw. QUALIFY
    -- keeps only the most recently updated version of each case.
    -- QUALIFY is supported on both DuckDB and BigQuery.
    -- The _ingested_at tiebreak makes the winner deterministic when two
    -- copies share an _socrata_updated_at, which happens after a resumed run
    -- re-reads rows it had already written.
    select *
    from source
    qualify row_number() over (
        partition by service_request_id
        order by
            {{ x_cast('_socrata_updated_at', 'timestamp') }} desc,
            {{ x_cast('_ingested_at', 'timestamp') }} desc
    ) = 1

),

renamed as (

    select
        -- identifiers
        service_request_id as case_id,

        -- timestamps (x_safe_cast returns null instead of erroring on bad data)
        {{ x_safe_cast('requested_datetime', 'timestamp') }} as opened_at,
        {{ x_safe_cast('closed_date', 'timestamp') }} as closed_at,
        {{ x_safe_cast('updated_datetime', 'timestamp') }} as last_updated_at,

        -- case details
        status_description as status,
        agency_responsible as agency,
        service_name as service_category,
        service_subtype as service_subcategory,
        address,
        -- 311 publishes this as "9.00000" where permits publish "9", so the
        -- x_safe_int macro routes through float; a direct int cast would
        -- null every value here. Typed rather than passed through as a
        -- string so it joins to the other staging models, which is the whole
        -- point of having a district column on both.
        {{ x_safe_int('supervisor_district') }} as upstream_supervisor_district,
        analysis_neighborhood as upstream_analysis_neighborhood,
        police_district,
        source as request_source,

        -- pipeline metadata
        {{ x_safe_cast('_socrata_updated_at', 'timestamp') }} as socrata_updated_at,
        {{ x_safe_cast('_ingested_at', 'timestamp') }} as ingested_at

    from deduplicated

),

final as (

    -- Geography arrives from stg_spatial__point_geography rather than being
    -- parsed here: coordinates, H3 cells at r8 and r10, and the neighborhood
    -- and district the case is exactly inside. This is ADR-2 landing, three
    -- ADRs later than it was written, and it is why the two upstream
    -- district columns above are now prefixed upstream_. They are kept for
    -- comparison and are not the answer; ADR-2 explains why they cannot be.
    {{ join_point_geography('renamed', 'raw_311_cases', 'case_id') }}

)

select * from final
