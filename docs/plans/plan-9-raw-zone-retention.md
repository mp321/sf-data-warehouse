---
status: done
date: 2026-08-07
related: [adr-4-raw-zone-layout, adr-9-cloud-raw-zone, adr-8-published-exports, adr-12-published-export-layout, adr-14-raw-zone-retention, plan-7-pipeline-assurance]
---

# PLAN-9. Bound what the buckets accumulate

**Status: closed 2026-08-07, all eight steps, recorded in ADR-14 and in the dev
note for the same day.** The bucket went from 563.5 MB over 3,128 objects to
249.6 MB over 196. Both open questions are answered in ADR-14. One measurement
in this plan was wrong and is corrected below: the object count was 236 and not
329.

Two prefixes grow without limit and neither has an owner. The raw zone grows
because it is append-only by decision, and the published prefix grows because
the uploader copies and never deletes. Both are cheap to bound and neither is
bounded.

## Why now

**Measured 2026-08-07, on the real bucket.** The raw prefix is 511.9 MB across
329 objects. The zone went remote on 2026-08-01, so that is six or seven days,
depending on how many of the runs in that window were manual dispatches.

> **Corrected on execution, same day.** 511,937,211 bytes is exact. The object
> count is **236**, not 329, by
> `gcloud storage ls --recursive gs://$GCS_BUCKET/raw/**`. The bytes are what
> the argument rests on and they were right; the count was not, and it is the
> number a reader would have checked first.

| growth | headroom against a 5 GB free allowance |
|---|---|
| 73 MB/day | 61 days |
| 95 MB/day | 47 days |
| 128 MB/day | 35 days |

So the free tier is reached somewhere between mid-September and early October
2026, and the first symptom is a bill rather than a failure. This is the only
item in the project with a date attached to it.

**Operations are not the problem and should not be confused with it.** About 47
objects a day is 1,410 Class A writes a month, plus 210 for the derived zone
rewrites, against a tier of 5,000 and before LIST operations, which are also
Class A. A daily publish would add 210. ADR-12's arithmetic holds; publishing is
about 4 percent of the tier and is not what is filling the bucket.

## What is actually driving it, which is not what it looked like

`ingest.py` is watermark-incremental: it reads the newest
`_socrata_updated_at` already in the zone and asks Socrata for rows after it.
It does not re-fetch a dataset on every run. The full backfill in the registry
is the first load only.

**The growth is upstream republish events on a current-state registry.** The
cron run of 2026-08-06 wrote 415,006 rows of `business_locations`, which is
essentially the whole dataset, because the city bumped `:updated_at` across it.
That writes another full copy, about 95 MB and 16 files, into a zone that never
deletes. It is bursty, it is outside this project's control, and it recurs.

**Those copies are sediment rather than history.** Staging deduplicates by
`grain_key` to the newest `_socrata_updated_at`, so once a later complete
snapshot exists, the rows it supersedes are unreachable through any model. The
zone is paying storage every month for rows no query can return.

**The distinction that has to drive the design.** The seven datasets are two
kinds, and only one is safe to prune:

| kind | datasets | a partition holds | prunable |
|---|---|---|---|
| snapshot | business_locations, film_locations, analysis_neighborhoods, supervisor_districts, census_block_groups | the whole dataset as of that run | yes, when a later complete snapshot exists |
| delta | 311_cases, building_permits | only the rows that changed since the watermark | **never**, deleting one deletes rows |

**This is why a bucket lifecycle rule is the wrong answer**, and it is the
obvious one, so it is worth saying before someone reaches for it. A lifecycle
rule deletes by object age and knows nothing about which partitions are
snapshots. Pointed at this zone it would silently destroy 311 and permit
history, and the loss would surface months later as a hole in a monthly series.

## Steps

1. **Measure the per-partition sizes before changing anything**, since the
   growth rate above is a range and this makes it a number.
   `gcloud storage du gs://$GCS_BUCKET/raw/raw_business_locations` broken out by
   `ingest_date`, and the same for `raw_311_cases`. Two minutes. It also
   confirms the snapshot-versus-delta split against the data rather than against
   this document.
2. **Add the kind to the registry**, as one field in `vars.pipeline_sources` in
   `dbt/dbt_project.yml`, since that is the one copy. Something like
   `refresh: snapshot | delta`. It is already there in prose: the registry
   comment explains that `business_locations` backfills fully because it is a
   current-state registry rather than an event log. This makes the sentence a
   field that code can read and `tests/test_dataset_registry.py` can check.
