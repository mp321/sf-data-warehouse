-- stg_datasf__311_cases
--
-- This is the worked example for the staging layer. When you hand-write
-- staging models for the other three sources, follow this same shape:
--
--   1. source        pull the raw table via {{ source(...) }}
--   2. deduplicated  raw tables are append-only, so keep only the latest
--                    version of each record
--   3. renamed       rename to clear names, cast STRINGs to real types
--
-- Tip before writing your own: run
--   select * from `<project>.raw_datasf.raw_building_permits` limit 10
-- in the BigQuery console to see the real column names, then rename and
-- cast them here. If DataSF ever changes a column name upstream, this is
-- the one place you fix it.

with source as (

    select * from {{ source('raw_datasf', 'raw_311_cases') }}

),

deduplicated as (

    -- Each ingestion run appends any row whose :updated_at changed, so a
    -- case that was opened and later closed exists twice in raw. QUALIFY
    -- keeps only the most recently updated version of each case.
    select *
    from source
    qualify row_number() over (
        partition by service_request_id
        order by cast(_socrata_updated_at as timestamp) desc
    ) = 1

),

renamed as (

    select
        -- identifiers
        service_request_id                                as case_id,

        -- timestamps (safe_cast returns null instead of erroring on bad data)
        safe_cast(requested_datetime as timestamp)        as opened_at,
        safe_cast(closed_date as timestamp)               as closed_at,
        safe_cast(updated_datetime as timestamp)          as last_updated_at,

        -- case details
        status_description                                as status,
        agency_responsible                                as agency,
        service_name                                      as service_category,
        service_subtype                                   as service_subcategory,
        address,
        supervisor_district,
        police_district,
        source                                            as request_source,

        -- geography
        safe_cast(lat as float64)                         as latitude,
        safe_cast(long as float64)                        as longitude,

        -- pipeline metadata
        safe_cast(_socrata_updated_at as timestamp)       as socrata_updated_at,
        safe_cast(_ingested_at as timestamp)              as ingested_at

    from deduplicated

)

select * from renamed
