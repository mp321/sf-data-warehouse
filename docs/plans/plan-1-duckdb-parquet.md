---
status: active
date: 2026-07-30
related: [adr-1-warehouse-targets, adr-2-spatial-strategy]
---

# PLAN-1. Make DuckDB and Parquet the default path

## Goal

`git clone && make setup && make build` produces a fully built and tested
warehouse on a machine with no Google Cloud account, no service account key,
and no network access beyond pip. BigQuery keeps working on its schedule,
fed from the same Parquet files.

## Why now

ADR-1 is accepted but staged. Today the default dbt target is still
BigQuery, `ingest.py` still writes only to BigQuery, and the Parquet zone is
populated by an opt-in script rather than by the pipeline. Every day that gap
stays open, the BigQuery sandbox 60 day expiration keeps the raw zone
non-durable and keeps the silent-full-backfill failure mode live.

## Constraints

- Ingestion logic is not to be refactored casually. `ingest.py` works and its
  incremental watermark is subtle. Change it deliberately, in its own commit,
  with the dev note to match.
- Raw stays raw, per ADR-1. Parquet columns stay STRING. No typing, no
  renaming, no cleaning on the way out.
- Every model must still compile on both engines. New engine differences go
  behind macros in `dbt/macros/cross_engine.sql`.
- Raw tables stay append-only.

## Steps

1. Add `dbt-duckdb` to `requirements.txt` and confirm `make compile-duckdb`
   passes on a clean checkout. (Done 2026-07-30.)
2. Run `python ingestion/export_parquet.py --all` and confirm the files land
   in `data/` with the row counts BigQuery reports.
3. Add `external_location` meta to `raw_datasf` in
   `_datasf__sources.yml` and uncomment the DuckDB extensions block in
   `dbt/profiles.yml`. Confirm `dbt build --target duckdb` builds and tests
   `stg_datasf__311_cases` against the Parquet files.
4. Compare the DuckDB and BigQuery outputs of `stg_datasf__311_cases` row for
   row. Any disagreement is a cross-engine bug and must be fixed with a macro,
   not by tolerating a diff.
5. Decide where Parquet actually lives long term. `data/` is gitignored and
   therefore durable against BigQuery expiry but not against losing the
   laptop. Candidates: a GCS bucket on the free tier, or Cloudflare R2. This
   step needs its own ADR.
6. Teach `ingest.py` to write Parquet as its output of record, with BigQuery
   becoming a load target fed from those files. Own commit, own dev note.
7. Flip `target:` in `dbt/profiles.yml` from `dev` to `duckdb`, and change CI
   from `compile` to a full `dbt build --target duckdb`.
8. Delete `ingestion/export_parquet.py`. It exists only to bridge this gap.

## Out of scope

- The H3 precompute from ADR-2. It depends on this plan finishing but is
  not part of it.
- Adding datasets. ADR-3 blocks that until both core sources have a mart.
- Any mart model. This plan changes where data lives, not what it means.

## Done when

- [ ] `make build` succeeds on a clean clone with no credentials in the env.
- [ ] `stg_datasf__311_cases` returns identical rows on both targets.
- [ ] The scheduled BigQuery workflow still goes green untouched.
- [ ] Parquet files live somewhere that survives losing this machine.
- [ ] `ingestion/export_parquet.py` is deleted.

## Open questions

- Where does the Parquet actually live (step 5)? Needs an ADR.
- Does `get_watermark` read from Parquet or stay on BigQuery during the
  transition? Reading from both risks two watermarks drifting apart, which
  would double-load or silently skip rows.
- Partitioning scheme for the Parquet zone. One file per table is fine now and
  will not be at 311 volumes over multiple years.
