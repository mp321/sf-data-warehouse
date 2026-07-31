{% raw %}
-- stg_datasf__city_budget
--
-- Grain: one row per published budget line.
--
-- There is no natural key. The full dimensional combination is not unique
-- either: fiscal_year plus department, fund, program, object, sub_object and
-- character still returns groups of up to six rows upstream, because the
-- city publishes a line per originating appropriation rather than one
-- consolidated line. Summing without grouping first therefore works, but
-- counting rows does not mean counting budget items.
--
-- So the grain key is Socrata's own row id, exposed as budget_line_id. It is
-- stable across re-ingests of the same row, which is exactly what the
-- deduplicated CTE needs, and it is the only column that can carry a unique
-- test honestly.
--
-- Demoted source under ADR-3: ingested and modelled, no freshness SLA, no
-- marts until the department crosswalk question has an answer.
--
-- Follows the shape of stg_datasf__311_cases: source / deduplicated /
-- renamed. See that model for why this header is wrapped in {% raw %}.
{% endraw %}

with source as (

    select * from {{ source('raw_datasf', 'raw_city_budget') }}

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
        _socrata_id as budget_line_id,

        -- when and which side of the ledger
        {{ x_safe_int('fiscal_year') }} as fiscal_year,
        revenue_or_spending as ledger_type,

        -- who. department_code is the join key a future crosswalk to the 311
        -- agency_responsible field would hang off; department is its label
        -- and DataSF rewords it between years.
        department_code,
        department as department_name,
        organization_group_code,
        organization_group as organization_group_name,
        program_code,
        program as program_name,

        -- what the money is. Four nested levels, coarse to fine:
        -- character (Salaries) -> object (Safety) -> sub_object (Uniforms).
        -- `character` is a SQL type name but not a reserved word on either
        -- engine, so it needs no quoting; it is aliased anyway so nobody has
        -- to rediscover that.
        character_code,
        character as character_name,
        object_code,
        object as object_name,
        sub_object_code,
        sub_object as sub_object_name,

        -- where the money sits
        fund_code,
        fund as fund_name,
        fund_type_code,
        fund_type as fund_type_name,
        fund_category_code,
        fund_category as fund_category_name,
        related_govt_unit,

        -- the amount. Negative values are normal and are not errors: they are
        -- transfers out and appropriation reversals, so filtering them away
        -- to make a chart look sensible will break the totals.
        {{ x_safe_cast('budget', 'float') }} as budget_amount,

        -- pipeline metadata
        {{ x_safe_cast('_socrata_updated_at', 'timestamp') }} as socrata_updated_at,
        {{ x_safe_cast('_ingested_at', 'timestamp') }} as ingested_at

    from deduplicated

)

select * from renamed
