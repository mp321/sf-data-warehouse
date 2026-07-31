---
status: active
date: 2026-07-30
related: [adr-2-spatial-strategy, adr-3-dataset-scope]
---

# ADR-1. Warehouse targets: DuckDB canonical, BigQuery secondary, Parquet raw

## Context

The project is BigQuery-first: `ingestion/ingest.py` streams Socrata records
into BigQuery, `dbt/profiles.yml` has one target, nothing touches local disk.

**Forcing constraint: BigQuery sandbox tables expire after 60 days.** The
expiry applies per table and per partition, and it deletes rather than
degrades. Two consequences matter:

- `get_watermark` reads `max(_socrata_updated_at)` from `raw_311_cases`, the
  only copy of ingested data. When that table expires the watermark resets to
  the dataset `start_date` and the next run silently full-backfills. No error,
  maximum cost, and Socrata does not guarantee full history on every endpoint.
- A fresh clone cannot build anything without a Google account, a project, a
  service account, and about an hour of setup.

Attaching billing removes the expiry at zero cost under the free tier. Worth
doing, but it does not address reproducibility, durability outside one vendor,
or local iteration speed.

## Options considered

**A. BigQuery only, attach billing.** One console action, no code changes.
Against: raw still lives in one vendor account, a fresh clone still cannot
build, every dbt iteration still costs a network round trip, and an account
suspension or rotated key reproduces the same silent backfill.

**B. DuckDB only, drop BigQuery.** Fast, free, reproducible, no credentials.
Against: discards the cloud-warehouse artifact the project exists to show, and
a single engine lets SQL drift engine-specific until it cannot move.

**C. Parquet raw, DuckDB canonical, BigQuery secondary.** Against: two
adapters, a class of engine-difference bugs, and Parquet files that still need
a durable home.

## Decision

Option C.

- Parquet under `data/` is the durable raw zone and ingestion's output of
  record.
- DuckDB is canonical: the default target for local development and CI. SQL
  that does not run on DuckDB is broken.
- BigQuery is a supported secondary target fed from the same Parquet, kept
  because a scheduled cloud-warehouse build is worth having.
- All models compile on both engines. Engine-specific functions go behind
  dispatch macros in `dbt/macros/cross_engine.sql` (`safe_cast` and `float64`
  are the first two).
- We also attach billing to the Google Cloud project.

## Consequences

**Buys.** Credential-free builds from a fresh clone, including on fork PRs in
CI. A raw zone that survives losing any single vendor account. dbt feedback in
milliseconds instead of round trips, which matters most now that marts are
being hand-written. The watermark reset stops being silent, because Parquet
outlives the BigQuery tables.

**Costs.** Two adapters means two ways for a model to break, and bugs that
only appear on the engine you are not currently using. Every engine-specific
expression needs a macro, which every contributor has to learn before writing
a cast. "Which target am I on" becomes a question that can be answered wrong.
`data/` is gitignored, so Parquet is durable against BigQuery expiry but not
against losing the laptop. That gap is tracked in
`docs/plans/plan-1-duckdb-parquet.md`.

**Lock-in.** Cross-engine SQL rules out BigQuery GEOGRAPHY and ML and DuckDB's
spatial extension, which is the pressure that produces ADR-2. Adopting either
engine's native spatial support later means breaking the cross-engine rule or
rewriting the models that used it. Parquet also fixes the raw zone as
all-STRING columns, because that is what `normalize_record` emits; widening to
real types later breaks every staging model at once.

## Implementation status

Accepted, staged. The default dbt target is still BigQuery and `ingest.py`
still writes only to BigQuery, deliberately left alone in the session that
introduced this ADR. `ingestion/export_parquet.py` is an opt-in
BigQuery-to-Parquet dump so the Parquet path can be exercised now. The DuckDB
target exists in `profiles.yml` and CI compiles against it. Flipping the
default is `docs/plans/plan-1-duckdb-parquet.md`.

## Revisit if

- Any raw table exceeds roughly 50 GB on disk, where single-node DuckDB stops
  being obviously right.
- The cross-engine macro layer passes about 10 macros, meaning the engines
  have diverged more than this decision assumed.
- Google removes the sandbox expiry, or paid billing becomes permanent and
  reproducibility stops being a goal. The first removes the forcing constraint
  and none of the other three reasons.
