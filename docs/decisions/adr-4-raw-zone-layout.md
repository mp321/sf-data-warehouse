---
status: active
date: 2026-07-31
related: [adr-1-warehouse-targets, adr-3-dataset-scope]
---

# ADR-4. Raw zone layout, and loading as a separate step

## Context

ADR-1 decided that Parquet under `data/` is the durable raw zone and DuckDB is
the canonical engine, and deliberately did not implement it. Implementing it
forces three questions that ADR-1 left open, two of which are listed as open
questions in `docs/plans/plan-1-duckdb-parquet.md`:

- What the Parquet actually looks like on disk. "One file per table" was the
  placeholder and does not survive daily appends.
- Where the incremental watermark comes from once there are two places it
  could live. Reading it from both risks two watermarks drifting apart, which
  would double-load or silently skip rows.
- How a warehouse gets fed, now that ingestion is no longer writing directly
  into one.

There is also a fourth question nobody had asked, which surfaced while
measuring the first one: `ingest.py` paged the API with `$order=:updated_at`
and `$offset`. DataSF bulk-refreshes these datasets, so ties in `:updated_at`
are enormous, and a measured slice of `building_permits` had a single tie of
7,425 rows across two page boundaries. Ordering by a non-unique column and
paging by offset gives the API no reason to return a stable sequence, so rows
were being re-read and, more importantly, skipped. A 36,112-row slice
contained 35,918 distinct `record_id` values against an upstream dataset where
`record_id` is unique.

## Options considered

**A. One Parquet file per table, rewritten each run.** Simplest to read.
Against: rewriting is not appending, so it breaks the append-only guarantee
that lets staging deduplicate, and it makes every run cost the size of the
whole dataset.

**B. Append-only files, no partitioning.** Correct but unnavigable: nothing in
a directory of `part-0001.parquet` tells you when anything arrived, and
pruning or reprocessing a bad day means opening files to find out what is in
them.

**C. Hive partitioning by ingest date, with a run manifest per run.** Against:
a partition key that is not in the files, which every reader has to know to
ask for, and a second kind of file to keep in step with the first.

**D. Keep loading inside ingestion, writing Parquet as a side effect.** No new
command to learn. Against: an API pull and a warehouse write fail for
unrelated reasons, and coupling them means a warehouse outage loses the pull.

## Decision

Option C, plus splitting the load out as its own command.

**Layout.**

```
data/raw/<table>/ingest_date=YYYY-MM-DD/part-<run_id>-<seq>.parquet
data/raw/<table>/_runs/<run_id>.json
```

`<table>` is the raw table name from `ingestion/datasets.py`, so one directory
maps to exactly one dbt source on both warehouses. `ingest_date` is a hive
partition key and lives only in the directory name. Every read therefore goes
through `raw_zone.read_sql()`, which is the single place that knows to ask for
`hive_partitioning`, to keep the partition column a VARCHAR rather than let
DuckDB infer a DATE and break the all-STRING contract, and to union files by
name because Socrata omits null fields and so files genuinely differ in which
columns they contain.

**Run manifests.** Every run writes `_runs/<run_id>.json` recording the
watermark it resumed from, rows written, mode and status. This exists because
a run that finds nothing new writes no Parquet at all, so the data alone
cannot distinguish "ingestion ran and there was nothing to fetch" from
"ingestion has not run in three days". Those mean opposite things to whoever
reads `mart_pipeline_freshness`.

**The watermark comes from Parquet.** Not from BigQuery, and not from both.
This settles the PLAN-1 open question in the direction ADR-1 implies: the zone
is the record, so it holds the position.

**Ingestion and loading are separate commands.** `ingest.py` writes Parquet
and nothing else. `load.py` reads Parquet into DuckDB or BigQuery and touches
no API.

**Loading replaces rather than appends.** Each load rebuilds the whole raw
table from the files currently in the zone. This is what makes the step
idempotent with no bookkeeping: there is no record of which partitions were
already loaded, so there is nothing to get wrong, and a half-finished load
leaves nothing to reconcile. Load jobs are unbilled on BigQuery and local on
DuckDB, so the rewrite is free in money and cheap in time at current volumes.
Append-only continues to mean something where it matters, on the zone.

**Paging orders by `(:updated_at, :id)`.** A total order, so offset paging is
stable across requests.

## Consequences

**Buys.** A warehouse can be rebuilt from disk without re-fetching anything,
which is what makes `make rebuild` and the CI end-to-end job possible, and
what makes "drop the DuckDB file" a recovery procedure rather than an
incident. The zone is inspectable: partitions say when data arrived, manifests
say what each run did. Ingestion stops needing credentials at all, so the pull
half of the pipeline runs anywhere. And the tie bug is fixed, which is worth
more than the rest of this decision combined, because it was losing rows.

**Costs.** Two commands where there was one, and a `make load` that is easy to
forget between ingesting and building. A partition column that does not exist
inside the files, which is invisible until someone reads the Parquet with
something other than `read_sql()` and finds `ingest_date` missing. Manifests
are a second source of truth about the same runs and can disagree with the
data if one is deleted; the freshness mart reads counts from the data and
timings from the manifests specifically so that a disagreement shows up rather
than being averaged away. Replacing on load means load time grows with the
whole table rather than with the increment.

**Lock-in.** The all-STRING contract hardens further: the partition column is
kept a string purely so the raw tables have no exceptions, and anything that
starts relying on `ingest_date` being a date will break when it is fixed.
Choosing ingest date rather than event date as the partition key means
questions of the form "give me everything about January" always scan
everything, and repartitioning later means rewriting the zone.

**Still unsolved.** `data/raw` is durable against BigQuery's expiry and not
against losing the machine. The scheduled workflow caches it between runs on
GitHub, which is a mitigation with a 7-day eviction and a 10 GB ceiling, not
an answer. That remains step 5 of PLAN-1 and still needs its own ADR.

## Revisit if

- A raw table's full reload stops finishing in a time anyone will wait for,
  which is where replace-on-load stops being obviously right and the loader
  needs to track partitions after all.
- The raw zone outgrows the Actions cache, at which point the durable-storage
  ADR is no longer optional.
- Anyone needs to query the zone by event date rather than ingest date often
  enough to care about the scan, which is the argument for a second partition
  key and against this layout.
