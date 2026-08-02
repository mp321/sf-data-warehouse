{#
    accepted_range: fail rows whose value falls outside [min_value, max_value].

    Written here rather than taken from dbt_utils on purpose. Installing a
    package means `dbt deps` has to reach the network before anything can
    build, and ADR-1's whole point is that a fresh clone builds with no
    network and no credentials. One generic test is a smaller price than a
    package dependency on the critical path of `make setup`.

    Null passes, matching every built-in dbt test. A latitude that is absent
    is `not_null`'s problem; a latitude of 5999163 is this test's problem.
    Conflating the two would mean a source with no coordinates at all could
    never satisfy both.

    Bounds are inclusive. Both are optional, so this doubles as a one-sided
    test: `min_value: 0` alone asserts non-negative.

    Usage, with `arguments:` because dbt deprecated bare generic-test args:

      - name: latitude
        tests:
          - accepted_range:
              arguments:
                min_value: 37.60
                max_value: 37.93
#}

{% test accepted_range(model, column_name, min_value=none, max_value=none, where=none) %}

{%- if min_value is none and max_value is none -%}
    {{ exceptions.raise_compiler_error(
        "accepted_range on " ~ model ~ "." ~ column_name
        ~ " sets neither min_value nor max_value, so it can never fail."
    ) }}
{%- endif -%}

select
    {{ column_name }} as out_of_range_value,
    count(*) as row_count

from {{ model }}

where {{ column_name }} is not null
    {%- if where %}
    and ({{ where }})
    {%- endif %}
    and (
        false
        {%- if min_value is not none %}
        or {{ column_name }} < {{ min_value }}
        {%- endif %}
        {%- if max_value is not none %}
        or {{ column_name }} > {{ max_value }}
        {%- endif %}
    )

group by {{ column_name }}

{% endtest %}
