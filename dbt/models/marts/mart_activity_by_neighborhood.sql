{% raw %}
-- mart_activity_by_neighborhood
--
-- Grain: one row per neighborhood per dataset per category per month.
--
-- The same counts as mart_activity_by_h3 at the geography people actually
-- name. This is the model behind "count 311 cases inside this neighborhood",
-- and the thing worth noticing about it is what is not in it: no geometry
-- function, no spatial join, no extension. The neighborhood on every row was
-- decided by an integer H3 cell lookup in ingestion/spatial.py, and this model
-- does a `group by` on the resulting string.
--
-- **Four denominators, and they disagree with each other on purpose.**
-- Ranking neighborhoods by raw count returns a population map. Ranking by
-- rate returns a different list depending on what you divide by, and that
-- disagreement is information rather than noise:
--
--   per 1000 residents        the default. Where is there more of this than
--                             the number of people explains.
--   per 1000 housing units    less sensitive to household size, which varies
--                             a lot across San Francisco.
--   per 1000 businesses       the right denominator for anything commercial.
--                             The Financial District has almost no residents
--                             and enormous daytime activity, so a per-capita
--                             street-cleaning rate there is close to
--                             meaningless and a per-business rate is not.
--   per sq km                 pure density, no human denominator at all.
--
-- The plan asked for rates per parcel and per street mile. Neither dataset is
-- in scope and adding one is a scope decision, not a modelling one, so this
-- normalises by what the warehouse actually holds. ADR-7 records the gap.
--
-- Rates are null, not zero, where the denominator is zero. A neighborhood
-- with no residents does not have an infinite complaint rate; it has a
-- question that does not apply.
{% endraw %}

with activity as (

    select
        analysis_neighborhood,
        dataset,
        category,
        event_month
    from {{ ref('int_point_activity') }}
    -- Events outside every neighborhood are excluded rather than bucketed
    -- into an "Unknown" row. Most of them are the 18 percent of registered
    -- businesses whose address is not in San Francisco, and a neighborhood
    -- mart with a row for "not in the city" invites a total that is not the
    -- sum of its neighborhoods.
    where analysis_neighborhood is not null

),

counted as (

    select
        analysis_neighborhood,
        dataset,
        category,
        event_month,
        count(*) as event_count
    from activity
    group by analysis_neighborhood, dataset, category, event_month

),

final as (

    select
        -- identity
        counted.analysis_neighborhood,
        counted.dataset,
        counted.category,
        counted.event_month,

        -- the count
        counted.event_count,

        -- the denominators, carried on the row so a rate can be checked
        -- without joining back to the dimension
        neighborhood.population,
        neighborhood.housing_units,
        neighborhood.business_count,
        neighborhood.area_sq_km,

        -- the normalised companions
        {{ x_safe_divide(
            '1000.0 * counted.event_count', 'neighborhood.population'
        ) }} as events_per_1000_residents,
        {{ x_safe_divide(
            '1000.0 * counted.event_count', 'neighborhood.housing_units'
        ) }} as events_per_1000_housing_units,
        {{ x_safe_divide(
            '1000.0 * counted.event_count', 'neighborhood.business_count'
        ) }} as events_per_1000_businesses,
        {{ x_safe_divide(
            'counted.event_count', 'neighborhood.area_sq_km'
        ) }} as events_per_sq_km

    from counted
    inner join {{ ref('dim_neighborhood') }} as neighborhood
        on counted.analysis_neighborhood = neighborhood.analysis_neighborhood

)

select * from final
