# Marts layer

Analysis tables, meant to be queried directly. Everything below staging and
intermediate is here.

## What is here now

| Model | Grain | What it is for |
|---|---|---|
| `dim_neighborhood` | one row per analysis neighborhood (41) | The denominator table. Area, population, housing units, business count. Read this before trusting any rate. |
| `dim_supervisor_district` | one row per district (11) | The same, on 2022 boundaries. |
| `mart_activity_by_h3` | cell, dataset, category, month | The hexagon map, at resolution 8. The largest published artifact here: 140k rows. |
| `mart_activity_by_neighborhood` | neighborhood, dataset, category, month | "Count 311 cases inside this neighborhood", with four denominators. |
| `mart_film_locations` | one row per shoot location | The demo mart. |
| `mart_pipeline_freshness` | one row per source | Pipeline health, not city data. The one metadata mart. |

Every mart above answers a question about where something is. That is the
claim this project makes rather than an accident of what survived: ADR-10 cut
the one non-spatial mart, and a mart that sat outside the claim diluted it
rather than broadening it.

## Rules of the road

1. Marts select from staging and intermediate models via `{{ ref('...') }}`,
   never from raw sources. `mart_pipeline_freshness` is the exception and its
   header says why.
2. One model per file; the filename is the model name.
3. Add every model to `_marts__models.yml` with a description that opens with
   its grain, stated as "one row per ...", and at least one test.
4. **Every count mart exposes a normalised companion measure.** A raw count
   per neighborhood is close to a map of where people live: ranking 311 cases
   by count puts Mission first, and by rate per resident puts Golden Gate Park
   first, which has 458 residents. Both are true and only one of them is
   interesting. A mart that offers only counts invites the wrong conclusion.
5. **No geometry (ADR-6).** No `ST_` function, no spatial extension. The
   neighborhood on a row is a precomputed column; a cell join is an integer
   equality. If you find yourself wanting `ST_Contains`, the answer is already
   in `stg_spatial__point_geography`.
6. Run `dbt build --select <model_name>` to build and test just your model
   while iterating.

## Choosing a denominator

`mart_activity_by_neighborhood` carries four, and they disagree on purpose.

- **per 1000 residents** is the default. Where is there more of this than the
  number of people explains.
- **per 1000 housing units** is less sensitive to household size, which varies
  a lot across the city.
- **per 1000 businesses** is the right one for anything commercial. The
  Financial District has almost no residents and enormous daytime activity, so
  its per-capita street-cleaning rate is close to meaningless.
- **per sq km** is pure density with no human denominator.

The disagreement is information. Bayview Hunters Point ranks 4th by raw 311
count and 18th per resident; if a chart does not say which it is using, it is
not saying anything.

## Denominators that are not here

The plan that commissioned these asked for rates per parcel and per street
mile. Neither dataset is in scope, and adding one is a scope decision rather
than a modelling one. ADR-7 records the gap and ADR-10 narrowed scope further
rather than widening it, so the gap is wider now than when it was written. Do
not approximate it with something that happens to be available.
