---
status: active
date: 2026-08-10
related: [adr-1-warehouse-targets, adr-4-raw-zone-layout, adr-9-cloud-raw-zone, adr-10-narrowed-scope, adr-14-raw-zone-retention, adr-16-cut-datasets-leave-the-zone, adr-17-scheduled-retention-proof, plan-9-raw-zone-retention]
---

# ADR-18. The raw zone: what it is, and everything that may delete from it

Supersedes ADR-4, ADR-14, ADR-16 and ADR-17. Those four are the same
conversation held five times, and this is the one document to read instead of
all of them. Nothing below reverses any of them; the rules are theirs and the
arithmetic is theirs, restated once with the refusals attached rather than
scattered across four files and an amendment note.

**It does not supersede ADR-9**, which is the fifth voice and the only one still
worth reading beside this. ADR-9 owns two things this ADR deliberately does not
touch: where the files physically are (`RAW_ZONE_URI`, a bucket prefix, local by
default) and how BigQuery gets at them (external tables over GCS, `gcsfs` for
DuckDB, `DIR` beats `URI`). ADR-9 opens by saying ADR-4 is otherwise still the
description of the zone. **This ADR is now that description**, so read ADR-9's
first paragraph as pointing here.

## Context

**The forcing constraint is a convention this project wrote down and then hit.**
`docs/README.md` says that when a fifth document amends one rule, the rule wants
restating in one place under a new ADR that supersedes the lot rather than
another amendment. ADR-4 stated the append-only rule; ADR-14 added the second
exception; ADR-16 added the third; ADR-17 moved half of ADR-14's answer onto a
schedule. The live rule lived in two documents with the amendments recorded in
two more, plus a note at the top of ADR-14, and the 2026-08-09 note named the
answer before this ADR existed: not a sixth document.

**The second forcing constraint is that the arithmetic finally has a measured
floor rather than an estimate.** ADR-17 set a 1 GB threshold against an
estimated 170 MB floor. On 2026-08-10 the first scheduled run reported, a human
ran the apply, and the raw prefix came to rest at 173,751,272 bytes over 164
objects, which is the workflow's own projection to the tenth of a megabyte. A
threshold argued from an estimate and a threshold argued from a measurement are
different documents, and restating is the cheap moment to fix that.

Nothing here is due to a failure. The zone is on its floor, the workflow is
green, and the tools refuse what they are supposed to refuse.

## Options considered

**A. A fifth amendment, as an ADR-18 that amends ADR-14 again.** Consistent with
what the last three did, and cheapest to write. Rejected on the convention
above: the reader's cost is what matters, and it was already four documents plus
a note to answer "may this file be deleted".

**B. Edit ADR-4 and fold the amendments into it.** The tidiest file. Refused
outright: ADRs are immutable once accepted, and the record of what was believed
when is the thing that makes them worth having. This is not a close call and is
listed only because a consolidation pass is exactly when someone reaches for it.

**C. Leave it as it is and improve the index.** Free, and `docs/README.md` was
already doing a decent job of explaining the shape. Rejected because the index
was explaining an accident rather than describing a design, and because the
honest version of that index was getting longer than the rule.

**D. One ADR that supersedes the four and restates the rule with its refusals
attached.** Chosen. The honest case against it: it moves four arguments into one
document written by someone who was not in the room for any of them, and a
restatement can lose a qualifier the original had. The mitigation is that the
originals are not deleted, only archived, and every section below names the ADR
it came from so a reader who suspects a lost qualifier knows exactly which file
to open.

Four substantive options were refused inside those ADRs and are restated in the
Decision rather than left in the archive, because a refusal that is not next to
the rule gets re-proposed: a bucket lifecycle rule, compaction, deletion by age,
and a flag that switches off the prune's proof.

## Decision

### 1. What the zone is (from ADR-4, unchanged)

```
<zone>/<table>/ingest_date=YYYY-MM-DD/part-<run_id>-<seq>.parquet
<zone>/<table>/_runs/<run_id>.json
```

`<zone>` is `data/raw` or a `gs://` prefix (ADR-9). `<table>` is the raw table
name from the registry, so one directory is exactly one dbt source on both
engines. `ingest_date` is a hive partition key and exists only in the directory
name, which is why every read goes through `raw_zone.read_sql()`: it is the one
place that asks for `hive_partitioning`, keeps the partition column a VARCHAR so
the all-STRING contract has no exceptions, and unions files by name because
Socrata omits null fields per record and files therefore genuinely differ in
which columns they carry.

