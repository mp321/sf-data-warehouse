---
status: active
date: 2026-07-31
related: [plan-2-ingestion-lint, plan-4-cloud-first-storage, plan-7-pipeline-assurance, adr-5-h3-computation, adr-7-dataset-scope-2, adr-10-narrowed-scope]
---

# PLAN-5. Narrow the project to seven datasets and cover the Python

## Goal

Seven datasets, two H3 resolutions, one dataset registry rather than two, and
`pytest` coverage on the geometry code. `make check` still passes and the
rebuild-from-zone proof still holds.

**Status: steps 1 to 8, 10 and 11 are done as of 2026-08-05.** The narrowing
landed on 2026-08-04 and is recorded in ADR-10; the Python coverage and the
split landed with it; the registry, the rename and the run-results retention
landed on 2026-08-05. What remains is 9 (incremental `spatial.py`), 12 (the
published object count) and 13 (the final sweep).

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

Done steps are left here in full rather than deleted, because a plan is the
record of what was intended and a reader needs to see the whole shape. What
happened is in the dev note for the date each one carries.

1. ~~**Cut `city_budget` end to end.**~~ Done. `ingestion/datasets.py`,
   `_datasf__sources.yml`, `_datasf__models.yml`,
   `stg_datasf__city_budget.sql`, `mart_budget_by_department_year.sql`,
   `_marts__models.yml`, `vars.pipeline_sources` in `dbt_project.yml`,
   `PUBLISHED_MARTS` in `publish/export.py`,
   `tests/fixtures/socrata/city_budget.json` and its entries in
   `make_fixtures.py`, and the table in `dbt/models/marts/README.md`.
2. ~~**Cut `street_trees` end to end.**~~ Done. The same file list, plus
   `int_point_activity.sql` and the `accepted_values` test listing its four
   datasets in `_intermediate__models.yml`, plus the `street_tree_count`
   column on `dim_neighborhood.sql`, plus `STAGING_MODELS` in
   `scripts/parity-check.py`, which this list missed. `street_tree_count` had
   no description or `not_null` test in `_marts__models.yml`; it was an
   undocumented column, which is why this list expected one and found none.
3. ~~**Drop r9.**~~ Done, and it reached further than `RESOLUTIONS` in
   `ingestion/spatial.py`: `h3_r9` was a real column threaded through
   `point_geography.sql`, `stg_spatial__point_geography`,
   `stg_spatial__pip_sample`, `int_point_activity`, `mart_film_locations` and
   four yml files. `mart_activity_by_h3` moved to r8 on the measurement in
   ADR-10. The derived zone was deleted and rebuilt rather than migrated.
4. ~~**One registry, not two.**~~ Done 2026-08-05. The second of the two
   options, the YAML both read, and the choice was forced rather than
   preferred: dbt cannot read an arbitrary YAML file, because its Jinja
   sandbox has no file access and `dbt_project.yml` has no include. Generating
   the vars at build time was the other option and it fails on sqlfluff, which
   templates every model through dbt and has no way to be passed `--vars`; a
   `var('pipeline_sources', [])` default to keep it working is a silent empty
   registry, which is the failure this step exists to remove. So the whole
   registry moved into `vars.pipeline_sources` and
   `ingestion/dataset_registry.py` reads it with PyYAML. The two cannot
   disagree because there are no longer two.
5. ~~**`pytest` on `ingestion/geometry.py`.**~~ Done 2026-08-03.
   `tests/test_geometry.py`, 95 cases, every containment one run through both
   the scalar and the vectorised implementation. The two cases the plan lists
   that have no right answer, a point on an edge and a point on a vertex, are
   asserted as a contract rather than as an output: at most one polygon of a
   non-overlapping set claims such a point, exactly one on an edge interior to
   the covered region, and possibly none on its outer perimeter. Areas are
   asserted against the closed form for a spherical quadrilateral, with the
   arithmetic in the file. `pytest>=8.0` floats, under the rule
   `requirements-dev.txt` already established for sqlfluff. In `ci.yml` it is
   its own job that `dbt-duckdb` declares `needs:`, because the jobs there run
   in parallel and step order inside one job could not have expressed it.
