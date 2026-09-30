---
status: active
date: 2026-09-28
related: [adr-18-the-raw-zone, adr-19-withdrawn-registrations, adr-20-registry-history-as-dbt-snapshot]
---

# ADR-21. A withdrawn key is ledgered, and then its partitions are prunable

Amends ADR-18 section 4, the first limb of the superset proof. Takes the
decision ADR-19 deferred in so many words: "whether staging reading only the
newest partition is enough to tell `prune_raw.py` that an older one is
unreachable. It needs its own acceptance test." The second limb, the keep
window, the manifests rule, the 1 GB threshold, and every refusal in ADR-18
section 5 stand unchanged. The deletion stays by hand.

## Context

**The forcing constraint is ADR-19's own revisit trigger, which has fired.**
Step 4 landed 2026-09-13, and the zone kept growing. The retention job has
exited 3 on every Monday since 2026-08-24. Measured on the bucket on 2026-09-28,
after the first green ingest since the DataSF host move:

| | |
|---|---|
| raw zone | **1420.4 MB**, 628 objects, 420.4 MB over the threshold |
| `business_locations` partitions | 23, of which 19 unprovable under ADR-18 section 4 |
| why each one fails | first limb only: 6 to 22 keys absent from the survivor, **0 behind** on all 19 |
| distinct keys absent, over every partition | **27**, the 27 ADR-19 counted |

**What the 27 are, read row by row.** 19 are test records: `dba_name` like
"Test - Add Tta - Do Not Move", `self_reported_naics_code` `123456`, most at
1455 Market St Fl 16, all last seen 2026-09-02. 8 are erroneous registrations
withdrawn 2026-08-22 to 2026-08-25: misspelled duplicates of one owner at three
addresses, one certificate filed under three addresses, a trust entry, and a
location that closed in 2024. None reappears in the newest partition under
another key (same certificate and address, checked). The host move of
2026-09-09 is not involved: both groups were gone by 2026-09-07, which is from
the old host.

**Two facts make the old refusal the wrong answer now, and neither held when
ADR-19 was written.**

1. `restrict_to_newest_snapshot` is on all five snapshot staging models. A key
   absent from the newest partition is already in no model. What the stale
   partitions hold for those keys is not reachable data; it is *the only record
   that the key ever existed*, which is why CLAUDE.md lists registry history as
   "none" and ADR-20 was refused.
2. `check_snapshots.py` exists and gates the build. It is the one thing that
   can tell a withdrawal (tens of keys) from a run that died mid-fetch (whole
   multiples of `ROWS_PER_FILE`), which ADR-19 option A was refused for being
   unable to tell apart.

So the question stopped being "may we delete rows a model returns", which the
answer is still no, and became "may we delete the only record of a withdrawal".
That has an answer that costs kilobytes: keep the record somewhere else first.

## Options considered

**A. Keep refusing.** Correct under ADR-18, and it costs the zone its bound.
The floor is every `business_locations` partition since the first withdrawal,
about 50 MB a day of ingest, so the 5000 MB allowance is roughly 70 days out
and the red X says "stop pruning" forever about a condition that is understood.
ADR-18's own revisit clause says a check fired and ignored twice is not the
signal it was meant to be; this one has fired six times.

**B. A tolerance on the first limb, with nothing else.** ADR-19 option A, and
it stays refused. A tolerance alone cannot tell a withdrawal from a short run,
and it deletes the withdrawal's only record.

**C. A dbt snapshot for registry history.** ADR-20, refused 2026-09-13: the
warehouse is rebuilt from scratch, so a snapshot table cannot outlive a rebuild.
Nothing here reopens it.

**D. A withdrawal ledger in the zone, written and read back before the delete,
with the first limb relaxed to withdrawals only.** Chosen. The honest case
against it: it is a new kind of object in a zone whose layout ADR-18 section 1
fixes, it makes the prune write as well as delete, and its correctness now rests
on a staging macro and a guard in another module, either of which a later
change could quietly weaken. The third is why both are asserted by tests rather
than by prose.

## Decision

**A candidate whose only failure is the first limb is prunable when the absence
is a withdrawal, and a withdrawal is recorded before anything is deleted.** All
of these must hold, checked per candidate against the newest partition:

