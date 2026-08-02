{% raw %}
-- stg_datasf__street_trees
--
-- Grain: one row per tree site.
--
-- A "tree site" is a planting location, not necessarily a living tree:
-- qlegalstatus distinguishes a DPW-maintained tree from a significant tree
-- from a plot that is currently empty, and planttype separates trees from
-- landscaping. Counting rows counts sites; filter on planttype and
-- qlegalstatus first if you mean living street trees.
--
-- Nearly 200,000 rows spread evenly across the city, which makes this the
-- most useful dataset here for checking the H3 machinery: 311 and permits
-- cluster hard enough that a broken cell assignment could hide in the noise,
-- and trees do not.
--
-- Coordinates come both ways upstream. There are flat latitude and longitude
-- columns and an xcoord/ycoord pair in California State Plane Zone III feet;
-- the registry points at the flat degree columns and the state plane pair is
-- carried through unparsed rather than converted, since nothing here needs it.
--
-- Follows the shape of stg_datasf__311_cases: source / deduplicated /
-- renamed. See that model for why this header is wrapped in {% raw %}.
{% endraw %}

with source as (

    select * from {{ source('raw_datasf', 'raw_street_trees') }}

),

deduplicated as (

    select *
    from source
    qualify row_number() over (
        partition by treeid
        order by
            {{ x_cast('_socrata_updated_at', 'timestamp') }} desc,
            {{ x_cast('_ingested_at', 'timestamp') }} desc
    ) = 1

),

renamed as (

    select
        -- identifiers
        treeid as tree_id,

        -- what it is. qspecies is "Genus species :: Common Name" in one
        -- string, published that way and split in a mart if anyone needs it.
        qspecies as species,
        planttype as plant_type,
        qlegalstatus as legal_status,

        -- who looks after it
        qcaretaker as caretaker,
        qcareassistant as care_assistant,

        -- when. plantdate is null on well over half the rows, because the
        -- city inherited most of this inventory without planting records.
        {{ x_safe_cast('plantdate', 'timestamp') }} as planted_at,

        -- where it sits, as published
        qaddress as address,
        siteorder as site_order,
        qsiteinfo as site_info,
        plotsize as plot_size,
        permitnotes as permit_notes,

        -- size. dbh is diameter at breast height in inches, and it needs
        -- clamping at both ends.
        --
        -- Zero and absent both occur and mean "not measured" rather than "no
        -- trunk", so zero is nulled to keep an average off the floor.
        --
        -- The upper clamp is the interesting one. Four rows carry 9999,
        -- 3030, 1920 and 1530 inches, which are 830, 252, 160 and 127 feet
        -- of trunk diameter. 9999 is the classic not-recorded sentinel and
        -- the others look like a decimal point lost in data entry. The
        -- widest tree trunk ever measured is about 38 feet, so 240 inches is
        -- comfortably above anything real and below every one of these.
        -- Nulled rather than kept, because these are not measurements and an
        -- average that includes 9999 is wrong by more than it is right; the
        -- rows themselves survive with every other column intact.
        nullif(
            case
                when {{ x_safe_cast('dbh', 'float') }} > 240 then null
                else {{ x_safe_cast('dbh', 'float') }}
            end,
            0
        ) as diameter_at_breast_height_in,

        -- California State Plane Zone III feet, carried through unparsed.
        xcoord as state_plane_x,
        ycoord as state_plane_y,

        -- pipeline metadata
        {{ x_safe_cast('_socrata_updated_at', 'timestamp') }} as socrata_updated_at,
        {{ x_safe_cast('_ingested_at', 'timestamp') }} as ingested_at

    from deduplicated

),

final as (

    {{ join_point_geography('renamed', 'raw_street_trees', 'tree_id') }}

)

select * from final