6. ~~**Split `spatial.py`.**~~ Done 2026-08-03. 942 lines, not the 883 this
   step was written against, into `h3_points.py` (192), `boundaries.py` (470),
   `population.py` (88) and a `spatial.py` (284) that keeps the CLI, the Arrow
   schemas, the run order and `raw_input_state`, which `check_derived.py`
   imports. Four files rather than three: the entry point has to stay at
   `ingestion/spatial.py`, which the Makefile, `ci.yml` and `ingest.yml` all
   invoke by path. The three containment modes moved to `boundaries.py`, which
   is the only file that uses them. `boundaries.py` misses the size this plan
   asked for; see the done-when box below.
7. ~~**Finish PLAN-2.**~~ Done 2026-08-05, in the same change as step 4
   because both rewrote the same import sites. `ingestion/datasets.py` is
   `dataset_registry.py`, five importers rather than the three this step was
   written against: the spatial split added `h3_points.py` and
   `boundaries.py`. Two of the files named here did not need touching, which
   is worth recording because the list read as exhaustive: neither the
   Makefile nor `ingest.yml` ever named `datasets.py`. Both invoke
   `ingest.py`, `spatial.py` and `load.py` by path and reach the registry
   through those. What did need it, beyond the imports, was `ruff.toml`'s
   `known-first-party`, CLAUDE.md, README.md and three dbt comments.
   PLAN-2 is closed. The sibling modules on that `known-first-party` list did
   not get the same rename; ruff.toml's comment has the reasoning.
8. ~~**Prune `meta_dbt_run_results`.**~~ Done 2026-08-05. `prune_run_results()`
   in `audit_run_results.sql`, a third `on-run-start` hook, keeping the 50
   most recent invocations and deleting whole runs rather than rows. A run
   count and not an age, because the macro's own header says the mart reports
   the PREVIOUS completed run: an age window can hold zero runs after a quiet
   month, which breaks `mart_pipeline_freshness` rather than pruning it. The
   window is written into the header, and the number lives in the macro rather
   than in a var so that the header and the value cannot drift the way the two
   registries did.
9. **Make `spatial.py` incremental.** Key it on unprocessed `ingest_date`
   partitions, with a code-version stamp that forces a full recompute when
   `spatial.py` itself changes. Roughly 40 seconds per 700k points today,
   linear, on every scheduled build. The derived zone must stay a pure
   function of the raw zone plus the code, so the stamp is not optional.
10. ~~**Write ADR-10**~~, covering the scope cut and the resolution cut
    together. Done on 2026-08-04, out of order: steps 1 to 3 made the
    decisions and started citing the ADR in code comments, and a forward
    reference to a document that does not exist is worse than an early one.
    It supersedes ADR-7. It **amends** ADR-5 rather than superseding it,
    against this plan's original wording: ADR-5's live decision is that H3 is
    computed in Python as BIGINTs, which is still a hard constraint, and
    filing that under history to tidy up a resolution list would be a bad
    trade. One ADR rather than two, as intended.
11. **Update CLAUDE.md and the READMEs.** Largely done on 2026-08-04 alongside
    steps 1 to 3, because leaving them describing nine datasets while the code
    held seven is the failure this plan is trying to fix. What is left is a
    verification pass, folded into step 13.
12. **Cut the published object count, or accept it in writing.** PLAN-4
    residue, homed here because step 3 is what changed the number. One publish
    is 2,885 objects against a free tier of 5,000 Class A operations a month.
    The cause is not the H3 resolution and not the data volume: it is 879
    monthly partitions on `mart_activity_by_h3`, because `business_locations`
    carries `location_started_at` values back to 1967 and the mart partitions
    by month over that whole range. 17 MB takes 6 minutes 39 because the cost
    is per object. Options, in the order they look sensible: partition by year
    (879 objects becomes about 73), or floor the mart's date range, or write
    one file per mart and drop hive partitioning. Whichever is chosen, the
    zero-cost claim in the first paragraph of CLAUDE.md is what it has to
    protect, and ADR-8 needs a note or a successor recording the outcome.
