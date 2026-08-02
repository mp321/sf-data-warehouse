{% raw %}
-- mart_budget_by_department_year
--
-- Grain: one row per fiscal year per department per ledger type.
--
-- The non-spatial mart. City budget rolled up from line items to departments,
-- with year-over-year change and each department's share of the year.
--
-- **What this deliberately does not do.** It does not join budget to 311.
-- That join is the project's headline question and it needs a crosswalk
-- between budget `department_code` and the 311 `agency_responsible` field,
-- which are two independently maintained taxonomies with no reason to agree.
-- ADR-3 identified building that crosswalk as a project in itself and blocked
-- any budget mart until it existed. ADR-7 revisits that: the objection was
-- always to the crosswalk, not to the budget data, and a mart that stays
-- inside one taxonomy owes it nothing. So this aggregates budget and stops,
-- and the spend-versus-demand question is still open.
--
-- **Ledger type is part of the grain and must stay in your group by.**
-- The dataset carries revenue and spending as rows of the same shape
-- distinguished only by `revenue_or_spending`. Summing without splitting them
-- nets a department's income against its outgoings and produces a number that
-- looks like a budget and is not one.
--
-- **Negative amounts are correct.** They are transfers out and appropriation
-- reversals. Filtering them to make a chart behave breaks the totals.
{% endraw %}

with budget as (

    select
        fiscal_year,
        ledger_type,
        department_code,
        department_name,
        organization_group_name,
        budget_amount
    from {{ ref('stg_datasf__city_budget') }}
    where fiscal_year is not null
        and department_code is not null

),

aggregated as (

    select
        fiscal_year,
        ledger_type,
        department_code,
        -- DataSF rewords department names between years, so one code can
        -- carry several labels across the history. max() picks one
        -- deterministically for display; the code is the key.
        max(department_name) as department_name,
        max(organization_group_name) as organization_group_name,
        -- SUM ignores nulls, so this is the total of the lines that parsed
        -- and is null only when not one line in the group did. That is left
        -- null rather than coalesced to zero on purpose: a group whose
        -- amounts were all unreadable has an unknown total, and zero is a
        -- claim about the city's budget rather than a statement about our
        -- data. unparseable_line_count below is how you tell the two apart.
        sum(budget_amount) as budget_amount,
        count(*) as line_item_count,
        sum(case when budget_amount is null then 1 else 0 end) as unparseable_line_count
    from budget
    group by fiscal_year, ledger_type, department_code

),

with_history as (

    select
        *,
        lag(budget_amount) over (
            partition by department_code, ledger_type
            order by fiscal_year
        ) as prior_year_amount,
        -- The denominator for the normalised share below. Computed as a
        -- window rather than a join so the mart stays one pass.
        sum(budget_amount) over (
            partition by fiscal_year, ledger_type
        ) as citywide_amount
    from aggregated

),

final as (

    select
        -- identity
        with_history.fiscal_year,
        with_history.ledger_type,
        with_history.department_code,
        with_history.department_name,
        with_history.organization_group_name,

        -- the amounts
        with_history.budget_amount,
        with_history.line_item_count,
        with_history.unparseable_line_count,
        with_history.prior_year_amount,
        with_history.budget_amount - with_history.prior_year_amount as year_over_year_change,

        -- normalised companions. Percent change is null in a department's
        -- first year, which is a real absence rather than a zero, and null
        -- rather than infinite when a department had exactly nothing last
        -- year and something this year.
        100.0 * {{ x_safe_divide(
            'with_history.budget_amount - with_history.prior_year_amount',
            'abs(with_history.prior_year_amount)'
        ) }} as year_over_year_pct_change,
        100.0 * {{ x_safe_divide(
            'with_history.budget_amount', 'with_history.citywide_amount'
        ) }} as pct_of_citywide_amount,

        with_history.citywide_amount

    from with_history

)

select * from final
