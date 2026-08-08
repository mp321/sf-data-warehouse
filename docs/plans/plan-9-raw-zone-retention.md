---
status: draft
date: 2026-08-07
related: [adr-4-raw-zone-layout, adr-9-cloud-raw-zone, adr-8-published-exports, adr-12-published-export-layout, plan-7-pipeline-assurance]
---

# PLAN-9. Bound what the buckets accumulate

Two prefixes grow without limit and neither has an owner. The raw zone grows
because it is append-only by decision, and the published prefix grows because
the uploader copies and never deletes. Both are cheap to bound and neither is
bounded.

## Why now

**Measured 2026-08-07, on the real bucket.** The raw prefix is 511.9 MB across
329 objects. The zone went remote on 2026-08-01, so that is six or seven days,
depending on how many of the runs in that window were manual dispatches.

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

- [ ] The bucket's raw prefix is measured per dataset and per partition, and the
      growth rate is a number rather than a range.
- [ ] The registry declares each dataset snapshot or delta, and a test fails if
      a new dataset omits it.
- [ ] A prune exists that refuses to delete a partition it cannot prove is
      superseded, and removes the run manifests with it.
- [ ] Prune then `make rebuild` leaves every model's row count unchanged, and
      `make context-pack-check` still passes.
- [ ] `publish/export.py --prune` removes orphans after the manifest lands, and
      the published prefix in the bucket holds 7 objects at
      `manifest_version` 2.
- [ ] An ADR amends ADR-4's append-only rule and records why the exception is
      safe for one kind of dataset and unsafe for the other.

## Open questions

- **Where does the prune run?** A make target run by hand is the smallest thing
  that works and matches how `make publish` is operated today. A step at the end
  of `ingest.yml` would keep the zone bounded without anyone remembering, and
  would also mean a scheduled job deletes data, which is a different risk
  appetite. Decide with the ADR.
- **Does `check_runs.py` get a third state?** Step 4 offers two answers and the
  cheaper one may be the wrong one. A run whose rows were deliberately removed
  is a fact about the zone, and a check that cannot express it is a check that
  will be silenced later. PLAN-7 step 1 made the same call the other way and its
  reasoning is worth re-reading first.
