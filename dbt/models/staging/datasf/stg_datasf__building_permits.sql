{% raw %}
-- stg_datasf__building_permits
--
-- Grain: one row per permit record.
--
-- Deliberately NOT one row per permit. permit_number repeats: 1,147,326
-- distinct numbers across 1,292,923 records upstream, with as many as 101
-- records sharing one number, because revisions and addenda are filed as
-- separate records against the same permit. record_id is the unique one and
-- is what the grain test goes on. Do not add a unique test to permit_number;
-- it will fail, and the failure is the data being right.
--
-- Follows the shape of stg_datasf__311_cases: source / deduplicated /
-- renamed. See that model for why this header is wrapped in {% raw %} and why
-- every cast goes through the x_* macros in macros/cross_engine.sql instead
-- of safe_cast or float64.
{% endraw %}

with source as (

    select * from {{ source('raw_datasf', 'raw_building_permits') }}

),

deduplicated as (

    -- The raw zone is append-only, so a permit re-ingested after an upstream
    -- edit exists more than once. Keep the newest version.
    --
    -- The _ingested_at tiebreak matters more here than on 311: DataSF
    -- bulk-refreshes this dataset, so tens of thousands of rows can carry an
    -- identical _socrata_updated_at and ordering on that alone leaves the
    -- winner to chance. Preferring the most recently ingested copy makes the
    -- model deterministic, which is what lets the two engines be compared
    -- row for row.
    select *
    from source
    qualify row_number() over (
        partition by record_id
        order by
            {{ x_cast('_socrata_updated_at', 'timestamp') }} desc,
            {{ x_cast('_ingested_at', 'timestamp') }} desc
    ) = 1

),

renamed as (

    select
        -- identifiers
        record_id as permit_record_id,
        permit_number,

        -- what kind of permit
        permit_type as permit_type_code,
        permit_type_definition as permit_type,
        application_submission_method as submission_method,

        -- lifecycle. A permit moves filed -> approved -> issued -> completed,
        -- and most rows stop partway, so every one of these is nullable.
        status as permit_status,
        {{ x_safe_cast('status_date', 'timestamp') }} as status_changed_at,
        {{ x_safe_cast('permit_creation_date', 'timestamp') }} as created_at,
        {{ x_safe_cast('filed_date', 'timestamp') }} as filed_at,
        {{ x_safe_cast('approved_date', 'timestamp') }} as approved_at,
        {{ x_safe_cast('issued_date', 'timestamp') }} as issued_at,
        {{ x_safe_cast('completed_date', 'timestamp') }} as completed_at,
        {{ x_safe_cast('first_construction_document_date', 'timestamp') }}
            as first_construction_doc_at,
        {{ x_safe_cast('last_permit_activity_date', 'timestamp') }} as last_activity_at,

        -- address
        street_number,
        street_number_suffix,
        street_name,
        street_suffix,
        unit,
        unit_suffix,
        zipcode,
        block,
        lot,
        description as permit_description,

        -- money. Both are kept on purpose: estimated_cost is what the
        -- applicant declared at filing and revised_cost is what the
        -- department settled on, so they answer different questions and the
        -- gap between them is itself interesting.
        {{ x_safe_cast('estimated_cost', 'float') }} as estimated_cost,
        {{ x_safe_cast('revised_cost', 'float') }} as revised_cost,

        -- scope of work
        existing_use,
        proposed_use,
        existing_occupancy,
        proposed_occupancy,
        existing_construction_type_description as existing_construction_type,
        proposed_construction_type_description as proposed_construction_type,
        {{ x_safe_int('existing_units') }} as existing_units,
        {{ x_safe_int('proposed_units') }} as proposed_units,
        {{ x_safe_int('number_of_existing_stories') }} as existing_stories,
        {{ x_safe_int('number_of_proposed_stories') }} as proposed_stories,
        {{ x_safe_int('plansets') }} as plansets,

        -- flags. adu is published as Y or N on every row, so a plain
        -- comparison is right. The rest are only present when true, so their
        -- null means false rather than unknown and has to be coalesced or
        -- every downstream filter silently drops rows.
        adu = 'Y' as is_adu,
        coalesce(site_permit = 'Y', false) as is_site_permit,
        coalesce(fire_only_permit = 'Y', false) as is_fire_only_permit,
        coalesce(reroof = 'Y', false) as is_reroof,
        coalesce(structural_notification = 'Y', false) as needs_structural_review,
        coalesce(primary_address_flag = 'Y', false) as is_primary_address,

        -- geography
        -- Unlike 311, permits carry no flat lat/long columns; the coordinates
        -- live inside the `location` GeoJSON point, which normalize_record
        -- stored as JSON text. GeoJSON orders coordinates [longitude,
        -- latitude], the reverse of how they are usually spoken, so index 0
        -- is the longitude. ADR-2's H3 cell id attaches here later.
        {{ x_safe_cast(x_json_extract_scalar('location', '$.coordinates[1]'), 'float') }}
            as latitude,
        {{ x_safe_cast(x_json_extract_scalar('location', '$.coordinates[0]'), 'float') }}
            as longitude,
        {{ x_safe_int('supervisor_district') }} as supervisor_district,
        neighborhoods_analysis_boundaries as analysis_neighborhood,

        -- pipeline metadata
        {{ x_safe_cast('_socrata_updated_at', 'timestamp') }} as socrata_updated_at,
        {{ x_safe_cast('_ingested_at', 'timestamp') }} as ingested_at

    from deduplicated

)

select * from renamed
