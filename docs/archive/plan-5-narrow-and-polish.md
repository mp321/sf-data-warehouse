---
status: done
date: 2026-07-31
related: [plan-2-ingestion-lint, plan-4-cloud-first-storage, plan-7-pipeline-assurance, adr-5-h3-computation, adr-7-dataset-scope-2, adr-8-published-exports, adr-10-narrowed-scope, adr-11-derived-zone-code-stamp, adr-12-published-export-layout]
---

# PLAN-5. Narrow the project to seven datasets and cover the Python

## Goal

Seven datasets, two H3 resolutions, one dataset registry rather than two, and
`pytest` coverage on the geometry code. `make check` still passes and the
rebuild-from-zone proof still holds.

**Closed 2026-08-05.** The narrowing landed on 2026-08-04 and is recorded in
ADR-10; the Python coverage and the split landed with it; the registry, the
rename and the run-results retention landed on 2026-08-05, and steps 9 and 12
later the same day as ADR-11 and ADR-12. Step 13, the final sweep, closed it.

Ten of the eleven Done-when boxes were met. The eleventh, "`spatial.py` is
three files, each under about 350 lines", was **resolved by judgement rather
than met**, which step 6 anticipated and delegated to step 13. It is five files
and two of them are over 350. The box and the decision are at the bottom of
this file; the short version is that the line count was a proxy for "one
subject per file, and a direct test on the risky parts", that condition holds,
and reaching 350 would have meant splitting the pip-sample oracle away from the
membership computation it exists to measure.

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
9. ~~**Make `spatial.py` incremental.**~~ **Done 2026-08-05, and recorded in
   ADR-11.** The stamp is a hash of the source of every module that decides the
   zone, not a constant someone bumps, because the costs are asymmetric: a hash
   fires on a comment change and costs one 23 second rebuild, a constant fires
   when someone remembers and fails silently when they do not. `check_derived.py`
   grades it as a third verdict, `RECODED`, exit 4. Incrementality is keyed on
   per-partition row and file counts in the manifest. Verified against the
   plan's own done-when and against the harder half: an incremental run and a
   full rebuild over the same raw zone agree row for row on all six tables, not
   only in count. The step's own premise turned out to be stale and the
   measurement is in ADR-11: the H3 cells it names are 0.95 seconds of a 24
   second run and the oracle sample is 18.86, so what was made cheap is not
   quite what this step expected to make cheap.

   The original text of this step follows, because it is what the work was
   commissioned against.

   Key it on unprocessed `ingest_date`
   partitions, with a code-version stamp that forces a full recompute when
   `spatial.py` itself changes. Roughly 40 seconds per 700k points today,
   linear, on every scheduled build. The derived zone must stay a pure
   function of the raw zone plus the code, so the stamp is not optional.

   **The stamp is a correctness guard, not only an incrementality trigger.**
   Amended 2026-08-05 on evidence rather than on reflection. The bucket's
   derived zone was found carrying H3 r9 cells that ADR-10 removed from the
   code the day before, and nothing in the project detected it: `make check`
   is DuckDB-only and local-only, and `check_derived.py` compares row counts,
   which agreed, because the raw zone had not moved and only the code had.
   It surfaced as an `accepted_values` failure in `make build-bigquery`,
   several steps downstream of the cause. So the stamp has to be readable
   from the zone without running `spatial.py`, so that a checker can say
   "this zone was built by code that no longer exists" and not only "this
   zone is behind". See the "make build-bigquery is red" section of
   `docs/dev-notes/2026-08-05.md`, and PLAN-7 step 1, which is where that
   checker might belong.

   **Amended again the same day, by the session that went to fix it.** The r9
   cells were already gone: the bucket's derived zone had been rebuilt at
   02:20 UTC and the `accepted_values` test passes untouched. That could not
   be established from the zone. It took GCS object mtimes plus the
   observation that the bucket's cell counts match the local zone's exactly,
   3,516 at r8 and 80,780 at r10, which is forensics and not a check. So the
   stamp answers a second question besides the one above: with it, "this zone
   is correct now" and "this zone was never wrong" are distinguishable states
   and a fix is attributable to a run. Without it they are one observation,
   and so are "someone rebuilt the zone" and "someone widened the test".

   **PLAN-7 step 2 does not cover this, now that it exists.**
   `parity-check.py --columns` compares column sets, and r9 against r8 and r10
   is a change in the values of an unchanged `resolution` column, so it passes
   on exactly the zone that produced the failure. The two are neighbours and
   neither subsumes the other. On the open question of where the checker
   belongs, `check_derived.py` now looks like the better answer than PLAN-7
   step 1: it already reads the zone rather than the warehouse, already parses
   the manifest the stamp would live in, and already returns two graded
   verdicts, STALE and DRIFT, that a third would join rather than complicate.
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
12. ~~**Cut the published object count, or accept it in writing.**~~ **Done
    2026-08-05, and recorded in ADR-12.** One file per mart, the third of the
    three options. One publish is 7 objects and 3.0 MB, from 2,280 and 16 MB.
    The deciding measurement was not the object count, which every option
    improves, but the byte count: month partitioning cost 5.8x the bytes of the
    same data, because the median partition held 40 rows and a 5 KB Parquet file
    is mostly footer. A layout worse on every axis is not a tradeoff. Flooring
    the date range was rejected rather than deferred: 65.6% of
    `mart_activity_by_h3`'s rows predate 2020, so it is a scope cut wearing a
    partitioning fix's clothes. `MANIFEST_VERSION` is 2 and ADR-8 carries a
    pointer. Two numbers in the original text below were measured smaller on the
    current zone, 2,280 objects rather than 2,885 and a range starting in 1849
    rather than 1967; the argument was unaffected.

    The original text of this step follows.

    PLAN-4
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
13. ~~**Final sweep for things this plan made obsolete.**~~ **Done 2026-08-05,
    and it closed this plan.** The finding worth carrying forward is where the
    staleness was, because it was not where this step expected it: **the code
    was clean and the documentation about the code was not.**

    Every module docstring, every Makefile target comment and every dbt model
    header already described the code as it is. That is not luck. Each of steps
    1 to 12 updated the header next to the code it changed, in the same change,
    so there was nothing left to sweep. What had drifted was the three
    documents that describe the project from outside it and that no single step
    owned: `USER-NOTES.md` still listed nine datasets, a budget mart and two
    dataset registries; `SETUP.md` described a two-layer dbt project loading
    four tables into BigQuery, with no spatial step in it at all; and
    `_spatial__sources.yml` still said three H3 resolutions.

    **The rule that follows from that, and it is the reusable part:** a fact
    written next to the code it describes gets corrected by whoever changes the
    code, and a fact written in a document about the code does not. That is the
    same argument this plan's step 4 made about the dataset registry, arriving
    a second time in a different shape. Put context at the top of the file or
    function it concerns; a document that restates it will be the copy that
    rots.

    Deletions: the six archived session prompts in `docs/handoff-prompt.md`,
    748 lines down to about 200, since what each session did is in the dev note
    for its date and what it decided is in the plan step or ADR it produced; and
    the "Review workflow with Claude" section of `dbt/models/marts/README.md`,
    which was generic advice carrying nothing about this repo.
    `docs/review-2026-07-31-scope-and-cloud.md` is kept, because
    `docs/README.md` conditions its deletion on PLAN-6 and PLAN-6 has not
    started, but it now carries a table at the top saying where each of its
    recommendations landed, so a reader cannot mistake it for current.

    The original text of this step follows.

    Deliberately last, and
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
- [x] ~~`spatial.py` is three files, each under about 350 lines.~~
      **Resolved by judgement in step 13 rather than met by line count, and
      the decision is not to split further.** Read this before "fixing" the
      numbers.

      Step 6 left the box open and step 13 was named as the place to settle
      it. The state today is five files rather than three: `spatial.py` 521,
      `boundaries.py` 503, `derived_state.py` 364, `h3_points.py` 235,
      `population.py` 88. Two of the five are over 350 and both grew after
      step 6, `spatial.py` and `derived_state.py` under step 9.

      The decision, with the argument, because the next reader will otherwise
      re-open it:

      **`boundaries.py` stays whole.** Splitting the pip-sample oracle out is
      what would fix the number, and it is the one split that should not
      happen: the oracle exists to measure the error in the membership
      computed a few functions above it, and putting a check in a different
      file from the thing it checks is how a check stops being read alongside
      it. Its four tables are one scheme, ADR-6's covering cells plus exact
      refinement plus the measurement of what that costs.

      **The line count was a proxy and the thing it proxied for is fixed.**
      This box was written when `spatial.py` was 942 lines, held every derived
      table, and had no direct test anywhere. Today every file has one subject
      named in its header, `geometry.py` has 95 direct pytest cases and
      `derived_state.py` has its own, and the largest file is 521. Moving code
      into a file where it fits less well to reach 350 would buy the number and
      sell the reason for it.

      What the box should have asked for, for anyone reusing this plan's shape:
      one subject per file and a direct test on the risky parts, not a line
      count.
