# Marts layer: yours to build

This folder is intentionally almost empty. The marts are where you write
SQL by hand, since interview-ready SQL is a goal of this project. Write
each model yourself, then paste it to Claude for review before merging.

The one model already here, `mart_pipeline_freshness`, is not part of that
exercise. It describes the pipeline rather than the city, which is why it is
`mart_` prefixed rather than `fct_`, `dim_` or `agg_`, and why it is the sole
exception to rule 1 below. Its header says why it breaks the rule. Read it as
infrastructure, not as an example to copy.

## Rules of the road

1. Marts select only from staging models via `{{ ref('...') }}`, never
   from raw sources.
2. One model per file; the filename is the model name.
3. After writing a model, add it to a `_marts__models.yml` with a
   description and at least one test, mirroring how the staging layer
   documents `stg_datasf__311_cases`.
4. Run `dbt build --select <model_name>` to build and test just your new
   model while iterating.

## Suggested build order

Work through these one at a time. Each teaches a different SQL muscle.

1. `fct_311_cases`: one row per case with a computed
   `resolution_time_hours` (timestamp_diff between opened and closed) and
   an `is_open` flag. Practices: date math, case expressions.
2. `agg_311_daily`: cases opened per day per service_category and
   supervisor_district. Practices: group by, count(distinct), date
   truncation.
3. `stg_datasf__film_locations` first (staging), then
   `fct_film_locations`: one row per movie per location, with a count of
   locations per title. Practices: window functions over a fun dataset.
4. `stg_datasf__building_permits`, then `agg_permits_monthly`: permits
   filed and estimated cost by month and neighborhood. Practices:
   handling messy numeric strings, safe_cast, nullif.
5. `stg_datasf__city_budget`, then `agg_budget_by_department_year`:
   year-over-year budget change per department. Practices: lag window
   function, percent change.
6. Stretch: join 311 volume against budget by department or district and
   see if spending tracks demand. This is the portfolio headline query.

## Review workflow with Claude

For each model, share: the SQL, what you expected it to do, and the
output of `dbt build --select <model>`. Ask for a review covering
correctness, style, and performance. Rewrite it yourself based on
feedback rather than pasting generated SQL back in.
