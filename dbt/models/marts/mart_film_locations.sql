{% raw %}
-- mart_film_locations
--
-- Grain: one row per published film location record.
--
-- The demo mart, and the one that is fun to query. Every shoot location with
-- the neighborhood it is actually in, plus a count of how many locations each
-- title used, so "which film was shot in the most places" and "which
-- neighborhood appears on screen most" are both one query away.
--
-- It also quietly settles a question. ADR-3 demoted this dataset partly
-- because "its locations are free text rather than coordinates, so it cannot
-- participate in ADR-2". The free-text `locations` column is real and is kept
-- below, but the dataset also publishes flat latitude and longitude, usable
-- on 2,127 of 2,214 rows. So no geocoding decision was needed: there was
-- nothing to geocode, and the neighborhood on every row here was computed the
-- same way as on every other point dataset. ADR-7 corrects the record.
--
-- upstream_analysis_neighborhood is kept beside the computed one on purpose.
-- This is the smallest dataset in the warehouse, which makes it the cheapest
-- place to eyeball how often the neighborhood DataSF stamped on a row agrees
-- with the one its own coordinates imply.
{% endraw %}

with films as (

    select * from {{ ref('stg_datasf__film_locations') }}

),

title_totals as (

    -- Window rather than a group-by-and-join: one pass, and the count stays
    -- attached to every row of the title rather than needing a join back.
    select
        film_title,
        count(*) as locations_for_title,
        count(distinct analysis_neighborhood) as neighborhoods_for_title
    from films
    group by film_title

),

final as (

    select
        -- identity
        films.film_location_id,
        films.film_title,
        films.release_year,

        -- who made it
        films.production_company,
        films.distributor,
        films.director,
        films.writer,
        films.actor_1_name,
        films.actor_2_name,
        films.actor_3_name,

        -- where, as written by whoever filed the location permit. Not an
        -- address and not a join key: "Columbus between Filbert and Lombard".
        films.location_description,

        -- where, computed. This is the join key to dim_neighborhood.
        films.analysis_neighborhood,
        films.supervisor_district_id,
        films.latitude,
        films.longitude,
        films.is_usable_coordinate,
        films.h3_r8,
        films.h3_r10,

        -- where, as DataSF stamped it. Kept for comparison only.
        films.upstream_analysis_neighborhood,

        -- how much of the city this title used
        title_totals.locations_for_title,
        title_totals.neighborhoods_for_title,

        films.fun_facts

    from films
    left join title_totals on films.film_title = title_totals.film_title

)

select * from final