3. **Write the prune, and make it refuse rather than guess.** A partition may be
   deleted only when a later partition of the same dataset holds every
   `grain_key` it holds. The tool verifies that superset property before
   deleting anything and exits nonzero when it does not hold, on the same
   principle as `check_derived.py`: a check that cannot prove the thing is safe
   says so rather than proceeding. Keep the newest complete snapshot and one
   before it, because one rollback is worth 95 MB.
4. **Prune the run manifests with the partitions, or the pruned zone fails its
   own consistency check.** `check_runs.py` compares each manifest in
   `<table>/_runs/` against the rows carrying its run id, and exits 3
   (MISCOUNTED) when they disagree. Delete a partition and leave its manifest
   and that fires on the next run. Either the prune removes both, or
   `check_runs` learns a third state for a run whose rows were deliberately
   removed. The first is simpler and should be tried first; the second is
   honest about what happened and may be what the check actually wants.
5. **The acceptance test, and it is the whole safety argument in one command.**
   Prune, then `make rebuild`, then compare every model's row count against the
   committed context pack. **If a row count moves, the prune was wrong**, because
   staging was supposed to be deduplicating those rows away already. This is
   also what keeps `context-pack-check` green through the change.
6. **Add `--prune` to `publish/export.py`.** The uploader copies and never
   deletes, which is why the bucket still holds the 2,280 objects of the
   pre-ADR-12 partitioned layout beside the 7 of the current one. Prune deletes
   objects under the prefix that the local export did not just write. Two
   constraints: it runs **after** the manifest lands, so ADR-8's rule that a
   consumer never sees a half-published state survives, and it prints every
   object it removes. Default off, because a flag that deletes remote data is
   not a default, and named in ADR-8's revisit clause as the thing to run when
   `MANIFEST_VERSION` changes.
7. **The one-time cleanup**, by hand and with credentials:
   `gcloud storage rm --recursive` on the published prefix, then a fresh
   `make rebuild` and an upload with `--destination`. Verify 7 objects and
   `manifest_version` 2. Deletes are free operations, so removing the 2,280
   costs nothing against the tier.

   **Done 2026-08-07, and the mechanism was substituted for step 6's.** The end
   state this step names is exactly what `--prune` produces, so it was run as
   `make publish PUBLISH_DEST=gs://.../published PUBLISH_PRUNE=1` rather than as
   a `gcloud storage rm` followed by an upload. Two reasons, and the first is the
   one that matters: `rm --recursive` then upload leaves a window in which the
   destination holds no export at all, which is the state ADR-8's manifest
   ordering exists to prevent, where prune-after-upload never has one. The
   second is that it exercises the flag step 6 had just added against the exact
   condition it was written for, instead of leaving it unrun. Result: 2,880
   objects removed, 7 remain, `manifest_version` 2, 18.6 MB to 3.2 MB. The
   orphans were 2,879 objects of the pre-ADR-12 month-partitioned layout plus
   `mart_budget_by_department_year`, a mart ADR-10 cut, which nothing had
   noticed was still being served.
8. **The ADR.** ADR-4 says the raw zone is append-only, files added and never
   edited or deleted, with `--full-refresh` as the single exception. This plan
   creates a second exception, so it needs an ADR that amends ADR-4 rather than
   an edit to it: ADRs are immutable once accepted. It should record the
   snapshot-versus-delta split as the thing that makes the exception safe, the
   superset check as the mechanism, the lifecycle rule as the rejected option
   and why, and the fact that `make rebuild` still reproduces every model, which
   is ADR-4's actual claim and is what must survive.

## Not doing, and why

- **Changing the ingest cadence.** Bi-weekly runs would cut Class A operations
  by half and would not cut a single byte, because the bytes are decided by how
  often the city republishes and not by how often this project asks. Operations
  are at a third of the tier. The lever is the prune.
- **Compaction.** Rewriting many small Parquet files into fewer larger ones
  would cut object count and help the read path, and it edits the zone in place,
  which is a much larger break of ADR-4 than deleting a superseded snapshot.
  Worth its own decision if the object count ever becomes the binding
  constraint. It is not: 329 objects against no object quota at all.
