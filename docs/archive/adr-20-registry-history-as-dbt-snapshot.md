---
status: superseded
date: 2026-09-10
related: [adr-9-cloud-raw-zone, adr-18-the-raw-zone, adr-19-withdrawn-registrations, plan-10-snapshot-semantics-and-exposures]
---

# ADR-20. Registry history lives in a dbt snapshot, not in the raw zone

Draft, to be argued in the first PLAN-10 session. The Decision section is a
proposal until this file is `active`.

## Refused, 2026-09-13 (PLAN-10 step 4)

**A dbt snapshot needs a warehouse that lasts, and this project's warehouse is
thrown away on purpose. Choosing C would give up that rule for a history that
starts empty and still needs the old partitions.** Nothing was built. The text
below this note is left as it was argued.

1. **The Decision's durability does not exist.** `make rebuild` starts by
   deleting the DuckDB file, `ci-build` deletes it and rebuilds to prove the
   zones are enough, and a runner starts each job with no DuckDB file.
   `make publish` copies the table out, but nothing loads it back before the
   next `dbt snapshot`. Scheduling publish is also outside PLAN-10's scope.
2. **It cannot see withdrawals that already happened.** Step 3 removed the 27
   from staging, so the first snapshot run never sees them. The old
   partitions stay the only record of them, so C is not "the precondition
   for reclaiming the zone".
3. **It runs in places that don't want it.** `dbt build` runs snapshots, so
   `make build`, `make rebuild`, `ci-build` and the weekly `dbt.yml`
   (`dbt build --target bigquery`) would each write a history of their own.
   The BigQuery one would be the first table BigQuery stores (ADR-9).
   Running it on BigQuery alone would keep it, but the marts, the export and
   both context packs all read DuckDB (ADR-15).
4. **It needs a daily dbt run that doesn't exist**, since `ingest.yml` runs
   no dbt. Minor: `invalidate_hard_deletes` has been deprecated since dbt 1.9
   (1.12.0 is installed).

A and B stay refused for the reasons given under them. The question itself,
which locations left the registry and when, is still real.

**Where a successor ADR should start (not decided here): a withdrawal ledger
in the raw zone.** At ingest, write the keys that were in the previous
complete snapshot partition and are missing from the new one, with their last
row, as a small append-only partition. For: everything downstream of Parquet
stays disposable, no new workflow, no data stored in BigQuery, a one-time
backfill from the partitions still in the zone, and a testable condition for
the deferred prune (survivor plus ledger covers every key in the candidate).
Against: custom ingest code rather than a dbt feature, withdrawals only with
no attribute history, and it must refuse to write on a SHORT partition,
because otherwise it writes false withdrawals into a file that can't be
edited.

## Context

**The forcing constraint is ADR-19's own consequence: once step 4 lands,
the only place a withdrawn business location survives is a raw partition
the zone is paying 55 MB/day to keep, and the zone crosses its always-free
allowance on about 2026-11-13.**

ADR-19 establishes that `business_locations` is a current-state registry
and that the city withdraws records from it without a tombstone. Step 4
makes the newest partition the dataset, which is correct for the marts and
removes the 27 withdrawn records they should not return. It also makes the
history question explicit: after step 4, an older partition is the sole
record that a location ever existed, and the prune cannot touch it because
the superset proof fails on exactly those withdrawn keys. The zone is
therefore keeping 24 near-identical 366k-row copies to preserve a few dozen
rows of difference between them.

Nothing today answers "which locations left the registry, and when." That
is a real question for a city-activity warehouse (closures by neighborhood
and month) and it is the question the zone is implicitly paying to keep
answerable.

## Options considered

**A. Keep history in the raw zone, as now.** No new artifact, no new
workflow, the zones stay the only system of record. The honest case
against: it costs 55 MB/day to keep a few dozen rows, the proof can never
pass on those partitions, and nothing downstream actually reads the
history, so the cost buys a capability nobody uses.

**B. Derive history in a model from `ingest_date` partitions.** A staging
or intermediate model that computes first-seen and last-seen `ingest_date`
per `uniqueid` across all partitions. Rebuildable from the zones, no
stateful table. The honest case against: it depends on every partition
staying in the zone, which is exactly the dependency this ADR wants to
remove, and it reads 8 million rows to answer a question about 27.

**C. A dbt snapshot over the staging model with hard-delete invalidation.**
`dbt snapshot` writes one row per `uniqueid` per version, with
`dbt_valid_from` and `dbt_valid_to`; a key absent from the source gets a
`dbt_valid_to` on the run that notices. The honest case against, and it is
the real cost: this is the project's first stateful artifact that cannot
be rebuilt from the zones once older partitions are gone. The design goal
since ADR-1 has been that everything downstream of Parquet is disposable.
A snapshot table is not, and it needs a scheduled run to be true, which no
workflow provides today.

## Decision

Proposed: **C.** The snapshot table becomes the record of registry history,
which lets a later ADR argue that a superseded raw partition is unreachable
and prunable. That later argument is not made here.

Concretely: `dbt/snapshots/snap_business_locations.sql`, unique key
`uniqueid`, `invalidate_hard_deletes: true`, strategy chosen by measurement
on the bucket zone (PLAN-10 open question), run daily against the DuckDB
warehouse in the workflow that already ingests, and durable because the
snapshot table is included in `make publish`.

## Consequences

**Buys.** A queryable answer to which locations left the registry and when,
at the cost of rows that actually changed rather than full copies. And the
precondition for reclaiming the zone without losing anything the warehouse
can answer.

**Costs.** A stateful table the zones cannot regenerate after a prune, a
daily dbt run that does not exist yet, and a decision about whether
BigQuery stores its own copy or reads a published one (ADR-9 says BigQuery
stores no raw bytes; a snapshot is not raw, but the principle needs a
sentence). If the daily run fails silently for a week, a week of
withdrawals is lost with no proof it happened.

**Lock-in.** `uniqueid` becomes the snapshot key and cannot be reinterpreted
without invalidating the history. The published export gains a file with a
different shape from the marts, and `MANIFEST_VERSION` moves.

## Revisit if

- The snapshot run misses two consecutive scheduled days. The table is then
  wrong in a way nothing detects, and a completeness check for the snapshot
  itself is the next piece of work.
- The measured false-change rate under the chosen strategy exceeds a few
  hundred rows a day on a registry that moves by tens. The strategy is
  wrong, not the decision.
- Someone needs history for a second snapshot dataset. Generalise then, not
  before.
