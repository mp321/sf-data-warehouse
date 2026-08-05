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
| **PLAN-5 narrow-and-polish** | **active** | Cut two datasets and one H3 resolution, one registry, pytest on the geometry code. Steps 1, 2, 3 and 10 done 2026-08-04: the narrowing has landed and is recorded in ADR-10. Steps 5 and 6 followed the same day: `geometry.py` has direct pytest coverage, gating the dbt job in CI, and `spatial.py` is four files. Steps 4, 7 and 8 done 2026-08-05: one dataset registry rather than two, the `datasets.py` rename that closed PLAN-2, and a 50-run retention window on `meta_dbt_run_results`. Remaining is incrementality, the publish object count, and a final obsolescence sweep. |
| PLAN-6 context-pack | draft | The versioned context artifact with explicit refusal boundaries. Last, deliberately. |
| PLAN-7 pipeline-assurance | draft | Reconcile run manifests against the data; assert the BigQuery column sets against DuckDB's. PLAN-4 residue that had been carried forward three times. Small: two steps. |

| ADR | Status |
|---|---|
| ADR-1 warehouse targets | active |
| ADR-2 spatial strategy | superseded by ADR-6 |
| ADR-3 dataset scope | superseded by ADR-7 |
| ADR-4 raw zone layout | superseded by ADR-9 |
| ADR-5 H3 computation | active, amended by ADR-10 |
| ADR-6 polygon membership | active |
| ADR-7 dataset scope, second pass | superseded by ADR-10 |
| ADR-8 published exports | active |
| ADR-9 cloud raw zone | active |
| ADR-10 narrowed scope | active |

**ADR-5 is amended rather than superseded, and the distinction is load
bearing.** ADR-10 changed one line of it, the H3 resolution list. Its actual
decision, that cells are computed in Python and stored as BIGINTs because
BigQuery has no H3 function, is still a hard constraint. Filing it under
history would mean the next reader skips a live rule, which is the failure the
superseding convention exists to prevent. If a future ADR changes only part of
another, say so in the new ADR and leave the old one active.

`review-2026-07-31-scope-and-cloud.md` is an outside assessment that produced
PLAN-4, PLAN-5 and PLAN-6. It is not one of the three kinds below and can be
deleted once those plans are done.

## The three kinds of document

Three kinds of document live here, deliberately separate.

| Folder | Filename | What it is | Mutable? |
|---|---|---|---|
| `plans/` | `plan-<n>-<slug>.md` | Forward-looking. What we intend to do and in what order. | Yes, until `status: done` |
| `decisions/` | `adr-<n>-<slug>.md` | An ADR. One architectural decision, its tradeoffs and consequences. | No, once accepted |
| `dev-notes/` | `YYYY-MM-DD.md` | Append-only session log. What actually happened. | Append only |

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
