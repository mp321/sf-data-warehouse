{#
    unique_combination: fail when a set of columns is not unique together.

    A grain test for models whose grain is more than one column, which is most
    of the marts. Written here rather than concatenating the columns into
    `unique`'s `column_name`, which is what this project did until PLAN-4 step 3
    ran the BigQuery target for the first time and found three of them broken.

    Why concatenation was the wrong shape, in the order the problems bite:

      1. It needs a cast for every non-string column, and `varchar` is DuckDB
         only. BigQuery spells it `string`, so `cast(event_month as varchar)`
         is a dialect leak in a place nothing was looking: a yml test, not a
         model. `dbt compile --target bigquery` cannot catch it, because
         compiling renders Jinja and never asks the warehouse whether the type
         exists.
      2. Routing the cast through `x_type` would fix the dialect and break the
         node name, because dbt derives a test's name from the rendered
         expression. The same test would be `..._as_varchar_` on one target and
         `..._as_string_` on the other, so `meta_dbt_run_results` and the
         committed manifest would disagree about which tests exist.
      3. `a || '|' || b` is ambiguous anyway. Any value containing the
         separator lets two distinct rows produce one key, so the test passes
         where it should fail. A group by cannot have that bug.
      4. Nulls. Concatenation with a null is null on both engines, so a row
         with a null grain column silently drops out of the test.

    A group by with `having count(*) > 1` has none of those properties: no
    cast, no separator, no null collapse, and a name that does not depend on
    the target.

    Nulls are grouped rather than ignored, which is deliberate and stricter
    than concatenation was: two rows that are null in the same grain column and
    equal elsewhere are a duplicate, and the model's own `not_null` tests are
    what say whether the null should have been there at all.

    Usage:

      tests:
        - unique_combination:
            arguments:
              columns: [h3_cell, dataset, category, event_month]
#}

{% test unique_combination(model, columns, where=none) %}

{%- if columns is string or columns | length == 0 -%}
    {{ exceptions.raise_compiler_error(
        "unique_combination: `columns` must be a list of at least one column name, got "
        ~ columns
    ) }}
{%- endif -%}

{%- set column_list = columns | join(', ') -%}

select {{ column_list }}, count(*) as duplicate_row_count
from {{ model }}
{%- if where %}
where {{ where }}
{%- endif %}
group by {{ column_list }}
having count(*) > 1

{% endtest %}
