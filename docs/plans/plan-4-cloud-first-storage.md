---
status: active
date: 2026-07-31
related: [plan-1-duckdb-parquet, adr-1-warehouse-targets, adr-4-raw-zone-layout, adr-8-published-exports]
---

# PLAN-4. Put the raw zone in the cloud and prove the BigQuery target

## Goal

The Parquet raw zone lives in GCS and survives losing this machine. BigQuery
reads it through external tables rather than holding a copy, so BigQuery
storage is marts only. `dbt build --target bigquery` has actually been
executed, and `stg_datasf__311_cases` has been compared row for row against
DuckDB.

## Why now

PLAN-1 cannot close. Steps 4 and 5 are the only ones still open and both are
here, and they have been open since the plan was written.

Two forcing facts. The scheduled workflow stands in for durable storage with
an Actions cache that evicts after 7 days; a miss makes `read_watermark`
return nothing and the run backfills from `start_date`, which for 311 is 8.8
million rows, unattended, on a shared runner. And `raw_datasf` in BigQuery
already holds 6.44 GB against a 10 GiB free-tier ceiling, in four tables the
current pipeline did not write and cannot reproduce.

The second fact is what turns "copy Parquet into BigQuery" from a working
arrangement into one that runs out of room. The raw contract is all-STRING, so
BigQuery's logical bytes are roughly ten times the Parquet on disk. Reading the
same files as external tables costs zero BigQuery storage and removes the
replace-on-load rewrite entirely.

## Constraints

- Models are not to change. Whatever `load.py` does per engine,
  `{{ source('raw_datasf', 'raw_311_cases') }}` must resolve the same way on
  both, per ADR-1.
- Raw stays raw and append-only, per ADR-4. Moving the zone changes where it
  is, not its layout, its all-STRING contract, or its watermark.
- Credential-free CI stays credential-free, per ADR-1. `make ci-build` must
  keep passing on a fork pull request with no secrets and no bucket. If a
  change makes CI need a bucket, the change is wrong.
- No credentials in the repo, per CLAUDE.md. `GCS_BUCKET` goes in
  `.env.example` as a placeholder and in `.env` for real.
- No commits or pushes by an agent. Leave changes in the working tree.

## Steps

1. **Human step. Confirm billing is attached to the GCP project.** If it is
   not, every table in `raw_datasf` expires 60 days after creation and the
   6.44 GB already there may be on a countdown. Attaching billing removes the
   expiry and leaves the free tier intact. Record the answer in the dev note
   either way, because the rest of this plan reads differently if the project
   is a sandbox. Done 2026-07-31: billing is attached, **and attaching it did
   not clear the expiry.** The `raw_datasf` dataset still carried
   `default_table_expiration_ms = 5184000000` from its sandbox days, which is a
   dataset property and outlives the sandbox, so every table `load.py`
   recreated in there was still on a 60 day countdown. Cleared with
   `bq update --default_table_expiration 0 raw_datasf`. Check this on any
   dataset the project creates later, `dbt_dev` included.
2. **Human step. Create a GCS bucket in `us-central1`, `us-west1` or
   `us-east1`.** Walked through in SETUP.md section 1.3, including the two
   settings that quietly break the free tier: soft delete, which is on by
   default for 7 days and keeps billing for deleted objects, and object
   versioning. Both matter here because `--full-refresh` swaps whole trees and
   `make publish` rewrites every mart directory. Done 2026-07-31:
   `gs://sf-data-bucket-mp`.
