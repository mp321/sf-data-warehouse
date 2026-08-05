---
status: done
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
   **Unblocked 2026-08-01.** `roles/storage.objectAdmin` was granted to
   `sf-dw-pipeline@` and every object read and write works. Note the grant looks
   like it failed if you test it wrong: objectAdmin deliberately does not include
   `storage.buckets.get`, so `client.get_bucket()` still returns 403 while
   `client.bucket()` plus object operations succeed. `publish/export.py` already
   used the latter. The command that granted it, for the record:

       gcloud storage buckets add-iam-policy-binding gs://sf-data-bucket-mp \
         --member=serviceAccount:sf-dw-pipeline@sf-data-warehouse.iam.gserviceaccount.com \
         --role=roles/storage.objectAdmin

   Steps 6 and 8 below are the two that remain.
5. **Teach `raw_zone.py` a GCS prefix.** `RAW_ZONE_URI` alongside
   `RAW_ZONE_DIR`: when set, `read_sql()` reads
   `gs://<bucket>/raw/<table>/**/*.parquet` through DuckDB's httpfs with the
   same three load-bearing options, and `write_batch` writes there. Local
   remains the default. `read_sql()` stays the single reader; do not add a
   second one for the remote case.

   **Done 2026-08-01, reads only.** fsspec rather than httpfs, decided by
   measurement and recorded in ADR-9. The new `ingestion/remote.py` owns the
   local-or-bucket question and the authentication, `read_sql()` is still the
   single reader in both zone modules, and `DERIVED_ZONE_URI` got the same
   treatment because `load.py` and BigQuery both need the derived zone too.
   `write_batch`, `write_table` and `write_manifest` raise `NotImplementedError`
   on a remote root rather than writing a local directory called `gs:`, which is
   what `pathlib` does with a URI. Writing remotely is step 6 and is deliberately
   still open.
6. **`ingest.py` writes to the zone URI when it is set.** `data/raw` becomes a
   local cache rather than the record. Say so in the module docstring, in
   CLAUDE.md's directory conventions, and in the Makefile header, all three of
   which currently describe `data/raw` as the durable zone.

   **Done 2026-08-01, and the step's own wording had to be corrected.** `data/raw`
   is not a cache. A run writes to the configured zone and to nothing else, so a
   remote run leaves `data/` holding whatever the last local run put there. The
   alternative, writing both, is ADR-9's rejected option D: two copies with
   nothing to detect divergence, and worse than the ADR's version because only
   the machines that happen to run `make ingest` would have the second copy.
   Recorded that way in the three places named plus `.env.example`.

   The writer is in `raw_zone.write_batch`, `raw_zone.write_run_manifest`,
   `derived_zone.write_table` and `derived_zone.write_manifest`, all four of
   which used to raise `NotImplementedError` on a remote root. `remote.py` grew
   `open_write`, `write_text` and `read_text`, so it is still the only module
   that knows what a bucket is. Three findings:

   - **`type=Path` on `--raw-root` was the silent-backfill bug, sitting in the
     open.** `Path("gs://b/raw")` collapses to `gs:/b/raw`, so `read_watermark`
     found nothing, returned None, and `resolve_watermark` fell through to
     `start_date`: 8.8 million rows for 311, no error. Harmless while writes
     raised, live the moment they stopped. Fixed with `remote.zone_root`, used by
     all four CLIs, and `resolve_watermark` now announces a backfill instead of
     performing one quietly.
   - **`--full-refresh` cannot be done atomically on GCS, so it is refused.**
     Locally it renames a finished tree into place. Object storage has no rename
     and no multi-object transaction, so the same swap is a delete of everything
     followed by a copy of everything, and an interruption inside that window
     loses the raw zone outright. Refusing before the first API call, with the
     local-then-upload recipe in the message, beat doing it non-atomically.
   - **Nothing about the layout had to change.** Hive partitioning, the
     `_runs/` directory, `union_by_name` across files with different column
     sets, and BIGINT round trips all work identically on the bucket, verified
     against a scratch prefix before the real zone was touched.
