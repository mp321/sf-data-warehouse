---
status: active
date: 2026-07-30
related: [adr-1-warehouse-targets, adr-2-spatial-strategy, plan-4-cloud-first-storage]
---

# PLAN-1. Make DuckDB and Parquet the default path

Seven of eight steps are done. Step 4 (BigQuery row-for-row parity) closed
2026-07-31 under PLAN-4 step 3. Step 5 (where the Parquet actually lives) is
the only one left, it is PLAN-4 steps 5 to 10, and closing this plan is PLAN-4
step 11. Nothing new should be added here.

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
2. ~~Run `python ingestion/export_parquet.py --all`~~ Superseded by step 6:
   `ingest.py` writes Parquet directly, so there is nothing to export.
   (Dropped 2026-07-31.)
3. ~~Add `external_location` meta to `raw_datasf`~~ Rejected 2026-07-31. It
   makes the DuckDB and BigQuery source definitions structurally different,
   which defeats the point of ADR-1. `load.py` materialises a `raw_datasf`
   schema on both engines instead, and the models are identical. Reasoning is
   in the header of `dbt/profiles.yml`.
4. Compare the DuckDB and BigQuery outputs of `stg_datasf__311_cases` row for
   row. Any disagreement is a cross-engine bug and must be fixed with a macro,
   not by tolerating a diff. (Done 2026-07-31 under PLAN-4 step 3. It found
   four cross-engine defects, three of them in yml test definitions rather than
   in model SQL, and one in the `x_safe_int` macro itself. All fixed, both
   targets green, and the comparison is now `scripts/parity-check.py` rather
   than a hash pasted into a dev note.)
5. Decide where Parquet actually lives long term. `data/` is gitignored and
   therefore durable against BigQuery expiry but not against losing the
   laptop. Candidates: a GCS bucket on the free tier, or Cloudflare R2. This
   step needs its own ADR. (Still open. The scheduled workflow now caches the
   zone between runs, which is a mitigation with a 7 day eviction and a 10 GB
   ceiling, not an answer. See ADR-4.)
6. Teach `ingest.py` to write Parquet as its output of record, with BigQuery
   becoming a load target fed from those files. (Done 2026-07-31, ADR-4. The
   load became its own command, `ingestion/load.py`, rather than a mode of
   ingestion.)
7. Flip `target:` in `dbt/profiles.yml` from `dev` to `duckdb`, and change CI
   from `compile` to a full `dbt build --target duckdb`. (Done 2026-07-31. The
   BigQuery output was renamed from `dev` to `bigquery` at the same time. CI
   also compiles against BigQuery, which needs no credentials and catches
   dialect leaks the DuckDB build cannot see.)
8. Delete `ingestion/export_parquet.py`. (Done 2026-07-31.)

## Out of scope

- The H3 precompute from ADR-2. It depends on this plan finishing but is
  not part of it.
- Adding datasets. ADR-3 blocks that until both core sources have a mart.
- Any mart model. This plan changes where data lives, not what it means.

## Done when

- [x] `make build` succeeds on a clean clone with no credentials in the env.
      `make ci-build` does the whole pipeline from fixtures; `make ingest`
      needs network but no credentials.
- [x] `stg_datasf__311_cases` returns identical rows on both targets. Verified
      2026-07-31 after fixing four defects, and the same check passes on all six
      point staging models. The gap this bullet described was real: CI's
      BigQuery compile proved the SQL was valid there and three of the four
      defects were type errors that only executing could find.
- [ ] The scheduled BigQuery workflow still goes green untouched. It was not
      left untouched: `ingest.yml` had to change, because ingestion no longer
      writes to BigQuery, and a workflow that silently does nothing is worse
      than one that fails.
- [ ] Parquet files live somewhere that survives losing this machine.
- [x] `ingestion/export_parquet.py` is deleted.

## Open questions

- Where does the Parquet actually live (step 5)? Needs an ADR. Still the one
  thing blocking this plan from closing.
- ~~Does `get_watermark` read from Parquet or stay on BigQuery?~~ Answered by
  ADR-4: Parquet, and only Parquet. The zone is the record, so it holds the
  position, and there is never a second watermark to drift from.
- ~~Partitioning scheme for the Parquet zone.~~ Answered by ADR-4:
  hive-partitioned by ingest date, one file per buffered batch. Note the
  choice of ingest date over event date is itself a tradeoff recorded there.
- New, from doing the work: does anything reconcile the run manifests against
  the data? Today nothing does, and `mart_pipeline_freshness` is built so a
  disagreement is visible rather than averaged away, which is not the same as
  catching it.
