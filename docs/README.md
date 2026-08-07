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
| **PLAN-8 remaining-context-packs** | **active** | The `published` and `bigquery` packs. PLAN-6's residue, homed rather than carried. The published one is the point: the export is six marts and no staging models, so it is the first test of whether one prose file with `applies_to` beats three hand-kept documents. Starts with the audit already done, on the plan. |
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

`review-2026-07-31-scope-and-cloud.md` is an outside assessment that produced
PLAN-4, PLAN-5 and PLAN-6. It is not one of the four kinds below and can be
deleted once those plans are done. **All three are, as of 2026-08-07, so it is
now deletable.** It is fully harvested, and carries a table at the top saying
where each of its recommendations landed, so deleting it loses nothing recorded
nowhere else. Left in place for one reader to confirm that; the deletion is a
one-line commit whenever someone agrees.

`handoff-prompt.md` was the fourth non-canonical file: the session prompts for
work that had not run yet. It said it would delete itself when PLAN-6 closed,
and it did, on 2026-08-07. There is no standing handoff document; a session that
needs one writes it and the session that consumes it deletes it.

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