3. **Prove BigQuery before changing it.** Change no scope and no code first:
   run `make load-bigquery` then `make build-bigquery` against the local zone
   as it stands. Capture an order-independent content hash of
   `stg_datasf__311_cases` on both engines and compare. Any disagreement is a
   cross-engine bug and is fixed with a macro in `cross_engine.sql`, not
   tolerated as a diff (ADR-1). This closes PLAN-1 step 4 whichever way it
   goes, and it is the highest-credibility item in the repo.

   **Done 2026-07-31, and it failed the first time in four separate ways.** The
   first `dbt build --target bigquery` in the project's history came back
   `PASS=150 ERROR=2 SKIP=44`. All four defects and their fixes:

   - Three grain tests concatenated their key columns and cast one of them with
     `cast(x as varchar)`. `varchar` is DuckDB only. Fixed by writing
     `macros/generic/test_unique_combination.sql`, a group-by grain test that
     needs no cast, no separator and no engine-specific type name. Routing the
     cast through `x_type` was rejected: dbt derives a test's node name from the
     rendered expression, so the same test would be named `..._as_varchar_` on
     one target and `..._as_string_` on the other.
   - `accepted_values` on an integer column rendered `int64 in ('8','9','10')`,
     which DuckDB casts implicitly and BigQuery rejects. Fixed with
     `quote: false`.
   - `dim_supervisor_district` cast `boundary_id` straight to int. DataSF
     publishes it as "1.0", DuckDB's `try_cast` truncates that to 1 and
     BigQuery's `safe_cast` returns null, so all 11 districts were null on
     BigQuery while every local test passed. Fixed by using `x_safe_int`, the
     macro that already existed for exactly this and was not reached for.
   - `x_safe_int` itself was engine-dependent. Its comment asserted the values
     are integral in practice; permit 1752022162216 reports "2.5" stories, and
     float to int rounds on BigQuery and truncates on DuckDB, so one model
     returned 3 on one engine and 2 on the other. Fixed with an explicit
     `trunc()` inside the macro, which keeps DuckDB's existing answer.

   Both targets now build `PASS=196 ERROR=0`, and `scripts/parity-check.py`
   compares all six point staging models row for row. The three defects that
   were type errors are all defects `dbt compile --target bigquery` structurally
   cannot catch, which is the finding worth carrying: compiling renders Jinja
   and never asks the warehouse whether the type exists.
4. **Drop the orphaned BigQuery tables.** The four `raw_` tables in
   `raw_datasf` came from the pre-ADR-4 code path and are not reproducible
   from the zone. Do this after step 3, so the parity check has something to
   run against. Resolved 2026-07-31 by step 3 rather than by a drop: the four
   tables are the four `load.py` writes, so `WRITE_TRUNCATE` replaced all of
   them with current-zone content, and the 8.8M row pre-ADR-4 `raw_311_cases`
   is gone. Human decision, recorded because it is not reversible: the old rows
   were not snapshotted. `raw_datasf` still holds materialized raw tables, and
   emptying it of those is step 7, not this step.
   **Blocked as of 2026-07-31 on one IAM grant.** Steps 5 through 9 all need the
   service account to reach the bucket, and it cannot: `sf-dw-pipeline@` gets
   403 `storage.buckets.get` on `gs://sf-data-bucket-mp`. The bucket was created
   under a human identity and never shared with the pipeline identity. Unblock
   with, from an identity that owns the bucket:

       gcloud storage buckets add-iam-policy-binding gs://sf-data-bucket-mp \
         --member=serviceAccount:sf-dw-pipeline@sf-data-warehouse.iam.gserviceaccount.com \
         --role=roles/storage.objectAdmin

   Nothing below this line has been attempted, so treat the two routes in the
   open question as still open.
5. **Teach `raw_zone.py` a GCS prefix.** `RAW_ZONE_URI` alongside
   `RAW_ZONE_DIR`: when set, `read_sql()` reads
   `gs://<bucket>/raw/<table>/**/*.parquet` through DuckDB's httpfs with the
   same three load-bearing options, and `write_batch` writes there. Local
   remains the default. `read_sql()` stays the single reader; do not add a
   second one for the remote case.
6. **`ingest.py` writes to the zone URI when it is set.** `data/raw` becomes a
   local cache rather than the record. Say so in the module docstring, in
   CLAUDE.md's directory conventions, and in the Makefile header, all three of
   which currently describe `data/raw` as the durable zone.
7. **`load.py --target bigquery` creates external tables over the GCS prefix**
   instead of loading bytes. Same dataset name, same table names, same schema.
   `load.py --target duckdb` is unchanged. The derived zone follows the same
   pattern.
8. **Delete the cache step in `ingest.yml`.** The restore-key hack and the
   silent-backfill failure mode it mitigated both go away with it. Update the
   long comment at the top of that file, which is mostly about the cache.
