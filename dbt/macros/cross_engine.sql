{#
    Cross-engine helpers.

    ADR-1 commits this project to SQL that compiles on both DuckDB (the
    canonical target) and BigQuery (the secondary target). Where the two
    engines spell the same idea differently, the difference lives here and
    nowhere else. Models call these macros; models never call safe_cast,
    try_cast, or a raw type name directly.

    How dispatch works, briefly: adapter.dispatch looks for a macro named
    <adapter>__<name> (for example bigquery__x_safe_cast) and falls back to
    default__<name> when there is no adapter-specific version. The second
    argument is this project's name, which tells dbt where to search.

    Note the {%- -%} whitespace control throughout. Without it every macro
    call injects newlines into the compiled SQL, which makes compiled output
    unreadable and gives sqlfluff spurious layout errors on the model rather
    than on the macro that caused them.

    Adding a new one: write x_thing, default__x_thing, and any
    <adapter>__x_thing overrides, then note it in ADR-1's revisit
    threshold if the count is getting high.
#}


{# ---------------------------------------------------------------------------
   Type names. DuckDB and BigQuery disagree on what a 64 bit float is called,
   among other things, so models ask for a logical type and get the physical
   name for the current target.
   --------------------------------------------------------------------------- #}

{%- macro x_type(logical_type) -%}
    {{- adapter.dispatch('x_type', 'sf_data_warehouse')(logical_type) -}}
{%- endmacro -%}


{%- macro default__x_type(logical_type) -%}
    {#- DuckDB and standard SQL naming. -#}
    {%- set mapping = {
        'float': 'double',
        'int': 'bigint',
        'string': 'varchar',
        'timestamp': 'timestamp',
        'date': 'date',
        'bool': 'boolean'
    } -%}
    {%- if logical_type not in mapping -%}
        {{- exceptions.raise_compiler_error(
            "x_type: unknown logical type '" ~ logical_type ~ "'. Known: "
            ~ mapping.keys() | join(', ')
        ) -}}
    {%- endif -%}
    {{- mapping[logical_type] -}}
{%- endmacro -%}


{%- macro bigquery__x_type(logical_type) -%}
    {%- set mapping = {
        'float': 'float64',
        'int': 'int64',
        'string': 'string',
        'timestamp': 'timestamp',
        'date': 'date',
        'bool': 'bool'
    } -%}
    {%- if logical_type not in mapping -%}
        {{- exceptions.raise_compiler_error(
            "x_type: unknown logical type '" ~ logical_type ~ "'. Known: "
            ~ mapping.keys() | join(', ')
        ) -}}
    {%- endif -%}
    {{- mapping[logical_type] -}}
{%- endmacro -%}


{# ---------------------------------------------------------------------------
   Non-throwing cast. Returns null instead of erroring on bad input, which
   matters because the raw zone is all STRING and DataSF ships malformed
   values. BigQuery spells this safe_cast; DuckDB spells it try_cast.
   --------------------------------------------------------------------------- #}

{%- macro x_safe_cast(column_expression, logical_type) -%}
    {{- adapter.dispatch('x_safe_cast', 'sf_data_warehouse')(column_expression, logical_type) -}}
{%- endmacro -%}


{#-
    Each branch calls its OWN type macro (default__x_type, bigquery__x_type)
    rather than the dispatching x_type. This matters: x_type dispatches on the
    adapter that is currently connected, so if bigquery__x_safe_cast called
    x_type while running on DuckDB it would emit safe_cast(x as double), which
    is a BigQuery function with a DuckDB type name and is valid on neither.
    In normal use the two always agree, so the bug only appears when a branch
    is invoked deliberately, which is exactly what makes it worth preventing.
-#}

{%- macro default__x_safe_cast(column_expression, logical_type) -%}
    {{- 'try_cast(' ~ column_expression ~ ' as ' ~ default__x_type(logical_type) ~ ')' -}}
{%- endmacro -%}


{%- macro bigquery__x_safe_cast(column_expression, logical_type) -%}
    {{- 'safe_cast(' ~ column_expression ~ ' as ' ~ bigquery__x_type(logical_type) ~ ')' -}}
{%- endmacro -%}


{# ---------------------------------------------------------------------------
   Throwing cast, for values we assert are well formed. Same spelling on both
   engines today, wrapped anyway so the logical type mapping is used
   consistently and so a future engine has somewhere to hook in.
   --------------------------------------------------------------------------- #}

{%- macro x_cast(column_expression, logical_type) -%}
    {{- 'cast(' ~ column_expression ~ ' as ' ~ x_type(logical_type) ~ ')' -}}
{%- endmacro -%}


{# ---------------------------------------------------------------------------
   Integers that DataSF publishes with a decimal tail. supervisor_district
   arrives as "9.00000" on 311 and "9" on permits; proposed_units arrives as
   "14.0". A direct cast to int nulls the first form on both engines, which
   looks like missing data rather than a formatting difference, so route
   through float first.

   Not dispatched: it composes x_safe_cast, which already is.

   The trunc() is load bearing and was added by PLAN-4 step 3, which ran the
   BigQuery target for the first time and compared it against DuckDB row for
   row. This macro used to be a float cast followed by an int cast, on the
   assumption recorded here that the values are integral in practice. One is
   not: building permit 1752022162216 reports "2.5" stories, and a float to int
   cast rounds it on BigQuery and truncates it on DuckDB, so the same model
   returned 3 on one engine and 2 on the other. Neither engine was wrong; the
   macro had simply never said which it wanted.

   trunc() means "drop the fractional part", exists under that name on both
   engines, and keeps the answer DuckDB was already giving, so no published
   number changes. Note it is truncation and not floor: they differ for
   negative inputs, and nothing here is negative today. A column where the
   difference would matter wants a real decimal, not this macro.
   --------------------------------------------------------------------------- #}

{%- macro x_safe_int(column_expression) -%}
    {{- x_safe_cast('trunc(' ~ x_safe_cast(column_expression, 'float') ~ ')', 'int') -}}
{%- endmacro -%}


{# ---------------------------------------------------------------------------
   Scalar extraction from a JSON string. Socrata sends nested values (point
   geometries, media_url) as objects and normalize_record stores them as JSON
   text, so pulling a field out is a staging concern on every dataset that has
   coordinates but no flat lat/long columns.

   BigQuery spells it json_value; DuckDB spells it json_extract_string. Both
   take the same '$.a.b[0]' path syntax and both return NULL rather than
   erroring on a malformed document.
   --------------------------------------------------------------------------- #}

{%- macro x_json_extract_scalar(column_expression, json_path) -%}
    {{- adapter.dispatch('x_json_extract_scalar', 'sf_data_warehouse')(column_expression, json_path) -}}
{%- endmacro -%}


{%- macro default__x_json_extract_scalar(column_expression, json_path) -%}
    {{- "json_extract_string(" ~ column_expression ~ ", '" ~ json_path ~ "')" -}}
{%- endmacro -%}


{%- macro bigquery__x_json_extract_scalar(column_expression, json_path) -%}
    {{- "json_value(" ~ column_expression ~ ", '" ~ json_path ~ "')" -}}
{%- endmacro -%}


{# ---------------------------------------------------------------------------
   Elapsed hours between two timestamps, as a float.

   Two differences, not one. The obvious one is the spelling and the reversed
   argument order: DuckDB is date_diff(part, start, end), BigQuery is
   timestamp_diff(end, start, part). The subtle one is that asking either
   engine directly for 'hour' counts boundary crossings rather than elapsed
   time, so 10:59 to 11:01 is 1 hour on DuckDB and 0 on BigQuery. Both
   branches therefore ask for seconds and divide, which makes the two agree
   and gives a fractional answer that is more useful in a freshness view than
   a truncated one.
   --------------------------------------------------------------------------- #}

{%- macro x_hours_between(start_timestamp, end_timestamp) -%}
    {{- adapter.dispatch('x_hours_between', 'sf_data_warehouse')(start_timestamp, end_timestamp) -}}
{%- endmacro -%}


{%- macro default__x_hours_between(start_timestamp, end_timestamp) -%}
    {{- "date_diff('second', " ~ start_timestamp ~ ", " ~ end_timestamp ~ ") / 3600.0" -}}
{%- endmacro -%}


{%- macro bigquery__x_hours_between(start_timestamp, end_timestamp) -%}
    {{- 'timestamp_diff(' ~ end_timestamp ~ ', ' ~ start_timestamp ~ ', second) / 3600.0' -}}
{%- endmacro -%}


{# ---------------------------------------------------------------------------
   Now, as a value that is comparable with the timestamps in this project.

   Use this rather than dbt.current_timestamp() or a bare current_timestamp.
   Everything ingestion writes is UTC, and x_safe_cast(col, 'timestamp')
   yields a naive UTC timestamp on DuckDB because casting an offset-bearing
   string to TIMESTAMP there keeps the UTC wall clock and drops the offset.
   DuckDB's now() is a TIMESTAMP WITH TIME ZONE, so subtracting one from the
   other converts to LOCAL time and silently shifts the answer by the
   machine's UTC offset: measured at -6.8 hours on a laptop in PDT, which in
   a freshness view reads as data arriving from the future rather than as an
   error. `at time zone 'UTC'` returns it to a naive UTC timestamp.

   BigQuery has no such trap, because its TIMESTAMP is an absolute instant
   and parsing an offset-bearing string respects the offset, so plain
   current_timestamp() is already comparable.
   --------------------------------------------------------------------------- #}

{%- macro x_utc_now() -%}
    {{- adapter.dispatch('x_utc_now', 'sf_data_warehouse')() -}}
{%- endmacro -%}


{%- macro default__x_utc_now() -%}
    {{- "now() at time zone 'UTC'" -}}
{%- endmacro -%}


{%- macro bigquery__x_utc_now() -%}
    {{- 'current_timestamp()' -}}
{%- endmacro -%}


{# ---------------------------------------------------------------------------
   First day of the month containing a timestamp, as a DATE.

   Three differences at once, which is why this is a macro rather than a
   convention. DuckDB is date_trunc('month', x) with the part as a quoted
   string first; BigQuery is date_trunc(x, MONTH) with the part as a bare
   keyword second. And BigQuery's DATE_TRUNC over a TIMESTAMP returns a
   TIMESTAMP interpreted in UTC, while DuckDB's returns a TIMESTAMP, so the
   two would agree on the instant but disagree on the column type, and a mart
   grouped by it would come out with a different schema on each engine.

   Casting to DATE first settles all three: both engines then truncate a DATE
   and return a DATE. Every month bucket in the marts goes through this, so
   that "2026-07" means one thing warehouse-wide.
   --------------------------------------------------------------------------- #}

{%- macro x_month_start(timestamp_expression) -%}
    {{- adapter.dispatch('x_month_start', 'sf_data_warehouse')(timestamp_expression) -}}
{%- endmacro -%}


{%- macro default__x_month_start(timestamp_expression) -%}
    {{- "date_trunc('month', " ~ x_safe_cast(timestamp_expression, 'date') ~ ')' -}}
{%- endmacro -%}


{%- macro bigquery__x_month_start(timestamp_expression) -%}
    {{- 'date_trunc(' ~ x_safe_cast(timestamp_expression, 'date') ~ ', month)' -}}
{%- endmacro -%}


{# ---------------------------------------------------------------------------
   Division that yields null rather than erroring or infinity when the
   denominator is zero.

   Every normalised measure in the marts is a rate over a population, an area
   or a count, and all three are legitimately zero: a cell over the bay has no
   residents, and dividing 40 street-cleaning requests by them should say
   "not applicable" rather than "infinity" or "divide by zero".

   Not dispatched. nullif is standard on both engines and the point of
   wrapping it is that a rate is never written as a bare `/` anywhere in this
   project, so nobody has to remember which denominators can be zero.
   --------------------------------------------------------------------------- #}

{%- macro x_safe_divide(numerator, denominator) -%}
    {{- '(' ~ numerator ~ ') / nullif(' ~ denominator ~ ', 0)' -}}
{%- endmacro -%}