7. **`load.py --target bigquery` creates external tables over the GCS prefix**
   instead of loading bytes. Same dataset name, same table names, same schema.
   `load.py --target duckdb` is unchanged. The derived zone follows the same
   pattern.

   **Done 2026-08-01.** 15 external tables, every row count identical to DuckDB,
   and `dbt build --target bigquery` green on them. BigQuery storage went from
   8.02 GB to 40.96 MB. Two things had to be discovered by trying the
   alternative: the source URI must be `<table>/*.parquet` and not `<table>/*`,
   because the run manifests live inside the table directory and a bare wildcard
   fails the whole table with "Incompatible partition schemas"; and hive
   partitioning needs `mode=STRINGS` or BigQuery infers DATE for `ingest_date`
   and puts a non-STRING column in an all-STRING raw table. One exception:
   `raw_ingest_runs` stays materialized, because the manifests are JSON arrays
   rather than newline-delimited JSON.
8. **Delete the cache step in `ingest.yml`.** The restore-key hack and the
   silent-backfill failure mode it mitigated both go away with it. Update the
   long comment at the top of that file, which is mostly about the cache.

   **Done 2026-08-01.** The cache step is gone and the header is rewritten around
   what replaced it, which is not a smaller mitigation but the absence of the
   problem: there is no restore to miss. Three consequential edits beyond the
   deletion. The job's `env:` no longer sets `RAW_ZONE_DIR`, because DIR beats
   URI and leaving it would have quietly sent the whole workflow back to a local
   zone on an ephemeral disk. It sets `RAW_ZONE_URI` and `DERIVED_ZONE_URI` from
   a new `GCS_BUCKET` repository secret, so SETUP.md now asks for four secrets
   rather than three. And the service account key is written first rather than
   just before the BigQuery load, because ingestion itself now needs the bucket.
   Not run: a scheduled run has not happened, so the Done-when box below stays
   open until one does.
9. **Run `make publish PUBLISH_DEST=gs://<bucket>/published` once, for real.**
   ADR-8's remote path stops being code that has never been executed. Verify
   the manifest lands last, as the ADR says it should.

   **Done 2026-08-01.** 2,885 objects uploaded, and `published/manifest.json`
   confirmed as the last object written, so ADR-8's ordering requirement holds in
   practice and not just in the code. The number is the finding: 2,885 objects is
   2,885 Class A operations against a free tier of 5,000 per month, so a second
   publish in the same month leaves the free tier. It also took 6 minutes 39 for
   17 MB, because the cost is per object and the H3 mart partitions into
   thousands of small ones. Either batch the upload or coarsen the partitioning
   before this runs on a schedule.
10. **Write ADR-9.** It supersedes ADR-4, because ADR-4's "loading replaces
    rather than appends" no longer holds on BigQuery, and the rule in
    `docs/README.md` has no vocabulary for a partial supersede. Restate what
    carries forward: the directory layout, the run manifests, the watermark
    coming only from the zone, and the `(:updated_at, :id)` ordering. Set
    ADR-4 to `status: superseded` with `related` pointing at ADR-9 and change
    nothing else in it.
11. **Close PLAN-1.** Set `status: done`, tick steps 4 and 5, and point its
    remaining open question at ADR-9.

    **Done 2026-08-01.** `status: done`, steps 4 and 5 both closed, and the
    "where does the Parquet live" question now points at ADR-9 with the "but the
    writer is local" qualifier removed. Two things were not tidied away. Its
    Done-when box asking that the scheduled workflow "still goes green
    untouched" is left unticked on purpose, because the premise is false twice
    over: `ingest.yml` changed under PLAN-1 and changed again under step 8 above,
    and a green run after those changes is tracked here rather than there. And
    the run-manifest reconciliation question is marked carried to this plan
    rather than closed, because it is still true that nothing reconciles them.

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
- [x] The zone is listed under `gs://<bucket>/raw/`, and a clone with no
      `data/` directory can run `make load && make build`. Verified 2026-08-01
      into a scratch warehouse: 37 second load from the bucket, `PASS=196`.