9. **Run `make publish PUBLISH_DEST=gs://<bucket>/published` once, for real.**
   ADR-8's remote path stops being code that has never been executed. Verify
   the manifest lands last, as the ADR says it should.
10. **Write ADR-9.** It supersedes ADR-4, because ADR-4's "loading replaces
    rather than appends" no longer holds on BigQuery, and the rule in
    `docs/README.md` has no vocabulary for a partial supersede. Restate what
    carries forward: the directory layout, the run manifests, the watermark
    coming only from the zone, and the `(:updated_at, :id)` ordering. Set
    ADR-4 to `status: superseded` with `related` pointing at ADR-9 and change
    nothing else in it.
11. **Close PLAN-1.** Set `status: done`, tick steps 4 and 5, and point its
    remaining open question at ADR-9.

## Out of scope

- Dataset cuts, the H3 resolution change, and the Python test coverage. That
  is PLAN-5, and it will touch the same registry files, so sequence it after.
- The context pack. PLAN-6.
- Making `spatial.py` incremental. It is a real cost but it is a performance
  change, not a storage one, and it belongs with the other cleanup.

## Done when

- [x] `dbt build --target bigquery` has been executed against real data and
      the dev note records the result, pass or fail. Failed at 4 defects, all
      fixed, now `PASS=196 ERROR=0`.
- [x] `stg_datasf__311_cases` returns identical rows on both targets, or the
      disagreement has been fixed with a macro and re-verified. All six point
      staging models are identical, verified by `scripts/parity-check.py`.
- [ ] The zone is listed under `gs://<bucket>/raw/`, and a clone with no
      `data/` directory can run `make load && make build`. Blocked on the IAM
      grant above.
- [ ] `raw_datasf` in BigQuery contains no materialized raw table. Still four,
      now reproducible from the zone rather than orphaned. Needs step 7.
- [ ] `ingest.yml` has no cache step and one scheduled run has gone green.
- [x] `make ci-build` still passes with no credentials and no bucket.
- [ ] `published/` has been written to a real bucket once.
- [ ] ADR-9 written, ADR-4 superseded, PLAN-1 closed. PLAN-1 step 4 is closed;
      step 5 is what ADR-9 is about and it is still open.
- [x] New, added by doing the work: a repeatable cross-engine row comparison
      exists rather than a one-off hash captured in a dev note.

## Open questions

- ~~Does DuckDB's httpfs read of GCS work with the service account directly?~~
  Answered 2026-07-31: **it does not.** DuckDB's httpfs extension reaches GCS
  through the S3-compatible interoperability layer and authenticates with
  `CREATE SECRET (TYPE gcs, KEY_ID ..., SECRET ...)`, which takes HMAC keys,
  not a service account JSON. That leaves two routes and step 5 has to pick
  one:

  **HMAC interoperability keys.** Native httpfs, implemented in C++, and the
  fast option. Costs a second credential to create, store in `.env`, add as a
  GitHub secret, and rotate, in a project whose hard constraint is that no
  credential reaches the repo. It also means the service account grant in
  SETUP.md 1.3 is no longer the only thing controlling access.

  **fsspec plus `gcsfs`, registered with `duckdb.register_filesystem`.** Reuses
  `GOOGLE_APPLICATION_CREDENTIALS`, so there is no second credential and the
  IAM story stays in one place. Costs performance, because fsspec filesystems
  are Python rather than C++, and it conflicts with having httpfs loaded, so
  the two cannot be mixed in one connection.

  Recommendation, to be confirmed by measurement rather than accepted: start
  with fsspec, because one credential is worth more here than throughput on a
  zone measured in hundreds of megabytes. Measure a full `load.py` run both
  ways before committing to it in ADR-9, and record both numbers there. If
  fsspec turns out to be minutes rather than seconds slower, HMAC wins and the
  second credential is the price.

  Note this only affects DuckDB reading the zone. BigQuery external tables
  read GCS with the querying identity's own credentials and need neither.
- Where do the run manifests live once the zone is remote? They are JSON
  beside the Parquet today and `runs_read_sql()` globs them locally. The
  simplest answer is that they move with the zone, but it needs checking that
  `read_json` over `gs://` behaves the same.
- Does anything reconcile the run manifests against the data? Carried forward
  from PLAN-1 and still true: nothing does.
