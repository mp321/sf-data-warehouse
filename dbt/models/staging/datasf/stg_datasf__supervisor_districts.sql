{% raw %}
-- stg_datasf__supervisor_districts
--
-- Grain: one row per supervisor district. 11 of them.
--
-- These are the boundaries drawn in the 2022 redistricting. That matters more
-- than it looks: every event row in this warehouse from before 2022 carries an
-- upstream supervisor_district assigned under the 2012 lines, so comparing an
-- upstream district against one computed here will disagree for reasons that
-- are historical rather than wrong. The 2012 boundaries are a separate DataSF
-- dataset (keex-zmn4) and are deliberately not ingested; carrying two boundary
-- vintages means every question needs a date before it can be answered.
--
-- sup_dist_num is the join key and is stored as an integer, matching the
-- supervisor_district column on the event staging models.
{% endraw %}

with source as (

    select * from {{ source('raw_datasf', 'raw_supervisor_districts') }}

),

deduplicated as (

    select *
    from source
    qualify row_number() over (
        partition by sup_dist_num
        order by
            {{ x_cast('_socrata_updated_at', 'timestamp') }} desc,
            {{ x_cast('_ingested_at', 'timestamp') }} desc
    ) = 1

),

renamed as (

    -- Published as "11.0", so this goes through x_safe_int rather than a
    -- direct int cast, which would null every value. Same trap as
    -- supervisor_district on 311.
    select
        {{ x_safe_int('sup_dist_num') }} as supervisor_district,
        sup_dist_name as district_name,
        sup_name as supervisor_name,
        polygon as geojson,

        {{ x_safe_cast('_socrata_updated_at', 'timestamp') }} as socrata_updated_at,
        {{ x_safe_cast('_ingested_at', 'timestamp') }} as ingested_at

    from deduplicated

)

select * from renamed