1. **The second limb holds.** No key present in the survivor is behind. Unchanged
   from ADR-18, and a withdrawal beside a stale key does not excuse it.
2. **No NULL `grain_key`** in the candidate. A row with no key cannot be ledgered.
3. **The survivor is not SHORT** by `check_snapshots.py`, with its constant,
   **and the candidate lost no more than that same tolerance (2%) of its keys**
   to the survivor. The second half exists because the guard compares
   neighbours: two short runs in a row pass it on the second.
4. **With `--apply`, the ledger reads back complete before the first delete.**
   `prune_raw.py` writes the last-seen row of every withdrawn key not already
   recorded to `<table>/_withdrawals/<stamp>.json`, then reads the whole ledger
   back, and deletes nothing unless it holds every withdrawn key. Exit 5,
   `UNRECORDED_EXIT`, otherwise.

**The ledger is JSON, append-only, and outside every reader's glob.** Not
Parquet: `read_sql` globs `<table>/**/*.parquet` and BigQuery's external tables
glob `<table>/*.parquet`, whose `*` crosses directories, so a Parquet ledger
would be read as rows of the dataset by both engines. Each record is the row as
last seen, plus `_last_seen_ingest_date`, `_absent_from_ingest_date` and
`_ledgered_at`. Files are added and never edited, which keeps it inside ADR-18's
rule that nothing in the zone is edited. Report mode, which is what
`retention.yml` runs, writes no ledger and reads only key columns.

**The precondition is asserted on every PR.** `tests/test_prune_raw.py` fails
if any `refresh: snapshot` staging model stops calling
`restrict_to_newest_snapshot`. Without the macro a withdrawn key is still in a
model, and this ADR's argument falls.

### The acceptance test

ADR-18 section 8's test, no model's row count moves after `make rebuild`, still
applies and still has to be run. ADR-19 is right that it is trivially satisfied
for partitions staging already ignores, so it is not the test of this decision.
This one is: **every key present in a deleted partition is either in the
survivor at a value no older, or in the ledger.** The first half is limbs 1 and
2; the second half is condition 4, which checks it against the ledger as read
back from the zone, before the delete, when it can still fail safely.

## Consequences

**Buys.** The zone is bounded again. Measured in report mode on 2026-09-28: 21
partitions and 22 manifests prunable, 1095.3 MB, leaving **325.1 MB**, and 27
keys to ledger. The retention job's exit goes from 3, "stop pruning", to 4, "run
the apply", which is the thing it was built to say. And the project gets
registry history for the first time: a withdrawal is now a durable record
rather than a side effect of partitions nobody could delete, and it survives
every rebuild because it is in the zone and not the warehouse.

**Costs.** The prune now writes to the zone as well as deleting from it, and it
imports `check_snapshots.py`, so the build gate's tolerance is now load bearing
for a destructive operation: raising it to quiet the guard would also widen
what the prune accepts as a withdrawal. The ledger holds rows no model reads,
and nothing yet reads it at all. A loss between 0 and 2% of a partition's keys
that is *not* a withdrawal, a run that died in its last file, is indistinguishable
from one; it is ledgered rather than lost, which is the whole reason the ledger
is the gate rather than the tolerance.

**Lock-in.** `_withdrawals/` joins `_runs/` as a reserved name inside every
dataset directory, and its JSON format is now what keeps it out of both
engines. Any future change to the ledger to Parquet, or to the readers' globs,
has to move the ledger out of the dataset directory first. And ADR-18's
superset proof is no longer one rule: it is a rule and an exception with four
conditions, and the conditions live in two modules.

## Revisit if

- **Anything starts reading the ledger** (a `stg_` model over it, a withdrawal
  mart). Then it is a source, it needs a registry entry and a loader, and the
  JSON-beside-Parquet arrangement should be reconsidered with it.
- **A withdrawal exceeds 2% of a partition.** The prune will refuse it, correctly,
  and the answer is to find out what upstream did, not to raise the tolerance.
- **A `refresh: snapshot` model stops using `restrict_to_newest_snapshot`.** The
  test fails first; if it is ever removed instead, this ADR is void.
- **The ledger grows past a few MB.** At 27 keys in seven weeks it will not for
  years; if it does, the withdrawal rate has changed by orders of magnitude and
  ADR-19's own revisit clause fires too.
