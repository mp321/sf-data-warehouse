---
status: done
date: 2026-07-30
related: [adr-1-warehouse-targets, adr-2-spatial-strategy, adr-9-cloud-raw-zone, plan-4-cloud-first-storage]
---

# PLAN-1. Make DuckDB and Parquet the default path

**Closed 2026-08-01 under PLAN-4 step 11.** All eight steps are done. Step 4
(BigQuery row-for-row parity) closed 2026-07-31 under PLAN-4 step 3, and step 5
(where the Parquet actually lives) closed 2026-08-01: ADR-9 put both zones in
GCS and PLAN-4 step 6 moved the writer there, so the bucket is written by the
pipeline rather than synced by hand.

One acceptance criterion below is deliberately left unticked rather than
massaged; see the closing note. Nothing new should be added here.

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
   step needs its own ADR. (**Done 2026-08-01. GCS, ADR-9.** Answered in two
   halves on the same day: the reader landed under PLAN-4 step 5, DuckDB
   through fsspec and BigQuery through external tables, and the writer under
   PLAN-4 step 6, so `ingest.py` appends Parquet straight to the bucket and
   `spatial.py` writes the derived zone there. The zone is no longer synced by
   hand and no longer depends on this laptop. Local stays the default and CI
   stays credential-free, which ADR-1 required and which `RAW_ZONE_DIR` beating
   `RAW_ZONE_URI` is what enforces.)
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
- [ ] The scheduled BigQuery workflow still goes green untouched. **Left
      unticked on purpose.** The premise stopped being true on 2026-07-31 and
      is now thoroughly false: `ingest.yml` had to change because ingestion no
      longer writes to BigQuery, and it changed again on 2026-08-01 when the
      cache step went and the zones became bucket prefixes. A workflow that
      silently does nothing is worse than one that fails, so "untouched" was the
      wrong thing to ask for. What is worth asking for is that it goes green
      after all that, which needs a scheduled run and is tracked in PLAN-4's own
      Done-when list rather than here.
- [x] Parquet files live somewhere that survives losing this machine.
      `gs://<bucket>/raw` and `/derived`, read and written directly by the
      pipeline (ADR-9, PLAN-4 step 6). Verified 2026-08-01 by ingesting 395,947
      rows into the bucket and rebuilding the warehouse from it alone.
- [x] `ingestion/export_parquet.py` is deleted.

## Closing note

This plan closes with one Done-when box unticked, which is the honest state
rather than an oversight. Everything the plan set out to do is done; that one
bullet asked for evidence about a workflow the plan itself made obsolete, and
the evidence that replaced it belongs to PLAN-4. The remaining substantive
question this plan raised, whether anything reconciles the run manifests against
the data, is unanswered and carried to PLAN-4 rather than closed here.

## Open questions

- ~~Where does the Parquet actually live (step 5)?~~ **Answered by ADR-9.** GCS,
  read through fsspec by DuckDB and as external tables by BigQuery, and written
  there by `ingest.py` and `spatial.py` themselves since PLAN-4 step 6. Read
  ADR-9 rather than this plan for the reasoning: it records why fsspec beat
  httpfs on credentials, why a synced local cache was rejected outright, and
  what the arrangement costs in read time and in a dependency ceiling. The
  qualifier this bullet used to carry, that the writer was still local, is gone.
- ~~Does `get_watermark` read from Parquet or stay on BigQuery?~~ Answered by
  ADR-4: Parquet, and only Parquet. The zone is the record, so it holds the
  position, and there is never a second watermark to drift from.
- ~~Partitioning scheme for the Parquet zone.~~ Answered by ADR-4:
  hive-partitioned by ingest date, one file per buffered batch. Note the
  choice of ingest date over event date is itself a tradeoff recorded there.
- New, from doing the work: does anything reconcile the run manifests against
  the data? Today nothing does, and `mart_pipeline_freshness` is built so a
  disagreement is visible rather than averaged away, which is not the same as
  catching it. **Carried to PLAN-4, still open there.** It outlived this plan
  because it is about trusting the zone's metadata, not about where the zone
  lives, and moving the zone to a bucket changed nothing about it.
