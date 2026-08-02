{% raw %}
-- stg_datasf__business_locations
--
-- Grain: one row per registered business location.
--
-- Not one row per business. `uniqueid` concatenates the certificate number,
-- a location sequence and an ownership sequence, so a business that moves or
-- changes hands accumulates a row per location it has held. Counting rows
-- counts registrations, not businesses; count distinct certificate_number for
-- that.
--
-- Both a subject and a denominator. It is the only dataset here that says
-- where commercial activity is, so "311 cases per 1000 businesses" is a
-- materially different question from "per 1000 residents", and a
-- street-cleaning complaint rate normalised by residents in the Financial
-- District is close to meaningless.
--
-- Note what is NOT dropped here. About 18 percent of rows carry coordinates
-- outside San Francisco, because the registry records where a business is and
-- plenty of businesses with a San Francisco tax certificate are located
-- elsewhere. Those rows are correct and are kept; they simply get no
-- neighborhood, and stg_spatial__point_geography marks them out_of_bounds
-- rather than silently discarding them.
--
-- Follows the shape of stg_datasf__311_cases: source / deduplicated /
-- renamed, then a single join for geography. See that model for why this
-- header is wrapped in {% raw %}.
{% endraw %}

with source as (

    select * from {{ source('raw_datasf', 'raw_business_locations') }}

),

deduplicated as (

    select *
    from source
    qualify row_number() over (
        partition by uniqueid
        order by
            {{ x_cast('_socrata_updated_at', 'timestamp') }} desc,
            {{ x_cast('_ingested_at', 'timestamp') }} desc
    ) = 1

),

renamed as (

    select
        -- identifiers
        uniqueid as business_location_id,
        certificate_number,
        ttxid as tax_id,

        -- who
        ownership_name,
        dba_name as business_name,

        -- what. lic_code_description is the single business activity code;
        -- the _list variant is a delimited string of all of them and is kept
        -- as published, because splitting it is a mart's job.
        lic as license_code,
        lic_code_description as business_category,
        lic_code_descriptions_list as business_category_list,
        self_reported_naics_code as naics_code,

        -- lifecycle. dba_* is the trading name's life, location_* is this
        -- location's life, and they differ often enough that using one for
        -- the other changes the answer. location_start_date is the event
        -- date the activity marts count on.
        {{ x_safe_cast('dba_start_date', 'timestamp') }} as dba_started_at,
        {{ x_safe_cast('dba_end_date', 'timestamp') }} as dba_ended_at,
        {{ x_safe_cast('location_start_date', 'timestamp') }} as location_started_at,
        {{ x_safe_cast('location_end_date', 'timestamp') }} as location_ended_at,
        -- Present only when true upstream, so null means false rather than
        -- unknown and has to be coalesced or every filter drops rows.
        coalesce(administratively_closed = 'true', false) as is_administratively_closed,
        location_end_date is null as is_active,

        -- where, as published. Kept beside the computed geography below so
        -- the two can be compared; ADR-2 is on record that the upstream
        -- string is not trustworthy.
        full_business_address as business_address,
        city as business_city,
        state as business_state,
        business_zip,
        business_corridor,
        community_benefit_district,
        neighborhoods_analysis_boundaries as upstream_analysis_neighborhood,
        {{ x_safe_int('supervisor_district') }} as upstream_supervisor_district,

        -- taxes, as flags
        coalesce(parking_tax = 'true', false) as pays_parking_tax,
        coalesce(transient_occupancy_tax = 'true', false) as pays_transient_occupancy_tax,

        -- pipeline metadata
        {{ x_safe_cast('_socrata_updated_at', 'timestamp') }} as socrata_updated_at,
        {{ x_safe_cast('_ingested_at', 'timestamp') }} as ingested_at

    from deduplicated

),

final as (

    {{ join_point_geography('renamed', 'raw_business_locations', 'business_location_id') }}

)

select * from final
