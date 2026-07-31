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
