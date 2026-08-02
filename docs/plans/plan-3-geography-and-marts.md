---
status: done
date: 2026-07-31
related: [adr-5-h3-computation, adr-6-polygon-membership, adr-7-dataset-scope-2, adr-8-published-exports]
---

# PLAN-3. Give the warehouse its geography, and publish query-ready marts

## Goal

"Count 311 cases inside this neighborhood" resolves through H3 integer
predicates with no geometry engine at query time, and the marts that answer it
are exported as Parquet anyone can read.

## Why now

ADR-2 chose H3 and nothing implemented it. The warehouse carried `latitude`
and `longitude` floats and an upstream `supervisor_district` string that ADR-2
had already documented as unusable, so no spatial question could be answered
honestly. Meanwhile ADR-3 blocked adding the boundary datasets until a mart
existed, and the mart could not exist without boundaries: a deadlock that had
to be resolved by an ADR before any code could be written.

## Steps

1. Add the geography sources to `ingestion/datasets.py` and model them through
   staging: registered business locations, analysis neighborhoods, supervisor
   districts, street trees, plus census block group population as the
   denominator. (Done. Nine datasets, three tiers, ADR-7.)
2. Geometry handling. Parse and judge point coordinates, record the drop rate
   in the freshness mart, store polygons as GeoJSON alongside precomputed
   covering H3 cells. (Done, ADR-6.)
3. H3 at resolutions 8, 9 and 10 on every point table. (Done, ADR-5. Computed
   in Python rather than by the DuckDB extension, because BigQuery has no H3
   support of any kind and ADR-1 requires both targets to compile.)
4. Marts: `mart_activity_by_h3`, `mart_activity_by_neighborhood`,
   `mart_film_locations`, and one non-spatial budget mart. Every count mart
   exposes a normalised companion. (Done.)
5. Publish marts to partitioned Parquet with a manifest, local-first. (Done,
   ADR-8.)

## Constraints inherited

- Every model compiles on both engines (ADR-1). This is what ruled out the
  DuckDB h3 extension and forced the precompute step.
- Raw stays raw and append-only (ADR-1, ADR-4). This is what put the H3 cells
  in a separate derived zone rather than in columns on the raw tables.
- No credentials on the ingestion path (ADR-1). This is what ruled out the
  ACS 5-year API, which now requires a key.

## Done when

- [x] A neighborhood count resolves with no geometry operator in the query
      plan. Verified by reading the DuckDB `EXPLAIN` output.
- [x] Boundary membership is exact, not approximate, and a test says so with
      no threshold to relax.
- [x] Every count mart has a normalised companion measure.
- [x] `make publish` produces Parquet and a manifest with no bucket.
- [x] `make check` passes: 196 dbt tests, ruff, sqlfluff, leak check, the
      BigQuery compile, and the full fixture pipeline including the
      drop-and-rebuild.

## Open questions carried forward

- The remote half of `make publish` has never run against a real bucket, so
  R2 and GCS uploads are code that has never been executed. It is the same
  shape of gap as the BigQuery target, and it should not be described as
  working until someone runs it.
- Resolution is chosen once, at 10, for every boundary set. The measurements
  in ADR-6 show block groups need a finer one and supervisor districts would
  be fine with a coarser one. Per-set resolution is a real improvement and was
  not attempted.
- Rates per parcel and per street mile were asked for and are not possible
  without ingesting a parcel or street centreline dataset. ADR-7 records it.