13. **Final sweep for things this plan made obsolete.** Deliberately last, and
    deliberately broad. Read every README, header comment, docstring and ADR
    pointer in the repo and check it against the code as it then is. Delete
    what is dead, correct what is wrong, shorten what is merely long. Two
    rules on what survives: keep a finding, a measurement or a tradeoff that a
    human or an LLM would plausibly look up again, and delete anything that
    only records that work happened, which is what dev notes are for. Put the
    context a reader needs at the top of the file or function it concerns
    rather than in a document about it. `docs/review-2026-07-31-scope-and-cloud.md`
    can be deleted once PLAN-6 is done, per `docs/README.md`; check whether
    PLAN-2 and the `handoff-prompt.md` are still earning their place too.

## Out of scope

- Anything in PLAN-4. Closed 2026-08-04.
- The context pack. PLAN-6.
- Reconciling run manifests, and asserting the BigQuery column sets. Both are
  PLAN-4 residue and both are assurance rather than narrowing. PLAN-7.
- Adding any dataset. Removing two and adding one is not narrowing.

## Done when

- [x] Seven datasets in the registry, and `make ci-build` passes from fixtures
      with no dangling model or source. Done 2026-08-04, `PASS=170 ERROR=0`
      on both passes including the rebuild-from-zone one.
- [x] `RESOLUTIONS` is `(8, 10)` and the derived zone has been rebuilt. Done
      2026-08-04 with `make clean-derived && make spatial`: 506,632 point rows.
- [x] `dbt_project.yml` no longer carries a second copy of the dataset list.
      Done 2026-08-05, by carrying the only copy instead. The direction is the
      opposite of what this box assumed, and step 4 says why it had to be.
- [x] `make test-python` runs and CI fails when a geometry test fails. Done
      2026-08-04. Checked by mutation rather than by inspection: nine changes
      to `geometry.py`, eight of them caught. The ninth, deleting the
      `count < 3` guard in `_ring_contains`, changes no answer for any input,
      because a one or two vertex ring already cancels its own crossings. It is
      a fast path and not a correctness guard, which is worth knowing before
      someone "fixes" a test to cover it.
- [ ] `spatial.py` is three files, each under about 350 lines. **Half done and
      deliberately left half done.** It is four files, for the reason in step 6,
      and three of the four are 88, 192 and 284 lines. `boundaries.py` is 470,
      because it holds four of the six derived tables: the bridge, exact
      membership, the pip-sample oracle and the boundary rows themselves. That
      grouping came from the session prompt and is defensible on cohesion; the
      line count is not what this box was protecting, so it is recorded rather
      than fixed by moving code somewhere it fits less well. Step 13 is the
      right place to decide whether the oracle and the sample want their own
      file.
- [x] `ingestion/datasets.py` no longer exists under that name, and PLAN-2 is
      closed. Done 2026-08-05.
- [ ] A second `make spatial` on an unchanged zone does substantially less
      work than the first.
- [x] ADR-10 written, ADR-7 superseded, ADR-5 amended, CLAUDE.md and the
      READMEs updated. Done 2026-08-04. The original wording of this box said
      ADR-5 was to be superseded; see step 10 for why it was not.
- [ ] One publish is under 200 objects, or ADR-8 carries a written decision to
      live with the count.
- [ ] Nothing in the repo describes nine datasets, three resolutions, a budget
      mart or a tree count.

## Open questions

Both original questions are answered. Kept with their answers, because the
second one was answered by finding the question's premise was false, and that
is worth more than the answer.

- ~~Does `mart_activity_by_h3` want r8 or r10?~~ **r8.** Measured on the real
  zone at the mart's real grain, with `street_trees` already excluded: 140,342
  rows at r8, 238,742 at r9, 330,960 at r10. It is the largest published
  artifact at any resolution, and r8 is the one that is still a map. ADR-10
  carries the table.
- ~~Is `film_locations` enough to keep the flat lat/lon path covered?~~ **The
  question rests on a false premise.** It assumed `street_trees` and
  `film_locations` were the only two datasets with flat `latitude`/`longitude`
  columns. `311_cases` is flat as well, and is the largest dataset in the
  project. The fixtures' adversarial coordinate cases were already split
  across the two coordinate shapes, unparseable on flat `311_cases` and
  out-of-bounds and impossible on GeoJSON `business_locations`; the two
  `street_trees` cases were a 9999 diameter sentinel and a missing plant date,
  neither of them a coordinate. No fixture needed to change.
