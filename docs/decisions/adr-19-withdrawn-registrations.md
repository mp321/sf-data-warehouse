---
status: active
date: 2026-09-07
related: [adr-9-cloud-raw-zone, adr-14-raw-zone-retention, adr-17-scheduled-retention-proof, adr-18-the-raw-zone]
---

# ADR-19. Withdrawn registrations: the newest snapshot is the dataset

Amends ADR-18's first revisit clause. It does not touch the append-only rule,
its three exceptions, the superset proof or the 1 GB threshold; all of those
stand exactly as ADR-18 states them. What it changes is the *reading* of a
snapshot dataset failing its proof, which ADR-18 wrote down as one thing and
which turns out to be two.

## Context

**The forcing constraint is that `retention.yml` has failed exit 3 on three
consecutive Mondays and the refusal is correct, so there is nothing to fix in
the tool and the zone keeps growing anyway.**

| Monday | raw zone | objects | exit | plan |
|---|---|---|---|---|
| 2026-08-10 | 322.7 MB | 191 | 0 | 149.0 MB prunable; a human applied it, floor 173.8 MB |
| 2026-08-17 | 298.1 MB | 241 | 0 | 49.7 MB prunable |
| 2026-08-24 | 680.5 MB | 360 | 3 | 0 MB prunable, 8 unproven |
| 2026-08-31 | 1064.4 MB | 479 | 3 | 199.2 MB prunable, 11 unproven |
| 2026-09-07 | 1449.5 MB | 597 | 3 | 149.6 MB prunable, 19 unproven |

Growth over the last week is 385.1 MB, **55.0 MB/day**, and the zone is 449.5
MB past a 1000 MB threshold with a 5000 MB always-free allowance behind it.

**ADR-18's revisit clause names the wrong cause, and the report says so in a
column nobody had read.** The clause reads: "A snapshot dataset starts failing
its proof regularly. That means the upstream stopped republishing wholesale and
`refresh` has become a lie, which is a registry fix and not a prune fix." The
proof has two limbs (ADR-18 section 4): every `grain_key` in the candidate is
present in the survivor, and for each of those keys the survivor is no older.
`refresh` becoming a lie is a *second-limb* failure: a partial republish leaves
the survivor behind on keys it does hold.

Every one of the 19 unproven partitions on 2026-09-07 fails the **first** limb
and the second limb is zero on all 19:

```
2026-08-14  NOT superseded by 2026-09-07: 6 of 365307 grain_key(s) absent,
                                          0 present at an older _socrata_updated_at
2026-08-22  NOT superseded by 2026-09-07: 22 of 365679 grain_key(s) absent,  0 ...
2026-09-02  NOT superseded by 2026-09-07: 18 of 366264 grain_key(s) absent,  0 ...
```

Keys are missing from the newer partition, not stale in it. **The city
withdraws records from `business_locations`.** It is a current-state registry
of active business locations, and a location that closes or is revoked leaves
the registry; there is no tombstone row and no status transition in the zone,
the `uniqueid` is simply absent from the next bulk refresh. `refresh: snapshot`
is still true. Upstream still republishes wholesale. The partition is still
complete. It is complete *as of a later day*, and a later day's registry is not
a superset of an earlier day's.

**So the refusal is correct and it is permanent.** The withdrawn keys never
come back, so the proof against them can never be satisfied by waiting. Only
the partitions written between the last withdrawal and the keep window are
provable, which on 2026-09-07 was three days: 09-03, 09-04 and 09-05, against a
keep window of 09-06 and 09-07. That is the shape of the whole problem. The
prune reclaims a few days and the zone keeps every partition older than the
last withdrawal forever.

**What the marts return, measured 2026-09-07 on the bucket zone.** Staging
unions all 24 partitions and deduplicates by `grain_key` to the newest
`_socrata_updated_at`, so a withdrawn record survives in the model for as long
as any partition holding it survives in the zone:

| | keys |
|---|---|
| `stg_datasf__business_locations`, union of 24 partitions | 366,491 |
| newest partition, `ingest_date=2026-09-07` | 366,464 |
| **records the marts return that the city's registry no longer holds** | **27** |

That is the number that makes this an ADR rather than a bug report. The zone is
not merely paying storage for unreachable rows, which is what ADR-18's prune
exists for; it is paying storage for rows that are reachable and should not be.
Withdrawal is being read as absence of an update.

## Options considered

