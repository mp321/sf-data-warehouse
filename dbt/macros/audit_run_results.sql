{#
    Persist dbt's own run results so a model can read them.

    mart_pipeline_freshness reports how many tests passed and failed. dbt
    knows that, but only in memory and only in target/run_results.json, which
    no warehouse can query. These two hooks copy it into a real table.

    Wiring is in dbt_project.yml:
      on-run-start  creates the schema and table if they are missing
      on-run-end    appends one row per node from the run that just finished

    The ordering consequence is worth stating plainly, because it looks like a
    bug otherwise: models build before on-run-end fires, so
    mart_pipeline_freshness reports the PREVIOUS completed run's results, not
    the one building it. A run cannot report on its own outcome. "Most recent
    completed dbt run" is the honest reading of that column and is what the
    model description says.

    The table is append-only and grows by one row per node per run. Nothing
    prunes it yet; at a handful of runs a day that is years away from
    mattering, and the fix is a retention delete in on-run-start.
#}


{%- macro run_results_relation() -%}
    {#- Not a ref(): this table is created by a hook, not by a model, so dbt
        has no node for it. Referenced by name in the mart, which is why the
        name lives in one macro rather than being typed twice. -#}
    {{- target.schema ~ '.meta_dbt_run_results' -}}
{%- endmacro -%}


{#-
    All three hooks return an empty string on `compile` and `parse`, which
    makes dbt skip the hook entirely rather than execute it.

    The reason is the plain one: there is nothing to audit on a compile. No node
    executes, so on-run-end has no results worth recording and the table it
    would write into does not need to exist. A hook that writes a row about
    nothing is a hook that will one day be believed.

    What this guard is NOT is the thing that keeps
    `dbt compile --target bigquery` credential-free. It was added on 2026-08-01
    for that reason and the reason was wrong: in dbt 1.12 `CompileTask` does not
    run on-run-start or on-run-end at all. `safe_run_hooks` is reached only from
    `RunTask.before_run` and `RunTask.after_run`, and `CompileTask` inherits
    `GraphRunnableTask.before_run`, which opens no hook. So this guard could
    never have been what was connecting, and removing it would not restore the
    bug it was written for. Keep it on its own merits; do not credit it with
    the other thing. That job belongs to the compile-bigquery target in the
    Makefile and to scripts/fake-bq-key.sh.
-#}
{%- macro _skip_audit_ddl() -%}
    {{- return(flags.WHICH in ('compile', 'parse')) -}}
{%- endmacro -%}


{%- macro ensure_run_results_schema() -%}
    {%- if _skip_audit_ddl() -%}{{- return('') -}}{%- endif -%}
    create schema if not exists {{ target.schema }}
{%- endmacro -%}


{%- macro ensure_run_results_table() -%}
    {%- if _skip_audit_ddl() -%}{{- return('') -}}{%- endif -%}
    create table if not exists {{ run_results_relation() }} (
        invocation_id {{ x_type('string') }},
        run_started_at {{ x_type('timestamp') }},
        target_name {{ x_type('string') }},
        resource_type {{ x_type('string') }},
        unique_id {{ x_type('string') }},
        node_name {{ x_type('string') }},
        tested_model {{ x_type('string') }},
        status {{ x_type('string') }},
        failures {{ x_type('int') }},
        execution_time_seconds {{ x_type('float') }}
    )
{%- endmacro -%}


{%- macro sql_string(value) -%}
    {#- Single quotes doubled, the one escape both engines agree on. Node
        names cannot contain quotes today; this is here so that stops being
        something you have to know. -#}
    {%- if value is none -%}
        null
    {%- else -%}
        '{{ value | string | replace("'", "''") }}'
    {%- endif -%}
{%- endmacro -%}


{%- macro log_run_results() -%}
    {%- if not execute or not results or _skip_audit_ddl() -%}
        {#- Parse time, a task with no nodes, or a compile.

            The `_skip_audit_ddl()` arm covers the case the first two miss.
            `dbt compile` sets `execute` true and would produce a non-empty
            `results`, one entry per compiled node, so a guard written as
            `not execute or not results` reads as though it covers every
            non-run command and does not. Compiling is not a run and has no
            outcome worth recording.

            Returning an empty string makes dbt skip the hook rather than
            execute a no-op query. -#}
        {{- return('') -}}
    {%- endif -%}

    {%- set rows = [] -%}
    {%- for result in results -%}
        {%- set node = result.node -%}

        {#- A test's unique_id says nothing about what it tests, so resolve
            the model it depends on now, while the manifest is in scope.
            Doing it later in SQL would mean pattern matching on node ids. -#}
        {%- set depends = (node.depends_on.nodes if node.depends_on is defined else []) or [] -%}
        {%- set tested_model = namespace(value=none) -%}
        {%- for dep in depends -%}
            {#- Unique ids look like model.<project>.<name>; take the name.
                No regex here: Jinja's 'match' test is not in dbt's sandboxed
                environment, and a hook that fails to compile takes the whole
                run down at the very end, after everything has already built. -#}
            {%- if tested_model.value is none and dep.startswith('model.') -%}
                {%- set tested_model.value = dep.split('.')[-1] -%}
            {%- endif -%}
        {%- endfor -%}

        {%- set values -%}
            {{ sql_string(invocation_id) }},
            {{ x_cast(sql_string(run_started_at.strftime('%Y-%m-%d %H:%M:%S')), 'timestamp') }},
            {{ sql_string(target.name) }},
            {{ sql_string(node.resource_type) }},
            {{ sql_string(node.unique_id) }},
            {{ sql_string(node.name) }},
            {{ sql_string(tested_model.value) }},
            {{ sql_string(result.status) }},
            {{ result.failures if result.failures is not none else 'null' }},
            {{ result.execution_time | round(4) if result.execution_time is not none else 'null' }}
        {%- endset -%}
        {%- do rows.append('(' ~ values ~ ')') -%}
    {%- endfor -%}

    insert into {{ run_results_relation() }} (
        invocation_id, run_started_at, target_name, resource_type, unique_id,
        node_name, tested_model, status, failures, execution_time_seconds
    )
    values {{ rows | join(',\n    ') }}
{%- endmacro -%}
