---
status: active
date: 2026-08-07
related: [adr-4-raw-zone-layout, adr-9-cloud-raw-zone, adr-8-published-exports, adr-12-published-export-layout, plan-9-raw-zone-retention, plan-7-pipeline-assurance]
---

# ADR-14. A superseded snapshot partition may be deleted, and a delta partition never may

Amends ADR-4 rather than superseding it, in the sense `docs/README.md` gives
that word. ADR-4 says the Parquet raw zone is append-only: files are added,
never edited or deleted, with `ingest.py --full-refresh` as the single
exception. This adds a second exception and changes nothing else. The layout,
the all-STRING contract, the run manifests, the watermark coming only from the
zone, and above all ADR-4's actual claim, that dropping the warehouse and
rebuilding from the zone reproduces every model, all carry forward. ADR-4 is
`superseded` already, by ADR-9, and is not edited here; ADRs are immutable once
accepted.

## Context

**A date, which is what makes this decision due rather than interesting.**
Measured on the real bucket on 2026-08-07: the raw prefix is 511.9 MB over 236
objects, against a 5 GB always-free allowance. The zone went remote on
2026-08-01, so at the observed rate the allowance is reached between
mid-September and early October 2026, and the first symptom is a bill rather
than a failure. PLAN-9 has the growth arithmetic.

**The growth is not what it looks like.** `ingest.py` is watermark-incremental
and does not re-fetch a dataset per run. `raw_business_locations` is 397.1 MB of
the 511.9, in seven daily partitions of about 49.6 MB each, because the city
periodically bumps `:updated_at` across a current-state registry and the next
run therefore fetches all 415,006 rows again. It is bursty, it is outside this
project's control, and it recurs.

**Those copies are sediment rather than history.** Staging deduplicates by
`grain_key` to the newest `_socrata_updated_at`, so once a later complete copy
exists the rows it supersedes cannot be returned by any model. The zone was
paying storage every month for rows no query can reach.

**Operations are not the constraint and should not be confused with this one.**
About 47 objects a day is roughly 1,410 Class A writes a month against a tier of
5,000, and a daily publish would add 210. ADR-12's arithmetic holds. The bytes
are the problem; the operations are not.

## Options considered

**A. A bucket lifecycle rule.** One line of bucket configuration, no code, no
maintenance. It is the obvious answer and it is why this option is written down
rather than left off the list. Rejected because it deletes by object age and
knows nothing about which partitions are snapshots. Pointed at this zone it
silently destroys 311 and building permit history, and the loss surfaces months
later as a hole in a monthly series that nothing can refill: the rows are gone
from the zone, gone from the warehouse the zone rebuilds, and only re-fetchable
from an API that serves current state. A destructive rule with no notion of what
it is deleting is worse than the bill.

**B. Compaction: rewrite many small Parquet files into fewer large ones.** Cuts
object count and helps the read path. Rejected for now on two grounds. It edits
the zone in place, which is a much larger break of ADR-4 than deleting a
partition whose rows are already unreachable, and it solves the wrong problem:
329 objects against no object quota at all, where the binding constraint is
bytes. Worth its own decision if object count ever becomes binding.

**C. Delete by age, keeping N partitions per dataset.** Cheap to implement and
predictable. Rejected for the same reason as A in a smaller way: age is a proxy
for supersession and not the thing itself. A dataset whose upstream stopped
republishing would have its only complete copy deleted on schedule.

**D. Delete a partition only when a surviving later one provably holds
everything it holds.** More code than any of the above, and it is the option
that can refuse. The honest case against it: it needs a per-dataset declaration
that can be wrong, it costs a query over two partitions per candidate, and it
will decline to delete partitions a human can see are superseded, because a
proof it cannot complete is a proof it does not have.

## Decision

Option D, as `ingestion/prune_raw.py`, run by hand through `make prune-raw` and
`make prune-raw-apply`.

**The snapshot-versus-delta split is what makes the exception safe, and it is
the whole of it.** The registry gains one field, `refresh`, in
`vars.pipeline_sources` in `dbt/dbt_project.yml`, which is the one copy of it:

| refresh | datasets | a partition holds | prunable |
|---|---|---|---|
| snapshot | business_locations, film_locations, analysis_neighborhoods, supervisor_districts, census_block_groups | the whole dataset as of that run | yes, once proven |
| delta | 311_cases, building_permits | only the rows that changed since the watermark | never |

Delta sources are not reachable from the prune by any flag. Naming one is
refused with an error rather than skipped, because a skip reads as "considered,
nothing to do", which is the wrong sentence about a source no partition of which
may ever be deleted. `refresh` is required rather than defaulted, and
`tests/test_dataset_registry.py` fails on a new dataset that omits it: the two
mistakes do not cost the same. A snapshot mislabelled delta wastes storage; a
delta mislabelled snapshot offers rows for deletion that nothing can bring back.

**The superset check is the mechanism, and membership of `snapshot` is nowhere
near sufficient.** `refresh: snapshot` says a partition of this dataset *can* be
complete, not that any given one *is*: a run that fetched 200 changed rows
writes a partition that looks exactly like a complete one from outside. So each
candidate is proven, before anything is deleted, against a partition that will
survive the prune, and both halves must hold:

