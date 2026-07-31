---
status: draft
date: 2026-07-31
related: [plan-2-ingestion-lint, plan-4-cloud-first-storage, adr-5-h3-computation, adr-7-dataset-scope-2]
---

# PLAN-5. Narrow the project to seven datasets and cover the Python

## Goal

Seven datasets, two H3 resolutions, one dataset registry rather than two, and
`pytest` coverage on the geometry code. `make check` still passes and the
rebuild-from-zone proof still holds.

## Why now

This repo is read in twenty minutes or it is not read. Nine datasets, 22
models and seven marts is roughly twice what the thesis needs, and the size is
load-bearing in the wrong direction: it makes the good parts, the raw zone
design and the H3 precompute, harder to find.

Separately, the largest and riskiest Python file has no direct tests.
`ingestion/geometry.py` is 280 lines of hand-rolled point-in-polygon and area,
and it is covered only indirectly by assertions inside `spatial.py`. Every dbt
test in the project checks SQL; nothing checks the code most likely to be
subtly wrong.

Sequenced after PLAN-4 because both plans edit `datasets.py`,
`dbt_project.yml` and `publish/export.py`, and because narrowing scope before
the BigQuery parity check would mean proving parity on a smaller surface than
the one that has been running.

## Constraints

- Every model still compiles on both engines (ADR-1).
- The rebuild-from-zone step in CI must still pass. Removing a dataset means
  removing it from the fixtures too, or the fixture pipeline builds a model
  that no longer exists.
- ADRs are immutable. The scope change is recorded by superseding, not
  editing (docs/README.md).
- No commits or pushes by an agent.

## Scope decisions to record in ADR-10

**Cut `city_budget`.** Non-spatial, joins to nothing, and ADR-7 concedes it
exists mainly so that a non-spatial mart exists. Removing it also removes the
only `kind: nonspatial` registry entry, which narrows the project to a purely
spatial one. That is the point rather than a side effect: "count events inside
this boundary with no geometry at query time" is one clear claim, and a budget
mart sitting beside it dilutes rather than broadens.

**Cut `street_trees`, keep `film_locations`.** ADR-7 justifies trees as the H3
stress test. `business_locations` at 365k rows is nearly twice the size, is
already load-bearing as a population-independent denominator, and exercises
exactly the same code path, so the stress test survives the cut.
`film_locations` is 204 KB and 2,214 rows, ingests end to end in seconds, and
is the only dataset small enough to serve as a canary. Cutting the 25 MB
dataset and keeping the 204 KB one is the right way round. This also removes
the larger of the two `geometry: {latitude, longitude}` flat-column datasets,
so verify the flat-column path is still exercised by `film_locations` before
deleting anything.

**Keep 311 at `start_date` 2024-01-01.** Roughly 155 bytes per row as Parquet,
measured from the current zone, so 2024 to date is about 250 to 310 MB against
5 GB of always-free GCS. Full history at 8.8 million rows would be about 1.4
GB and still fits, so widening later is a scope decision and not a storage
one. Do not trim below 2024: the marts are monthly and a window shorter than
that cannot show a seasonal pattern, which is most of what they are for.

**Drop H3 resolution 9.** ADR-5 keeps it explicitly so that ADR-2's original
guess "stays checkable", which is a dev-note reason carrying a permanent
schema cost of a third of the largest derived table. Keep r8 for readable maps
and r10 for membership. Record the r9 measurement in ADR-10 so the guess stays
checkable in prose, where it costs nothing.

## Steps

1. **Cut `city_budget` end to end.** `ingestion/datasets.py`,
   `_datasf__sources.yml`, `_datasf__models.yml`,
   `stg_datasf__city_budget.sql`, `mart_budget_by_department_year.sql`,
   `_marts__models.yml`, `vars.pipeline_sources` in `dbt_project.yml`,
   `PUBLISHED_MARTS` in `publish/export.py`,
   `tests/fixtures/socrata/city_budget.json` and its entries in
   `make_fixtures.py`, and the table in `dbt/models/marts/README.md`.
2. **Cut `street_trees` end to end.** The same file list, plus
   `int_point_activity.sql` and the `accepted_values` test listing its four
   datasets in `_intermediate__models.yml`, plus the `street_tree_count`
   column on `dim_neighborhood.sql` and its description and `not_null` test in
   `_marts__models.yml`.
