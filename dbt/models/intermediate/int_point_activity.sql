{% raw %}
-- int_point_activity
--
-- Grain: one row per dated event, across every point dataset that has a date.
--
-- The spine both activity marts are built on. Four datasets reduced to one
-- shape: what happened, when, what kind of thing it was, and where. Written
-- once here because mart_activity_by_h3 and mart_activity_by_neighborhood ask
-- the same question at two different geographies, and a union that appears in
-- both is a union that will diverge.
--
-- This is the project's first intermediate model. It exists because it is
-- neither a staging model (it unions five sources and picks an event date,
-- which is a modelling decision) nor a mart (nothing queries it directly).
-- CLAUDE.md's directory conventions were updated to name the layer.
--
-- Two judgement calls worth arguing with:
--
--   1. **The event date per dataset is a choice, not a given.** 311 uses when
--      the case was opened, not closed. Permits use when the application was
--      filed, not issued: filing is the demand signal and issuing is the
--      city's response to it, and mixing them makes a permitting backlog look
--      like a drop in construction. Businesses use when the location opened.
--      Trees use the plant date, which is null on most rows, so most trees do
--      not appear here at all. Every one of these is defensible and none is
--      the only answer.
--
--   2. **film_locations is deliberately absent.** It has no event timestamp,
--      only a release year, and a release year is not when the shoot
--      happened. Forcing it to January of its release year would produce a
--      seasonal spike that is an artefact of this model. It has its own mart.
--
-- Rows with no usable coordinate are kept, with null cells and null
-- boundaries. Dropping them here would make every mart's total quietly
-- disagree with its staging model's row count, and the drop rate is exactly
-- what mart_pipeline_freshness reports.
{% endraw %}

with cases as (

    select
        '311_cases' as dataset,
        case_id as event_id,
        {{ x_month_start('opened_at') }} as event_month,
        -- Top-level request type: "Street and Sidewalk Cleaning", "Graffiti".
        service_category as category,
        h3_r8,
        h3_r9,
        h3_r10,
        analysis_neighborhood,
        supervisor_district_id
    from {{ ref('stg_datasf__311_cases') }}

),

permits as (

    select
        'building_permits' as dataset,
        permit_record_id as event_id,
        {{ x_month_start('filed_at') }} as event_month,
        permit_type as category,
        h3_r8,
        h3_r9,
        h3_r10,
        analysis_neighborhood,
        supervisor_district_id
    from {{ ref('stg_datasf__building_permits') }}

),

businesses as (

    select
        'business_locations' as dataset,
        business_location_id as event_id,
        {{ x_month_start('location_started_at') }} as event_month,
        business_category as category,
        h3_r8,
        h3_r9,
        h3_r10,
        analysis_neighborhood,
        supervisor_district_id
    from {{ ref('stg_datasf__business_locations') }}

),

trees as (

    select
        'street_trees' as dataset,
        tree_id as event_id,
        {{ x_month_start('planted_at') }} as event_month,
        plant_type as category,
        h3_r8,
        h3_r9,
        h3_r10,
        analysis_neighborhood,
        supervisor_district_id
    from {{ ref('stg_datasf__street_trees') }}

),

combined as (

    select * from cases
    union all
    select * from permits
    union all
    select * from businesses
    union all
    select * from trees

),

final as (

    -- Undated rows are dropped here and only here. An event with no date
    -- cannot be counted in a month, and carrying it into a monthly mart means
    -- either a null bucket that every consumer has to remember to exclude or
    -- a total that does not equal the sum of its months. Both are worse than
    -- a documented exclusion; the counts that include them live in
    -- mart_pipeline_freshness.
    -- Column order here is sqlfluff's ST06 rather than a preference: simple
    -- targets before calculations, so the coalesce goes last even though
    -- category reads more naturally beside event_month.
    select
        dataset,
        event_id,
        event_month,
        h3_r8,
        h3_r9,
        h3_r10,
        analysis_neighborhood,
        supervisor_district_id,
        coalesce(category, 'Unknown') as category
    from combined
    where event_month is not null

)

select * from final