Also unchanged, and load bearing: every run writes `_runs/<run_id>.json`, which
is the only record of a run that fetched nothing; the watermark comes from the
zone and from nowhere else; ingestion and loading are separate commands;
`load.py` rebuilds each raw table wholesale rather than tracking partitions; and
paging orders by `(:updated_at, :id)`, a total order, because ordering by a
non-unique column with offset paging was silently losing rows.

**ADR-4's actual buy is the claim to defend**: a warehouse can be rebuilt from
the zone without re-fetching anything. Every rule below exists to keep that true.

### 2. Append-only, with exactly three exceptions

Files are added to the zone and never edited. **Nothing edits a file in the
zone**, which is the part of the rule with no exceptions at all. Three things
may remove one, and each is safe for a different reason:

| # | Exception | The proof that it is safe | Who runs it |
|---|---|---|---|
| 1 | `ingest.py --full-refresh` (ADR-4) | it swaps a whole tree atomically, so no window exists in which the zone holds neither | by hand, local zones only |
| 2 | the prune, `ingestion/prune_raw.py` (ADR-14) | a surviving later partition provably holds every `grain_key` at values no older | by hand, `make prune-raw-apply` |
| 3 | a scope deletion (ADR-16) | nothing reads the dataset: absent from the registry, so absent from dbt, checked on every PR | by hand, once per ADR that cuts a dataset |

**Exception 1 is local only.** A directory rename is atomic and object storage
has no rename, so `--full-refresh` against a `gs://` zone refuses and explains
itself rather than doing a delete-then-copy that can leave the zone holding
neither tree. Refresh into a local zone and upload the result.

**Exception 3 is not a smaller version of exception 2, and the prune is
deliberately not the tool for it.** With no surviving partition the superset
proof is not merely unavailable, it is false by construction: every key is
unreachable afterwards, which is the exact condition the prune refuses on.
`prune_raw.py` exits on a name the registry does not hold, and that refusal is
correct and stays. The scope deletion's proof is a repo proof rather than a data
proof, and it is the stronger of the two: the registry has one copy,
`tests/test_dataset_registry.py` asserts in both directions that it agrees with
the dbt sources, the staging models and the fixtures, and dbt cannot read a
table that is not a declared source. Its safety net is that the rows are
re-fetchable from DataSF, which a delta partition's are not.

**No tool was written for exception 3 and none should be.** Its next occurrence
is the next ADR that cuts a dataset. `check_runs.py`'s warning, "in the zone and
not in the registry, so nothing reads it", is what catches the step being
skipped, so **that warning must never be softened into silence**; it is the only
thing standing between this residue and nobody noticing it.

### 3. Snapshot datasets are prunable, delta datasets never are

`refresh` is a required field in `vars.pipeline_sources` in
`dbt/dbt_project.yml`, which is the one copy of the registry.

| refresh | datasets | a partition holds | prunable |
|---|---|---|---|
| snapshot | business_locations, film_locations, analysis_neighborhoods, supervisor_districts, census_block_groups | the whole dataset as of that run | yes, once proven |
| delta | 311_cases, building_permits | only the rows that changed since the watermark | never |

Deleting a delta partition deletes rows nothing can bring back, because an API
serving current state cannot return what it served in July. Delta sources are
not reachable from the prune by any flag, and naming one is an error rather than
a skip: a skip reads as "considered, nothing to do", which is the wrong sentence
about a source no partition of which may ever be deleted. `refresh` is required
rather than defaulted, and a new dataset omitting it fails
`tests/test_dataset_registry.py`, because the two mistakes do not cost the same:
a snapshot mislabelled delta wastes storage, a delta mislabelled snapshot offers
rows for deletion that nothing can bring back.

### 4. The superset proof, which is the whole of why exception 2 is safe

`refresh: snapshot` says a partition of this dataset *can* be complete, not that
any given one *is*: a run that fetched 200 changed rows writes a partition that
looks exactly like a complete one from outside. So every candidate is proven,
before anything is deleted, against a partition that will survive the prune, and
both halves must hold:

1. every `grain_key` in the candidate is present in the surviving partition,
   which is what makes those rows still reachable after the delete; and
2. for each of those keys the survivor's newest `_socrata_updated_at` is not
   older than the candidate's, which is what makes them reachable *at the same
   values*. Staging picks the newest per key, so a superset that is behind on
   one key would quietly change what a model returns while leaving every row
   count identical.

A candidate failing either is not deleted, is named with its numbers, and the
tool exits 3. **Deleting nothing is always a correct outcome here and deleting
the wrong partition never is**, which is `check_derived.py`'s principle applied
to a destructive operation.

Three things sit around the proof and each was decided against a cheaper
alternative:

- **The newest two partitions per dataset are kept regardless of proof**, and
  the keep window is applied before any proof so a proof cannot override it. One
  rollback is worth about 50 MB.