3. **Drop r9.** `RESOLUTIONS` in `ingestion/spatial.py`. Then check
   `mart_activity_by_h3`, which is documented as resolution 9 and has to move
   to 8 or 10; its description, its grain sentence and its tests move with it.
   Delete and rebuild the derived zone rather than trying to migrate it.
4. **One registry, not two.** `vars.pipeline_sources` in `dbt_project.yml`
   duplicates `ingestion/datasets.py`, and the duplication is documented
   rather than prevented. Either generate the dbt vars from the Python
   registry at build time, or move the shared fields into a YAML both read.
   Whichever is smaller. The test is that the two cannot disagree silently.
5. **`pytest` on `ingestion/geometry.py`.** Cover a point strictly inside, one
   strictly outside, one on a vertex, one on an edge, one inside a hole, a
   degenerate polygon, and a multipolygon. Assert the area calculation against
   a shape with a known answer. Add `pytest` to `requirements-dev.txt`, add a
   `make test-python` target, and wire it into `make check` and `ci.yml`
   before the dbt job, since it is the fastest gate in the set.
6. **Split `spatial.py`.** 883 lines into `h3_points.py`, `boundaries.py` and
   `population.py`, with the module docstring's explanation of the three
   containment modes moving to whichever file uses them. Do this after step 5,
   so the tests are what tell you the split preserved behaviour.
7. **Finish PLAN-2.** Rename `ingestion/datasets.py` to `dataset_registry.py`.
   `known-first-party` silenced the lint without fixing the underlying hazard:
   `from datasets import DATASETS` resolves only because Python puts the
   script's directory on `sys.path`, and it will collide with the HuggingFace
   `datasets` package the moment anything pulls that in. Update the imports,
   the Makefile, `ingest.yml` and CLAUDE.md's directory conventions. Then
   close PLAN-2 as done.
8. **Prune `meta_dbt_run_results`.** One row per node per run, forever, today.
   A rolling window in `audit_run_results.sql`, with the window written into
   the macro's header comment.
9. **Make `spatial.py` incremental.** Key it on unprocessed `ingest_date`
   partitions, with a code-version stamp that forces a full recompute when
   `spatial.py` itself changes. Roughly 40 seconds per 700k points today,
   linear, on every scheduled build. The derived zone must stay a pure
   function of the raw zone plus the code, so the stamp is not optional.
10. **Write ADR-10**, covering the scope cut and the resolution cut together.
    It supersedes ADR-7 and ADR-5. One ADR rather than two: this plan exists
    to reduce the document count, and answering it with four new records would
    defeat it.
11. **Update CLAUDE.md.** The "Current state versus intended state" table, the
    dataset counts, the marts count and the read-first order all describe nine
    datasets and three resolutions.

## Out of scope

- Anything in PLAN-4. Do not start this until PLAN-4's done-when list is
  ticked.
- The context pack. PLAN-6.
- Adding any dataset. Removing two and adding one is not narrowing.

## Done when

- [ ] Seven datasets in the registry, and `make ci-build` passes from fixtures
      with no dangling model or source.
- [ ] `RESOLUTIONS` is `(8, 10)` and the derived zone has been rebuilt.
- [ ] `dbt_project.yml` no longer carries a second copy of the dataset list.
- [ ] `make test-python` runs and CI fails when a geometry test fails.
- [ ] `spatial.py` is three files, each under about 350 lines.
- [ ] `ingestion/datasets.py` no longer exists under that name, and PLAN-2 is
      closed.
- [ ] A second `make spatial` on an unchanged zone does substantially less
      work than the first.
- [ ] ADR-10 written, ADR-5 and ADR-7 superseded, CLAUDE.md updated.

## Open questions

- Does `mart_activity_by_h3` want r8 or r10 now that r9 is gone? r8 is the
  readable map and r10 is the membership resolution, and the mart is currently
  documented as the map. Measure the row count at both before choosing:
  264,802 rows at r9 is already the largest published artifact.
- Is `film_locations` genuinely enough to keep the flat lat/lon code path
  covered, or does cutting `street_trees` leave that branch tested only by a
  2,214-row dataset? If the latter, the fixture for it needs to carry the
  adversarial coordinate cases that trees currently supplies.