**A. Relax the proof: allow a candidate whose missing keys are missing because
they were withdrawn.** The cheapest change and the one to refuse. There is no
way to tell "withdrawn upstream" from "this run fetched part of the dataset"
from inside the zone: both are a key in an older partition and not in a newer
one. Worse, it is wrong even when it guesses right, and this is the part that
is easy to miss. **Today staging still returns those 27 records.** Deleting the
partitions that hold them would move mart row counts, which is precisely the
failure ADR-18 section 8 says means stop pruning. A proof relaxed before the
staging change is not a faster route to the same place; it is data loss with
the acceptance test disabled to hide it. ADR-18's own refusal of a proof
override applies verbatim: the override is reached for at the moment the check
is inconvenient, which is the moment it is right.

**B. Raise the threshold.** ADR-18 has already refused this in advance ("Not a
higher threshold") and the arithmetic refuses it again: at 55.0 MB/day the
allowance is 67 days away, so a threshold raise buys two months and hides the
only signal that would notice.

**C. Delete by age, or a bucket lifecycle rule.** Refused in ADR-14, again in
ADR-17, again in ADR-18, and nothing here reopens either.

**D. Make the newest snapshot partition authoritative, then let the proof
follow.** What a `refresh: snapshot` dataset means is "the registry as of this
run". Staging does not read it that way; it reads every partition ever written
and takes the newest version per key, which is the correct rule for a delta
source and the wrong one for a snapshot. Fixing that makes the 27 withdrawn
records leave the marts, which is a correctness fix on its own, and it makes an
older partition genuinely unreachable, which is what a later revisit of the
proof would rest on. The honest case against it: it is four changes rather than
one, it moves mart row counts once, and it does not reclaim a byte by itself.
Chosen anyway, because it is the only option that makes the zone's growth
addressable without first making the warehouse wrong.

## Decision

**The newest `ingest_date` of a snapshot dataset is the dataset. Everything
else follows from that.** Four steps, in this order, and the order is the
decision as much as the steps are.

1. **Prune what is provable, and record that the prune is not the lever.** Run
   the apply by hand as ADR-18 designs it, and take the measurement rather than
   the estimate. Done 2026-09-07; the numbers are below.
2. **A `refresh: snapshot` dataset fetches from its `start_date`, never from
   the zone watermark** (`ingest.py`, `resolve_watermark`). Every snapshot
   partition is then complete by construction. It was previously complete by
   accident: the city bumps `:updated_at` across the whole registry on a bulk
   refresh, so a watermark fetch happened to return every row. `ingest.py` did
   not read `refresh` at all before this. The change costs nothing today for
   exactly the reason it was needed today.
3. **A completeness guard beside `check_runs.py`.** A snapshot partition whose
   distinct `grain_key` count is materially below its predecessor's is a run
   that died mid-fetch, and it is invisible to every other check in the
   project: `_flush` counts as it writes, so the manifest claims exactly what
   landed and `check_runs.py` passes by design. `ingestion/check_snapshots.py`,
   one verdict, SHORT, exit 3. **It does not gate `make build` today and it
   must gate it as step 4's first line**; the reasoning is in the module
   docstring and is restated under Consequences.
4. **A shared macro restricting snapshot staging models to the `grain_key`s
   present in the newest `ingest_date`**, applied to every snapshot model and
   not only `business_locations`. `ingest_date` is a queryable STRING column on
   both engines. **Not implemented as of this ADR's date**, and it is the step
   that moves row counts: the one-time movement is expected, must be captured
   per model as evidence, and is not ADR-18 section 8's failure, which is about
   a *prune* moving counts.

**Step 4 does not license a change to the proof.** A later ADR may argue that
once staging reads only the newest partition an older one is genuinely
unreachable and the proof can be told so. That is a separate decision with its
own acceptance test, and it is not taken here.

### What step 1 measured

| | |
|---|---|
| zone before | **1449.5 MB**, 597 objects |
| removed | **149.6 MB**, 27 objects: 3 partitions and 3 run manifests |
| partitions deleted | `raw_business_locations` 2026-09-03, 09-04, 09-05 |
| zone after | **1299.9 MB**, 570 objects |
| still over threshold by | 299.9 MB, **with nothing prunable** |
| runway to the 5000 MB allowance at 55.0 MB/day | **67 days** |

ADR-18 section 8's acceptance test, run in its clean form for the first time
since 2026-08-07 and in the window ADR-18 asked for, after the day's 09:17 UTC
ingest and before the next: `make rebuild`, `PASS=172 ERROR=0`, and **0 of 19
models moved**. The only relations that moved are the two raw mirrors of the
zone, which are not models and are rebuilt wholesale by `load.py`:
`raw_business_locations` 9,147,074 to 8,047,940 rows, which is exactly the
366,307 + 366,389 + 366,438 rows of the three deleted partitions, and
`raw_ingest_runs` 316 to 313 manifests.

**"Nothing in it is prunable" is now the zone's steady state**, and it is
ADR-18's fourth revisit clause firing verbatim: "The zone is over the threshold
with nothing prunable. The prune is not the lever in that case and neither is
this check."

### What the guard's tolerance is set against

`SHORTFALL_TOLERANCE` is 2%. The measured movement it has to stay quiet through,
`business_locations` over 2026-08-14 to 2026-09-07:

- key counts rose monotonically, 365,307 to 366,464, every day-over-day delta
  positive, largest +144 (+0.039%);
- withdrawals removed 6 to 22 keys at a time, at most 0.006% of the dataset,
  and never enough to make a day net negative;
- `ingest_date=2026-08-15` holds 730,755 rows and 365,400 distinct keys, two
  runs on one day. **This is why the guard counts distinct `grain_key` and not
  rows**: a row-based check would have read 365,307, then 730,755, then
  365,410 and called the third partition a 50% collapse.

The failure it has to catch is a run killed mid-fetch, which loses whole
multiples of `ROWS_PER_FILE` out of 366,000. Two orders of magnitude separate
the noise from the failure, so the constant is not load bearing and its order
of magnitude is.

## Consequences

**Buys.** The exit 3 on the last three Mondays has a cause with a measurement
behind it rather than a revisit clause pointing at the wrong fix, and the
project stops being able to reach for the one change that would make the
warehouse wrong. Snapshot completeness becomes a property of the code rather
than a property of how DataSF happens to bump `:updated_at`. The mid-fetch
death, which no check in the project could see, becomes a named verdict. And
the 27 withdrawn records the marts return are now a written-down number rather
than an unasked question.

**Costs.** Steps 2 and 3 reclaim nothing. The zone is 1299.9 MB with 67 days of
runway and this ADR does not move that number at all; step 4 does not move it
either. What buys headroom is a later decision this one deliberately does not
take. Anyone reading the retention job's red X expecting these three steps to
clear it will be disappointed on the next three Mondays, and that is the
correct outcome rather than a gap: the red X is now saying something true that
the prune cannot fix.

Step 3 also ships a guard that gates nothing, which is the state CLAUDE.md
warns about for a check that fires without consequence. It is accepted for one
release because the consequence arrives with step 4 and the guard has to exist
before the thing that needs it, not after.

**Lock-in.** After step 4, `ingest_date` stops being partition bookkeeping and
becomes a semantic column that staging depends on, which means the raw zone
layout (ADR-9, ADR-18 section 1) can no longer change without changing what the
snapshot models return. `grain_key` takes a third job on top of ADR-18's two:
it is the dedup key, the thing a deletion is proven against, and now the thing
completeness is measured in. And the four steps are ordered by safety rather
than convenience, so doing 4 before 2 and 3 would filter staging to a partition
nothing guarantees is complete.

## Revisit if

- **A snapshot partition fails the guard.** That is the mid-fetch death this
  was written for. Re-run `make ingest` for the dataset; the short partition is
  then superseded and provable. It is not a reason to raise the tolerance.
- **The withdrawal rate changes by an order of magnitude**, in either
  direction. At 6 to 22 keys per event the 2% tolerance has two orders of
  magnitude of room; at 4,000 it does not, and the guard would start firing on
  the city rather than on a defect.
- **Step 4 lands and the zone still grows at 55 MB/day**, which it will. That
  is the trigger for the decision this ADR does not take: whether staging
  reading only the newest partition is enough to tell `prune_raw.py` that an
  older one is unreachable. It needs its own acceptance test, because the
  existing one, no model's row count moves, is satisfied trivially by a
  staging model that already ignores the partition being deleted.
- **The allowance comes inside 30 days**, currently 2026-11-13 at 55.0 MB/day.
  At that point the choice is a decision taken under a deadline, which is what
  ADR-14 was, and this ADR exists partly so the next one is not.
- **`business_locations` stops withdrawing records for a month.** Then the
  proof starts succeeding again on its own, this ADR's context is wrong about
  the upstream, and steps 2 and 3 are still correct for their own reasons.