1. every `grain_key` in the candidate is present in the surviving partition,
   which is what makes those rows still reachable after the delete; and
2. for each of those keys the survivor's newest `_socrata_updated_at` is not
   older than the candidate's, which is what makes them reachable *at the same
   values*. Staging picks the newest per key, so a superset that is behind on
   one key would quietly change what a model returns while leaving every row
   count identical.

A candidate that fails either is not deleted, is named with its numbers, and the
tool exits 3. Deleting nothing is always a correct outcome here and deleting the
wrong partition never is, which is `check_derived.py`'s principle applied to a
destructive operation.

**The newest two partitions per dataset are retained regardless of proof.** The
keep window is applied before any proof, so a proof cannot override it. One
rollback is worth about 50 MB.

**The run manifests are deleted with the partitions.** This answers PLAN-9's
second open question, and against its own suspicion that the cheaper answer was
the wrong one. `check_runs.py` compares each manifest under `<table>/_runs/`
against the rows carrying its run id and exits 3 MISCOUNTED when they disagree,
so a partition deleted without its manifests leaves the zone failing its own
consistency check. The alternative was a third state in `check_runs.py` for a
run whose rows were deliberately removed, and it loses on what it would cost:
the marker has to live somewhere, and the two places available are inside the
manifest, which means editing a file in the zone and is a *larger* break of
ADR-4 than deleting a superseded snapshot, or in a new record type beside it,
which makes a second thing to keep in step with the first. What tipped it is
that PLAN-7 step 1 made the opposite call for a reason that does not transfer:
it kept `check_runs.py` separate from `check_derived.py` because the reader and
the moment were different, and here the reader is the same file and the moment
is the same run.

**Manifests for runs that wrote no rows are never touched**, and that exclusion
is the load-bearing half rather than a detail. A run that found nothing new
writes no Parquet, so its manifest is the only record that it ran at all, and it
is the one thing in this zone that cannot be recomputed from anything else. It
is what lets `mart_pipeline_freshness` tell "ingestion ran and found nothing"
from "ingestion has not run in three days". Only manifests of runs every row of
which is inside a deleted partition go, which also leaves a run spanning two
partitions alone: its rows are not all being deleted, and deleting its manifest
would turn one defect `check_runs.py` reports into two.

**By hand, not on a schedule.** This answers PLAN-9's first open question. It is
how `make publish` is operated, and the reason is stronger here: a cron that
deletes data is a different risk appetite from a cron that writes some, and the
prune's whole design is that a human reads a refusal. Revisit when the zone is
bounded by something other than someone remembering.

**What must survive is ADR-4's claim, not its wording.** ADR-4's decision buys
"a warehouse can be rebuilt from disk without re-fetching anything", and the
acceptance test for this change is that claim and nothing weaker: prune, then
`make rebuild`, then every model's row count unchanged. A moved row count would
mean staging was not deduplicating what this argument assumes it was.

## Consequences

**Buys.** 297.8 MB of 511.9 comes back, 58.2 percent of the raw prefix, in 54
objects and 6 run manifests, and the free allowance stops being reachable this
year at the observed rate. The zone stops paying monthly for rows no query can
return. The `refresh` field also makes a sentence that was only in a registry
comment into something code can read and a test can check.

**Costs.** The zone is no longer append-only, and that is a real loss however
narrowly it is bounded: "files are only ever added" was a sentence you could
reason from without reading any code, and the replacement is a sentence plus a
tool plus a proof. A pruned dataset loses its per-run ingest history in
`raw_ingest_runs`, so `mart_pipeline_freshness` sees fewer runs for
`business_locations` than actually happened; the runs that wrote nothing are
kept, which is the half that matters, but the count is no longer the truth. A
prune forces `spatial.py` to recompute that dataset's derived rows in full,
because `derived_state._table_plan` treats a vanished partition as a replaced
zone; that is seconds and it is correct, and the comment there now names this as
the second of the two things that can cause it. And the proof costs a scan of
two partitions per candidate, about 34 seconds against the bucket for five.

**Lock-in.** `refresh` is now a required registry field, so every future dataset
has to answer the question, which is the intent. The `grain_key` field acquires
a second job: it was the join key between the derived zone and staging, and it
is now also what a deletion is proven against, so changing one for a dataset
with history in the zone changes what can be proven about that history. And the
keep window plus the proof means the zone's floor is two partitions per snapshot
dataset, so the storage this bounds to is a function of the largest dataset's
size rather than of time.

## Revisit if

- A snapshot dataset starts failing its proof regularly. That means the upstream
  stopped republishing wholesale and `refresh` has become a lie, which is a
  registry fix and not a prune fix.
- The zone becomes bounded by object count rather than bytes, which is the
  condition for reopening compaction (option B).
- Anyone wants the prune in `ingest.yml`. That is a decision about whether a
  scheduled job may delete data, and it should be made explicitly rather than by
  someone adding a step.
- `make rebuild` after a prune ever moves a row count. That falsifies the
  argument in Decision rather than revealing a bug in the tool, and the response
  is to stop pruning until it is understood.