- **A partition's run manifests are deleted with it**, because `check_runs.py`
  compares each manifest against the rows carrying its run id, and a partition
  deleted without its manifests leaves the zone failing its own consistency
  check. The alternative, a third state in `check_runs.py` for rows deliberately
  removed, loses on where the marker would live: inside the manifest means
  editing a file in the zone, which is a larger break than the deletion, and
  beside it means a second thing to keep in step with the first.
- **Manifests of runs that wrote no rows are never touched**, and this is the
  load-bearing half rather than a detail. Such a manifest is the only record
  that the run happened, and it is the one thing here that cannot be recomputed
  from anything else. It is what lets `mart_pipeline_freshness` tell "ingestion
  ran and found nothing" from "ingestion has not run in three days". Only
  manifests of runs every row of which is inside a deleted partition go, which
  also leaves a run spanning two partitions alone.

**A flag that disables the proof would be a worse tool than no flag.** This is
the refusal that generalises past the case that produced it (ADR-16): the proof
is not a safety check bolted onto the deletion, it is the entire argument that
the deletion is not data loss. A destructive tool whose central check has an
override has, in practice, no check, because the override is what gets reached
for at the moment the check is inconvenient, which is exactly the moment it is
right. When the proof does not apply, the answer is a different act with its own
proof, which is what exception 3 is.

### 5. What is refused, and stays refused

- **A bucket lifecycle rule.** One line of configuration, no code, no
  maintenance, and it is the obvious answer, which is why it is written down
  rather than left off the list. It deletes by object age and knows nothing
  about which partitions are snapshots, so pointed at this zone it destroys 311
  and building permit history, surfacing months later as a hole in a monthly
  series that nothing can refill. A destructive rule with no notion of what it
  is deleting is worse than the bill. Refused in ADR-14, not reopened by ADR-17,
  and not reopened here. The only occurrence of the phrase in the repo is
  `retention.yml`'s comment refusing to become one.
- **Compaction**, rewriting many small Parquet files into fewer large ones. It
  edits the zone in place, which is a much larger break than deleting a
  partition whose rows are already unreachable, and it solves the wrong problem:
  the binding constraint is bytes and there is no object quota. Reopen it if
  object count ever becomes binding, as its own decision.
- **Deletion by age, keeping N partitions per dataset.** Age is a proxy for
  supersession and not the thing itself. A dataset whose upstream stopped
  republishing would have its only complete copy deleted on schedule.
- **`--apply` in any workflow.** Refused in ADR-14, refused again in ADR-17 with
  two days of evidence in hand, and refused here. A cron that deletes data is a
  different risk appetite from a cron that writes some, and the prune's whole
  design is that a human reads a refusal. **It should be reopened by an ADR
  rather than by a line added to a YAML file.**

### 6. The proof runs on a schedule and the deletion never does

`prune_raw.py` does two things and only one is destructive: a read-only proof
over two columns of two partitions per candidate, and `--apply`. Scheduling the
first is not a smaller version of scheduling the second. **It is the thing that
makes the second get run**, which the two days after ADR-14 demonstrated: nobody
ran the prune, two daily crons each wrote another full 49.7 MB snapshot, and the
raw prefix went 214.1 MB to 323.4 MB.

`.github/workflows/retention.yml`, Mondays at 11:29 UTC, runs
`prune_raw.py --max-bytes 1000000000`. It reports, deletes nothing, and exits 4
if the zone is over the threshold. `--max-bytes` is off unless given, because a
threshold is a property of one bucket's allowance and not of the tool.
`make prune-raw PRUNE_ARGS="--max-bytes 1e9"` is the same check locally.

**Exit 3 outranks exit 4 when both hold**, and the ordering is load bearing
rather than tidy. Over budget asks the reader to run the apply. Unproven says a
snapshot dataset failed its supersession proof, which means `refresh` has become
a lie and the response is to stop pruning until it is understood. Reporting the
cheaper verdict as the headline would invite exactly the action the more serious
one forbids.

**Weekly and not daily**, because the proof costs about 34 seconds against the
bucket and daily would put a job that can go red in front of a human six times
more often than it has anything new to say. A check that cries wolf is the
failure this whole design is shaped around avoiding.

**What this does not claim.** It does not bound the zone. It bounds how long the
zone can be unbounded without someone being told, which is weaker and honest. If
nobody reads the red X the zone still fills; the difference is that the record
then shows three ignored failures rather than nothing at all.

### 7. The threshold arithmetic, now from a measured floor

The threshold is 1 GB against a 5 GB always-free allowance, and the number is
chosen so that a breach means one thing only.