- [x] `raw_datasf` in BigQuery contains no materialized raw table. Nine
      external, plus `raw_ingest_runs`, which cannot be external and is 19 rows
      of metadata rather than a raw DataSF table.
- [x] `ingest.yml` has no cache step and one run has gone green. The cache step
      is gone (step 8), and the work is committed and on `main` as of
      2026-08-02. Both preconditions were met on 2026-08-03: the `GCS_BUCKET`
      repository secret was added holding the bucket name alone, and
      `SOCRATA_APP_TOKEN` was deleted outright rather than replaced, which is
      the correct resolution of the two the plan offered, because the token is
      optional and an invalid one is worse than none.

      **Ticked on a `workflow_dispatch` run, not a `schedule` one**, and the
      distinction is recorded rather than smoothed over. The two triggers run
      the same job with the same `env:` and the same secrets, so everything
      this box was written to prove is proven: the bucket is reachable from a
      runner, the watermark resolves from it, ingestion resumes rather than
      backfills, and the whole path is green without a cache. What a manual run
      cannot prove is that the `17 9 * * *` cron entry itself fires, which is a
      property of the schedule line and of `main` having the workflow, not of
      this plan's work. First scheduled run is due 09:17 UTC on 2026-08-04 and
      is a thing to look at, not a thing to block on.
- [x] `make ci-build` still passes with no credentials and no bucket. Re-verified
      2026-08-01 after the writer landed, in a clean shell and in one with `.env`
      sourced, and the write path was asserted directly: with both DIR and URI
      set, `write_batch` and `write_run_manifest` land on disk.
- [x] `published/` has been written to a real bucket once.
- [x] ADR-9 written, ADR-4 superseded, PLAN-1 closed under step 11.
- [x] New, added by doing the work: the pipeline writes the zone it reads.
      `ingest.py` appended 395,947 rows to `gs://<bucket>/raw` and `spatial.py`
      wrote all six derived tables to `gs://<bucket>/derived`, then a warehouse
      built from those two prefixes alone came out `PASS=196 ERROR=0`.
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

  **Answered 2026-08-01: fsspec, recorded in ADR-9.** httpfs was tested and
  cannot use the service account at all: `provider credential_chain` is rejected
  for `type gcs` and a bare gcs secret 403s, so it is HMAC or nothing. fsspec
  costs 8.83 seconds against 1.48 local for a full scan of the three largest
  tables, and 37 seconds for a whole `load.py` run from the bucket, which is not
  worth a second credential. The real cost turned out to be a version ceiling
  rather than throughput: `gcsfs` 2026.x and `dbt-bigquery` cannot coexist, so
  `requirements.txt` pins `gcsfs<2026` with the reason written down.

  Note this only affects DuckDB reading the zone. BigQuery external tables
  read GCS with the querying identity's own credentials and need neither.
- ~~Where do the run manifests live once the zone is remote?~~ Answered: they
  move with the zone, and `read_json` over `gs://` reads all 19 of them
  correctly. They are also the reason external table URIs need `*.parquet`
  rather than `*`, since they sit inside the table directory.
- Does anything reconcile the run manifests against the data? Carried forward
  from PLAN-1 and still true: nothing does. **This plan is done and this
  question is not, so it is now homeless rather than carried.** It has survived
  two plans by being appended to the next one, and PLAN-5 has no step for it.
  Either give it a step in PLAN-5 or accept it as a known gap in writing; do not
  append it to PLAN-6, which would be the third time. Listed with the other
  unowned PLAN-4 residue in `docs/dev-notes/2026-08-03.md`.
