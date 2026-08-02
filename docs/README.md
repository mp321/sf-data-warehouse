# docs

## Where things stand

Read this table first. It is the index a new session needs before opening
anything else in here.

| Plan | Status | What it is |
|---|---|---|
| PLAN-1 duckdb-parquet | done | Parquet raw zone and DuckDB default. Closed 2026-08-01 under PLAN-4 step 11, once the writer moved to the bucket. One Done-when box is left unticked on purpose; the plan says why. |
| PLAN-2 ingestion-lint | draft, nearly closed | Ruff exemptions gone. One item left, carried into PLAN-5 step 7. |
| PLAN-3 geography-and-marts | done | H3, boundaries, marts, published exports. Delivered 2026-07-31. |
| **PLAN-4 cloud-first-storage** | **active, one item left** | All eleven steps done 2026-08-01. BigQuery is built and proven row for row against DuckDB, both zones live in GCS and are now read *and written* there by the pipeline itself, the CI cache step is gone and PLAN-1 is closed. Left: commit, add the `GCS_BUCKET` secret, and get one scheduled `ingest` run green. |
| PLAN-5 narrow-and-polish | draft | Cut two datasets and one H3 resolution, one registry, pytest on the geometry code. After PLAN-4. |
| PLAN-6 context-pack | draft | The versioned context artifact with explicit refusal boundaries. Last, deliberately. |

| ADR | Status |
|---|---|
| ADR-1 warehouse targets | active |
| ADR-2 spatial strategy | superseded by ADR-6 |
| ADR-3 dataset scope | superseded by ADR-7 |
| ADR-4 raw zone layout | superseded by ADR-9 |
| ADR-5 H3 computation | active, superseded by ADR-10 when PLAN-5 lands |
| ADR-6 polygon membership | active |
| ADR-7 dataset scope, second pass | active, superseded by ADR-10 when PLAN-5 lands |
| ADR-8 published exports | active |
| ADR-9 cloud raw zone | active |

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
