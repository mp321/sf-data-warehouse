# Marts layer

Analysis tables, meant to be queried directly. Everything below staging and
intermediate lives here.

## What is here

| Model | Grain | What it holds |
|---|---|---|
| `dim_neighborhood` | one row per analysis neighborhood (41) | Area, population, housing units and business count per neighborhood. The denominator table for every rate below. |
| `dim_supervisor_district` | one row per district (11) | The same measures on 2022 district boundaries. |
| `mart_activity_by_h3` | one row per cell, dataset, category and month | Event counts per H3 cell at resolution 8, with rates per 1000 residents, per 1000 housing units and per sq km. 143k rows, the largest published table. |
| `mart_activity_by_neighborhood` | one row per neighborhood, dataset, category and month | Event counts per neighborhood, with rates per 1000 residents, per 1000 housing units, per 1000 businesses and per sq km. |
| `mart_film_locations` | one row per shoot location | Film shoot locations with their neighborhood, district and H3 cells. |
| `mart_pipeline_freshness` | one row per source | Pipeline health: row counts, load times, staleness flags and coordinate drop rates. Describes the pipeline, not the city. |

Every model here is keyed by place. A neighborhood, a supervisor district or an
H3 cell is on every row.

## Adding a model

1. Select from staging and intermediate models with `{{ ref('...') }}`, never
   from a raw source. `mart_pipeline_freshness` is the exception and its header
   says why.
2. One model per file, named after the file.
3. Add it to `_marts__models.yml` with a description opening "one row per ...",
   and at least one test.
4. **If the model counts events, give it a rate as well.** A count per
   neighborhood tracks population closely enough that ranking by it mostly
   ranks by population: 311 cases put Mission first by count and Golden Gate
   Park first per resident, on 458 residents. Both marts above follow this rule.
5. **No geometry (ADR-6).** No `ST_` function and no spatial extension. The
   neighborhood on a row is a precomputed column and a cell match is an integer
   equality. If you want `ST_Contains`, the column you need is already in
   `stg_spatial__point_geography`.

Build and test one model while iterating with
`dbt build --select <model_name>`.

## Choosing a denominator

`mart_activity_by_neighborhood` carries four rate columns. They rank
neighborhoods differently, so pick the one that matches the question and name
it in the output.

| Column | Use it for |
|---|---|
| `events_per_1000_residents` | The default. Volume relative to how many people live there. |
| `events_per_1000_housing_units` | Less sensitive to household size, which varies widely across the city. |
| `events_per_1000_businesses` | Anything commercial. The Financial District has few residents and heavy daytime activity, so its per-capita rate is misleading. |
| `events_per_sq_km` | Density with no human denominator. |

`mart_activity_by_h3` carries the first, second and fourth. There is no
per-cell business count, so it has no business rate.

Rates per parcel and per street mile are not available: neither dataset is in
scope. ADR-7 records the gap.