- **Moving to a cheaper storage class.** Nearline and Coldline have retrieval
  costs and minimum storage durations, and this zone is read by every build. It
  trades a bill you can see for one you cannot.

## Done when

- [x] The bucket's raw prefix is measured per dataset and per partition, and the
      growth rate is a number rather than a range. Done 2026-08-07.
      `raw_business_locations` is 397.1 MB of the 511.9, in seven daily
      partitions of about 49.6 MB, and every one of the six days after the
      backfill wrote a fresh full copy. The other six datasets are 114.8 MB
      between them, of which 55.5 MB is `city_budget` and `street_trees`, which
      ADR-10 cut and nothing has deleted.
- [x] The registry declares each dataset snapshot or delta, and a test fails if
      a new dataset omits it. Done: `refresh` in `vars.pipeline_sources`,
      required by `dataset_registry.load_registry`, three tests in
      `tests/test_dataset_registry.py`.
- [x] A prune exists that refuses to delete a partition it cannot prove is
      superseded, and removes the run manifests with it. Done:
      `ingestion/prune_raw.py`, `make prune-raw` and `make prune-raw-apply`, 14
      tests in `tests/test_prune_raw.py`, four of which are refusals.
- [x] Prune then `make rebuild` leaves every model's row count unchanged, and
      `make context-pack-check` still passes. Done on the real bucket: 54
      objects and 297.8 MB removed, 2,188,619 raw rows of
      `raw_business_locations` gone, **0 of 19 model row counts moved**,
      `dbt build` `PASS=172 ERROR=0`, `make check-runs` still clean, and
      `context-pack-check` agrees with the live target.
- [x] `publish/export.py --prune` removes orphans after the manifest lands, and
      the published prefix in the bucket holds 7 objects at
      `manifest_version` 2. Done: 2,880 objects removed, 18.6 MB to 3.2 MB.
- [x] An ADR amends ADR-4's append-only rule and records why the exception is
      safe for one kind of dataset and unsafe for the other. ADR-14.

## Open questions

- **Where does the prune run?** A make target run by hand is the smallest thing
  that works and matches how `make publish` is operated today. A step at the end
  of `ingest.yml` would keep the zone bounded without anyone remembering, and
  would also mean a scheduled job deletes data, which is a different risk
  appetite. Decide with the ADR.
  **Answered in ADR-14: by hand, `make prune-raw` then `make prune-raw-apply`.**
  The prune's whole design is that a human reads a refusal, and a refusal
  nobody reads is a tool that either wedges a scheduled job or gets its exit
  code ignored. The cost is named rather than argued away: the zone is now
  bounded by someone remembering, and that is what reopens this.
- **Does `check_runs.py` get a third state?** Step 4 offers two answers and the
  cheaper one may be the wrong one. A run whose rows were deliberately removed
  is a fact about the zone, and a check that cannot express it is a check that
  will be silenced later. PLAN-7 step 1 made the same call the other way and its
  reasoning is worth re-reading first.
  **Answered in ADR-14: no third state. The prune deletes the manifests with
  the partitions**, and `check_runs.py` is unchanged. Re-reading PLAN-7 step 1
  is what settled it, and against the cheap answer's favour rather than for it:
  that call kept `check_runs.py` out of `check_derived.py` because the reader
  and the moment were different, and here they are the same. The deciding cost
  is that a third state needs a marker, and the only places to put one are
  inside a manifest, which means editing a file in the zone and is a larger
  break of ADR-4 than deleting a superseded snapshot, or in a new record type
  that becomes a second thing to keep in step. What is genuinely lost is
  recorded in ADR-14's Costs: `mart_pipeline_freshness` now sees 8 runs of
  `business_locations` where 14 happened. The manifests of runs that wrote
  nothing are kept, and those are the ones nothing else can reconstruct.

## What this plan did not do

- **`raw_city_budget` and `raw_street_trees`, 55.5 MB.** ADR-10 cut both
  datasets and nothing deleted their trees, so they are 25.9 percent of the
  pruned raw prefix and no model reads a row of either. `make check-runs`
  already warns about both. They are not prunable by this tool and should not
  be: they are not in the registry, so it has no `refresh` and no `grain_key`
  to prove anything with, and deleting a whole dataset is a different act from
  deleting a superseded partition of a live one. It wants one line of an ADR or
  a dev note saying the scope decision extends to the zone, and then a
  `gcloud storage rm --recursive` on two prefixes.