- [x] `ingestion/datasets.py` no longer exists under that name, and PLAN-2 is
      closed. Done 2026-08-05.
- [x] A second `make spatial` on an unchanged zone does substantially less
      work than the first. Done 2026-08-05: 23.2 seconds becomes 0.3, and the
      six Parquet files are byte-identical afterwards rather than rewritten.
      The correctness half was checked separately and harder than this box
      asks: an incremental run over a raw zone with a new daily partition, and
      a `make clean-derived && make spatial` over the same zone, agree row for
      row on all six tables. Not only in count, and not only on the point
      tables. `derived_h3_population` is the one table compared with a
      tolerance, and the reason is in ADR-11: two full builds of an identical
      zone differ by 4.55e-13 residents on a few cells, so an exact comparison
      there would fail on the full path too.
- [x] ADR-10 written, ADR-7 superseded, ADR-5 amended, CLAUDE.md and the
      READMEs updated. Done 2026-08-04. The original wording of this box said
      ADR-5 was to be superseded; see step 10 for why it was not.
- [x] One publish is under 200 objects, or ADR-8 carries a written decision to
      live with the count. Done 2026-08-05: 7 objects and 3.0 MB, from 2,280
      and 16 MB. ADR-12 carries the decision and ADR-8 carries a pointer to it.
      A daily publish is now 210 Class A operations a month against a free tier
      of 5,000, so the quota reason for publishing by hand is gone; whether it
      goes on a cron is deliberately left open.
