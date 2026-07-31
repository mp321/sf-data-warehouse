{% raw %}
-- mart_pipeline_freshness
--
-- Grain: one row per registered source.
--
-- The human check-in view: is every source current, is it growing, and did
-- its tests pass. Built to be read directly and to be serialised into the
-- context pack later, which is why every column is a plain scalar and no
-- column needs another query to interpret.
--
-- Two deliberate departures from the marts rules in marts/README.md:
--
--   1. It selects from sources rather than only from staging models. A
--      freshness view has to report what landed in the raw zone; counting
--      rows in staging would count post-deduplication rows, which is a
--      different and less useful number.
--   2. It reads meta_dbt_run_results, a table created by the on-run-start
--      hook rather than by a model, so it is referenced by name and not by
--      ref(). See macros/audit_run_results.sql.
--
-- The consequence of (2) worth knowing before reading the test columns: dbt
-- builds models before on-run-end writes results, so the test counts here
-- describe the most recent COMPLETED run, not the one that built this table.
-- On a first ever build they are all zero. That is not a bug and cannot be
-- fixed by reordering; a run cannot report its own outcome.
--
-- The source list is var('pipeline_sources') in dbt_project.yml, and the
-- loop below turns it into one union-all branch per source. Adding a source
-- there adds a row here.
{% endraw %}

{%- set sources = var('pipeline_sources') -%}

with row_counts as (

    -- Actual rows on the ground, per source. Counted from raw rather than
    -- trusted from the run manifests, so that a manifest lost or a load that
    -- never ran shows up as a disagreement rather than as a clean number.
    {% for s in sources %}
    select
        '{{ s.name }}' as source_name,
        count(*) as row_count,
        max({{ x_safe_cast('_ingested_at', 'timestamp') }}) as last_load_at,
        max(_ingest_run_id) as last_ingest_run_id
    from {{ source('raw_datasf', s.source_table) }}
    {%- if not loop.last %}
    union all
    {% endif %}
    {%- endfor %}

),

registry as (

    -- The static half of the row: what we expect of each source. Kept in a
    -- CTE rather than inlined so the joins below read as data, not as Jinja.
    {% for s in sources %}
    select
        '{{ s.name }}' as source_name,
        '{{ s.source_table }}' as source_table,
        '{{ s.staging_model }}' as staging_model,
        '{{ s.tier }}' as tier,
        {{ s.stale_after_hours if s.stale_after_hours is not none else 'null' }}
            as stale_after_hours
    {%- if not loop.last %}
    union all
    {% endif %}
    {%- endfor %}

),

latest_run as (

    -- The most recent ingestion ATTEMPT, which is not the same as the most
    -- recent load. A run that finds nothing new writes no Parquet and so
    -- leaves no trace in row_counts, but it is exactly what distinguishes a
    -- healthy quiet source from a broken one.
    select
        dataset as source_name,
        run_id as last_run_id,
        finished_at as last_run_finished_at,
        status as last_run_status,
        rows_written as rows_written_last_run,
        mode as last_run_mode
    from {{ source('raw_datasf', 'raw_ingest_runs') }}
    qualify row_number() over (
        partition by dataset order by run_id desc
    ) = 1

),

latest_test_run as (

    -- One invocation id: the most recent completed dbt run. Isolated in its
    -- own CTE because "most recent" has to be decided once for all sources,
    -- otherwise a source with no tests would silently pick a different run.
    select invocation_id
    from {{ run_results_relation() }}
    qualify row_number() over (order by run_started_at desc) = 1

),

test_results as (

    select
        results.tested_model,
        max(results.run_started_at) as last_test_run_at,
        count(*) as tests_total,
        sum(case when results.status = 'pass' then 1 else 0 end) as tests_passed,
        sum(case when results.status = 'fail' then 1 else 0 end) as tests_failed,
        sum(case when results.status = 'warn' then 1 else 0 end) as tests_warned,
        sum(case when results.status = 'error' then 1 else 0 end) as tests_errored
    from {{ run_results_relation() }} as results
    inner join latest_test_run on results.invocation_id = latest_test_run.invocation_id
    where results.resource_type = 'test'
    group by results.tested_model

),

final as (

    select
        -- identity
        registry.source_name,
        registry.source_table,
        registry.staging_model,
        registry.tier,

        -- volume. row_delta is what the latest ingestion run added, so it is
        -- 0 when the run found nothing new, which is the common healthy case
        -- and is why it comes from the run log rather than from a difference
        -- of two counts.
        row_counts.row_count,
        coalesce(latest_run.rows_written_last_run, 0) as row_delta,
        row_counts.row_count
        - coalesce(latest_run.rows_written_last_run, 0) as previous_row_count,

        -- recency. last_load_at is when rows last landed; last_run_finished_at
        -- is when ingestion last ran at all. Both, because they answer
        -- different questions and only their difference explains a quiet source.
        row_counts.last_load_at,
        row_counts.last_ingest_run_id,
        latest_run.last_run_finished_at,
        latest_run.last_run_status,
        latest_run.last_run_mode,
        {{ x_hours_between('row_counts.last_load_at', x_utc_now()) }} as hours_since_load,
        {{ x_hours_between('latest_run.last_run_finished_at', x_utc_now()) }}
            as hours_since_run_attempt,

        -- freshness verdict. A null threshold means the source has no SLA
        -- (the demoted tier in ADR-3): report the age, never call it stale.
        registry.stale_after_hours,
        case
            when registry.stale_after_hours is null then false
            when row_counts.last_load_at is null then true
            else {{ x_hours_between('row_counts.last_load_at', x_utc_now()) }}
                > registry.stale_after_hours
        end as is_stale,

        -- tests, from the most recent completed dbt run. Zero rather than
        -- null when a source has never been tested, so that summing these
        -- columns across sources works without a coalesce at every call site.
        coalesce(test_results.tests_total, 0) as tests_total,
        coalesce(test_results.tests_passed, 0) as tests_passed,
        coalesce(test_results.tests_failed, 0) as tests_failed,
        coalesce(test_results.tests_warned, 0) as tests_warned,
        coalesce(test_results.tests_errored, 0) as tests_errored,
        test_results.last_test_run_at,

        -- one column to read when you only read one column
        case
            when latest_run.last_run_status is not null
                and latest_run.last_run_status <> 'success' then false
            when coalesce(test_results.tests_failed, 0) > 0 then false
            when coalesce(test_results.tests_errored, 0) > 0 then false
            when registry.stale_after_hours is null then true
            when row_counts.last_load_at is null then false
            else {{ x_hours_between('row_counts.last_load_at', x_utc_now()) }}
                <= registry.stale_after_hours
        end as is_healthy

    from registry
    left join row_counts on registry.source_name = row_counts.source_name
    left join latest_run on registry.source_name = latest_run.source_name
    left join test_results on registry.staging_model = test_results.tested_model

)

select * from final