| quantity | value | how it is known |
|---|---|---|
| floor, prune current | 173.8 MB | measured 2026-08-10, 173,751,272 bytes over 164 objects |
| delta growth | 4.86 MB/day | 311 and permits, the only terms that never stop growing |
| snapshot churn, prune neglected | about 50 MB/day | one full `business_locations` copy per bulk refresh |
| threshold | 1000 MB | `retention.yml` |
| allowance | 5000 MB | GCS always-free, single region |

From the floor, delta growth alone needs **170 days** to reach the threshold and
**993 days** to reach the allowance. Snapshot churn with nobody pruning reaches
the threshold in about **15**. **So this check cannot fire because the project
grew. It fires because the apply stopped being run**, which is the only sentence
it is trying to say, and it still leaves about 70 days of runway to the
allowance at the neglected rate, which is three more Mondays.

**The floor is two `business_locations` partitions plus the reference sets plus
every delta partition ever written, and only the last term grows.** ADR-17
estimated it at 170 MB and the estimate was good; 173.8 MB is the measurement,
and a restatement of the runway should start from that number and not from the
estimate. The floor is a function of the largest snapshot dataset's size rather
than of time, so it moves when a snapshot dataset joins the registry or when
`business_locations` grows, and the threshold's whole claim is arithmetic over
it.

### 8. The acceptance test

**Prune, then `make rebuild`, then every model's row count unchanged.** That is
ADR-4's claim and nothing weaker: a moved row count would mean staging was not
deduplicating what this whole argument assumes it was, which falsifies the
argument rather than revealing a bug in the tool, and the response is to stop
pruning until it is understood.

It held on 2026-08-07: 2.19 million raw rows deleted, 0 of 19 model row counts
moved. **It has not been run in its clean form since**, because the 2026-08-10
apply happened after that day's ingest, so the only available comparison spanned
a prune and a day of new data. What was shown instead, from surviving partitions
rather than from the tool's report, is that the deleted partitions contributed
no distinct `grain_key` to any staging count. Run the clean form at the next
apply, before the 09:17 UTC ingest.

## Consequences

**Buys.** One document answers "may this file be deleted", where it was four
plus a note. The four refusals travel with the rule instead of sitting in
superseded documents, which is what stops them being re-proposed by someone who
read only the live ones. The threshold argument now rests on a measured floor.
And the three exceptions can be stated in one table, which is the form the
sentence a new reader has to hold should have been in all along.

**Costs.** Consolidation loses the shape of the argument: ADR-14 is a decision
taken under a deadline with a bill approaching, ADR-16 is a five-day-old residue
found by two tools nobody was listening to, and ADR-17 is a revisit clause
firing two days after it was written. Those are three different kinds of event
and this document flattens them into one set of rules. The originals are kept
for exactly that reason and are worth opening when the question is why rather
than what: ADR-14, ADR-16 and ADR-17 stay in `docs/decisions/` marked
`superseded`, and ADR-4 is in `docs/archive/`. This ADR is also long, and a long ADR is read once and
skimmed thereafter; the tables in sections 2, 3 and 7 are written to be the part
that survives skimming.

**Lock-in.** Four superseded ADRs now depend on this one being kept current, so
an amendment to the raw zone's rules amends a document that four archived ones
point at. The next change to append-only should be an ADR-19 that amends this
one in the ordinary way, and the fifth-amendment rule starts counting again from
here. `grain_key` keeps its second job as the thing a deletion is proven
against, so changing one for a dataset with history in the zone changes what can
be proven about that history.

## Revisit if

- **A snapshot dataset starts failing its proof regularly.** That means the
  upstream stopped republishing wholesale and `refresh` has become a lie, which
  is a registry fix and not a prune fix. The scheduled proof is now what
  discovers this rather than whoever next types `make prune-raw`.
- **The 173.8 MB floor moves materially**, by a snapshot dataset joining the
  registry or by `business_locations` growing. The threshold's claim is
  arithmetic over the floor, and it is the arithmetic and not the constant that
  has to be re-derived.
- **The check fires and is ignored twice.** The red X is then not the signal it
  was meant to be, and the answer is a notification that reaches a person, or a
  reconsideration of `--apply` in a workflow with the risk stated plainly. Not a
  higher threshold.
- **The zone is over the threshold with nothing prunable.** The prune is not the
  lever in that case and neither is this check.
- **`make rebuild` after a prune ever moves a row count.** Stop pruning.
- **The zone becomes bounded by object count rather than bytes**, which is the
  condition for reopening compaction.
- **A dataset is cut and wanted back** inside the window where re-fetching would
  lose history. That argues for a hold period before the delete rather than
  against the delete, and the answer is to move the bytes out of the zone rather
  than to leave them in it.
- **Anyone proposes `--apply` in a workflow.** Twice refused, three times now.
  An ADR, not a YAML line.