- [x] Nothing in the repo describes nine datasets, three resolutions, a budget
      mart or a tree count. Done 2026-08-05 under step 13. Three live
      statements were still wrong and all three were in documentation rather
      than in code: USER-NOTES.md carried the nine-dataset table, a budget
      mart and the dual registry; SETUP.md described a two-layer dbt project
      loading four tables into BigQuery and no spatial step at all; and
      `_spatial__sources.yml` still described `derived_point_h3` as carrying
      cells "at resolutions 8, 9 and 10".

      The ADRs, the dev notes, the plans and the 2026-07-31 review still say
      nine datasets and three resolutions, and are supposed to. They are the
      record of what was believed and when, which `docs/README.md` protects
      deliberately, and an ADR cannot be edited to agree with the present
      anyway. The box is about what describes the repo as it is.

### Re-verified on 2026-08-05 at close, rather than trusted

Every box above was checked against the repo again before this plan was closed,
because a box ticked on the day the work landed is a claim about that day. The
numbers that moved since are recorded here rather than edited into the boxes.

| Claim | Then | At close |
|---|---|---|
| Datasets in the registry | 7 | 7 |
| `make ci-build`, both passes | `PASS=170 ERROR=0` | `PASS=171 ERROR=0`, 19 models, 148 data tests, 4 hooks |
| `RESOLUTIONS` | `(8, 10)` | `(8, 10)`, and `make check-derived` reports the zone current with the raw zone and the code |
| `make test-python` | 95 cases | 127 passed in 0.12s |
| Second `make spatial`, unchanged zone | 23.2s to 0.3s | 0.27s, nothing rebuilt |
| One publish | 7 objects, 3.0 MB | 7 objects, 3.0 MB |

The test count moved because steps 5 and 9 both added files, not because
anything was re-counted: 95 geometry cases plus the registry and
`derived_state` suites.

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
