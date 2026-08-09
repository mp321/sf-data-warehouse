# docs

## Where things stand

Read this table first. It is the index a new session needs before opening
anything else in here.

| Plan | Status | What it is |
|---|---|---|
| PLAN-1 duckdb-parquet | done | Parquet raw zone and DuckDB default. Closed 2026-08-01 under PLAN-4 step 11, once the writer moved to the bucket. One Done-when box is left unticked on purpose; the plan says why. |
| PLAN-2 ingestion-lint | done | Ruff exemptions gone 2026-07-31. Closed 2026-08-05 under PLAN-5 step 7, when `ingestion/datasets.py` became `dataset_registry.py` and stopped shadowing a PyPI package name. |
| PLAN-3 geography-and-marts | done | H3, boundaries, marts, published exports. Delivered 2026-07-31. |
| PLAN-4 cloud-first-storage | done | Parquet raw zone in GCS, BigQuery on external tables and proven row for row against DuckDB, the CI cache step gone. Closed 2026-08-03 when `ingest.yml` went green on a runner against the bucket. Its residue is now homed: assurance items in PLAN-7, the publish object count in PLAN-5 step 12, the `dbt_dev` expiry question closed by measurement. |
| PLAN-5 narrow-and-polish | done | Cut two datasets and one H3 resolution, one registry, pytest on the geometry code. Closed 2026-08-05 by step 13, the obsolescence sweep, which found the stale documents were USER-NOTES.md and SETUP.md rather than anything in the code. Recorded in ADR-10 (scope), ADR-11 (the derived zone's code stamp) and ADR-12 (the published layout). One Done-when box was resolved by judgement rather than met; the plan says which and why. |
| PLAN-6 context-pack | done | The versioned context artifact with explicit refusal boundaries. Last, deliberately. Step 1 done 2026-08-05: `docs/specs/context-pack.md`, written before the generator, which answers the plan's open question as one pack per target. Steps 2 and 3 done 2026-08-06: `tools/context_pack/` generates the DuckDB pack and the four rules the spec says must fail the build fail it. Closed 2026-08-07 by step 4 and ADR-13: CI checks the committed pack against the fixture warehouse and generates nothing, because the only warehouse a credential-free runner can build has seven-row tables. Both open questions are settled in the ADR, and the second one amended the spec: the traps block is in the compact markdown, because a trap is a disclosure with no condition. Closed with one target of three; the other two are PLAN-8. |
| PLAN-9 raw-zone-retention | done | Bound what the two buckets accumulate. Closed 2026-08-07, all eight steps, recorded in ADR-14. The bucket went from 563.5 MB over 3,128 objects to 249.6 MB over 196: 297.8 MB of superseded `business_locations` snapshots off the raw prefix, and 2,880 objects of the pre-ADR-12 layout off the published one. The acceptance test is the whole safety argument and it held: 0 of 19 model row counts moved after deleting 2.19 million raw rows. Both open questions answered in the ADR, and the object count in the plan's own measurement was wrong (236, not 329), corrected in place. **Its two loose ends closed 2026-08-09** as ADR-16, the two datasets ADR-10 cut deleted from the zone, and ADR-17, the proof on a schedule and the deletion still by hand. |
| PLAN-8 remaining-context-packs | done | The `published` and `bigquery` packs. PLAN-6's residue, homed rather than carried. Closed 2026-08-07 at two packs rather than three: the published pack earned its cost, because the export is six marts against nineteen models and twelve prose entries could not resolve against it, and ADR-15 struck the bigquery one for the opposite reason, that its model set is duckdb's word for word. Both open questions answered, the second only once the second pack existed. |
| PLAN-7 pipeline-assurance | done | Reconcile run manifests against the data; assert the BigQuery column sets against DuckDB's. PLAN-4 residue that had been carried forward three times. Closed 2026-08-05, both steps the same day. Step 2 was overtaken on the way: the column-set disagreement it was written to detect turned `make build-bigquery` red first, so `parity-check.py --columns` was built against a live defect. Step 1 is `ingestion/check_runs.py` and `make check-runs`, and it answered its own open question against the precedent: a separate file from `check_derived.py`, because the reader and the moment it runs are both different. |

| ADR | Status |
|---|---|
| ADR-1 warehouse targets | active |
| ADR-2 spatial strategy | superseded by ADR-6 |
| ADR-3 dataset scope | superseded by ADR-7 |
| ADR-4 raw zone layout | superseded by ADR-9 |
| ADR-5 H3 computation | active, amended by ADR-10 and ADR-11 |
| ADR-6 polygon membership | active |
| ADR-7 dataset scope, second pass | superseded by ADR-10 |
| ADR-8 published exports | active, amended by ADR-12 |
| ADR-9 cloud raw zone | active |
| ADR-10 narrowed scope | active |
| ADR-11 derived zone code stamp | active |
| ADR-12 published export layout | active |
| ADR-13 context pack format | active |
| ADR-14 raw zone retention | active, amends ADR-4, amended by ADR-16 and ADR-17 |
| ADR-15 bigquery pack declared, not generated | active, amends the context-pack spec |
| ADR-16 cut datasets leave the zone | active, amends ADR-4 |
| ADR-17 scheduled retention proof | active, amends ADR-14 |

**Amended rather than superseded, and the distinction is load bearing.** ADR-10
changed one line of ADR-5, the H3 resolution list, and ADR-11 changed what a
re-run of the spatial step recomputes; ADR-5's actual decision, that cells are
computed in Python and stored as BIGINTs because BigQuery has no H3 function, is
still a hard constraint. ADR-12 reverses one bullet of ADR-8, the month
partitioning, and leaves the other eight standing. Filing either under history
would mean the next reader skips a live rule, which is the failure the
superseding convention exists to prevent. If a future ADR changes only part of
another, say so in the new ADR, add a note at the top of the old one pointing at
it, and leave the old one active.

**ADR-14 and ADR-16 amend an already-superseded ADR, and that is why neither
adds a note at the top of the one it amends.** They add the second and third
exceptions to ADR-4's append-only rule. ADR-4 is `superseded` by ADR-9, which
supersedes it on two points and says in its own first paragraph that ADR-4 is
otherwise still the description of the zone, and the append-only rule is one of
the things ADR-9 explicitly carries forward. So the live rule lives in two
documents and the amendments are recorded in two more. Read ADR-4, ADR-9, ADR-14
and ADR-16 as one conversation about what the raw zone is; the convention above,
where the amended ADR carries a pointer, was written for an `active` one and
editing an accepted ADR to add a pointer is the thing the immutability rule
forbids.

**ADR-17 is the ordinary case of that convention and shows what it costs.** It
amends ADR-14, which is `active`, so ADR-14 carries a note at the top and its
decision text is untouched: the note says which line moved and why, and the
sentence it moved, "By hand, not on a schedule", still reads as written
underneath. The three exceptions to append-only are now spread over four
documents plus a note, which is the price of immutability and is worth paying
once. It is also the signal to watch: a fifth document amending the same rule
would mean the rule wants restating in one place under a new ADR that supersedes
the lot, rather than another amendment.

**Everything in `docs/` is one of the four kinds below.** The two files that
were not, the 2026-07-31 outside review and `handoff-prompt.md`, were deleted on
2026-08-07 when PLAN-6 closed, which is the condition each of them named for its
own deletion. Both are in git history. There is no standing handoff document: a
session that needs one writes it, and the session that consumes it deletes it.

## The four kinds of document

Four kinds of document live here, deliberately separate.

| Folder | Filename | What it is | Mutable? |
|---|---|---|---|
| `plans/` | `plan-<n>-<slug>.md` | Forward-looking. What we intend to do and in what order. | Yes, until `status: done` |
| `decisions/` | `adr-<n>-<slug>.md` | An ADR. One architectural decision, its tradeoffs and consequences. | No, once accepted |
| `dev-notes/` | `YYYY-MM-DD.md` | Append-only session log. What actually happened. | Append only |
| `specs/` | `<slug>.md` | The contract a generated artifact is built against. What it must contain and what the generator is verified by. | Yes |

A spec is not an ADR and the difference is worth stating, since both are
normative. An ADR records one decision, its alternatives and its consequences,
and it is immutable because the record of what we believed is the point. A spec
describes a thing that is still being built and is amended when the thing has to
change; the decision behind it, and what it left out, is what the ADR is for.
`specs/context-pack.md` is the first one, written under PLAN-6 step 1 and
deliberately ahead of its generator.

## Why plans and dev notes are separate

A plan is intent, a dev note is incident. Folding what happened into the plan
makes it unexecutable: a reader six months later cannot tell which lines are
still instructions and which are history.

## Numbering and referencing

Numbers are allocated in order and never reused. Refer to documents in prose
as `ADR-1` and `PLAN-2`, and in `related:` frontmatter by full slug, for
example `adr-1-warehouse-targets`. The prefix is what keeps ADR-1 and PLAN-1
from being confused.

`TEMPLATE.md` in each folder is the template and carries no number.

## Changing an accepted ADR

You do not. Write a new ADR that supersedes it:

1. Create `docs/decisions/adr-<n>-<slug>.md` with `related: [<old-slug>]`.
2. In the old ADR, change only `status:` to `superseded` and add the new ADR
   to its `related` list. Leave the decision text alone.

The record of what we believed and why is worth more than a tidy file.

## Frontmatter

Plans and ADRs carry:

```yaml
---
status: draft | active | done | superseded
date: YYYY-MM-DD
related: []
---
```

Dev notes carry no frontmatter. The filename is the date.

## Style

Concise. One or two sentences per point unless the nuance is load bearing. No
emojis, no em or en dashes.
