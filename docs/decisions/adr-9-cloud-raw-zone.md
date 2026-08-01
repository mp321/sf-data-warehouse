---
status: active
date: 2026-08-01
related: [adr-1-warehouse-targets, adr-4-raw-zone-layout, adr-8-published-exports, plan-4-cloud-first-storage]
---

# ADR-9. The Parquet zones live in GCS, and BigQuery reads them where they lie

Supersedes ADR-4, which is otherwise still the description of the zone. Read
that one first: the layout, the all-STRING contract, the append-only rule, the
run manifests, and the watermark coming only from the zone all carry forward
unchanged. This ADR changes two things and nothing else: where the files are,
and how BigQuery gets at them.

## Context

Two forcing constraints, both quotas rather than preferences.

**The zone was not durable.** `data/raw` is gitignored, so the record of every
ingestion since 2026-07-30 existed on one laptop. The scheduled workflow stood
in for durability with a GitHub Actions cache, which evicts after 7 days; a miss
makes `read_watermark` return nothing and the next run backfills from
`start_date`, which for 311 is 8.8 million rows, unattended, on a shared runner.
That is a mitigation with a 7 day fuse, not durable storage.

**BigQuery was running out of room.** `load.py --target bigquery` copied the zone
in as materialized tables. The raw contract is all-STRING, so BigQuery's logical
bytes run roughly ten times the Parquet on disk: a 162 MB zone became 8.02 GB in
four tables against a 10 GiB free-tier ceiling. Every scheduled run also rewrote
all of it, because loading replaces rather than appends.

A third constraint bounds the answer. ADR-1 requires that a fresh clone with no
Google account can run the whole pipeline and the whole CI gate. Whatever the
zone's remote home is, local has to stay the default and the credential-free path
has to keep working with no bucket at all.

## Options considered

**A. Keep the zone local, keep copying into BigQuery.** Free, and it is what was
already running. Rejected on the two quotas above: it is not durable and it does
not fit. It also makes the BigQuery copy a second source of truth that nothing
reconciles.

**B. Zone in GCS, read by DuckDB through httpfs.** DuckDB's own extension, C++,
and the fastest option. Rejected on credentials, and only after testing it:
httpfs reaches GCS through the S3-compatible interoperability layer, so
`create secret (type gcs, provider credential_chain)` is rejected outright and a
bare gcs secret returns 403 against a service account. It needs HMAC
interoperability keys, which means a second credential to create, store in
`.env`, add as a GitHub secret and rotate, in a project whose hard constraint is
that no credential reaches the repo. The honest case for it: it is the only
option with no Python dependency and no version ceiling, and if the zone grows
to tens of gigabytes it wins on throughput.

**C. Zone in GCS, read by DuckDB through fsspec and `gcsfs`.** Reuses
`GOOGLE_APPLICATION_CREDENTIALS`, so the IAM story stays one service account with
object admin on one bucket. The honest case against it: it adds two
dependencies, and the newest `gcsfs` cannot coexist with this project's dbt
adapter, because `gcsfs` 2026.x requires `google-cloud-storage>=3.11` while
`dbt-bigquery` caps it below 3.2. That is a real ceiling, and installing across
it leaves an importable `gcsfs` that raises `ModuleNotFoundError` on
`google.cloud.storage.asyncio` at first use rather than failing at install time.

**D. GCS holds the record, a local cache is synced down, DuckDB only ever reads
local files.** No new dependencies, no second credential, and the fast local read
path stays byte-identical to the one already proven. Rejected because it creates
two copies with no mechanism to tell when they disagree, which is exactly the
failure this project had just spent a session fixing in the derived zone. A cache
that can be silently stale is a worse trade than a version ceiling that fails
loudly at install time.

## Decision

The zones live in GCS. `RAW_ZONE_URI` and `DERIVED_ZONE_URI` point at bucket
prefixes; unset means local, and local stays the default.

`ingestion/remote.py` owns the one answer to "is this a directory or a bucket"
and the one authentication path, so neither zone module has to. `read_sql()`
remains the single reader in each zone module: it builds a `gs://` glob instead
of a local one, and the DuckDB connection has `gcsfs` registered on it. Reads go
through fsspec, per option C.

`load.py --target bigquery` creates external tables over the GCS prefixes rather
than loading bytes. Same dataset, same table names, same all-STRING schema, so
`{{ source('raw_datasf', 'raw_311_cases') }}` resolves identically on both
engines and no model changes.

`DIR` beats `URI` when both are set. `make ci-build` sets `RAW_ZONE_DIR` and
`DERIVED_ZONE_DIR`, and it has to stay local and bucket-free even in a shell that
has sourced a `.env` full of URIs. Any other precedence makes
`set -a; source .env` quietly change what the CI gate tests.

Writes stay local for now. `ingest.py` and `spatial.py` write to the local zone
and the bucket is synced by hand; making the writer remote is PLAN-4 step 6. This
ADR is about where the record lives and who can read it, and the reader is the
half that BigQuery and a fresh clone depend on.

## Consequences

**Buys.** The zone survives losing the machine. BigQuery storage went from
8.02 GB to 40.96 MB, marts and views only, because no raw bytes live there any
more; the free tier stopped being a ceiling and became irrelevant. The
replace-on-load rewrite is gone: new Parquet in the bucket is visible to BigQuery
with no load job at all. A clone with no `data/` directory can run
`make load && make build`, verified.

**Costs.** Reads are slower, measured rather than assumed: a full materialising
scan of the three largest raw tables takes 8.83 seconds from GCS against 1.48
seconds locally, and a whole `load.py --target duckdb` run from the bucket takes
37 seconds. `dbt build --target bigquery` over external tables takes 1 minute 48
against 68 seconds materialized, because every query reads Parquet from GCS
instead of BigQuery's own storage. Two new dependencies, one with a version
ceiling that will need attention the first time `dbt-bigquery` relaxes its
`google-cloud-storage` bound. And `raw_ingest_runs` cannot be external, because
the run manifests are JSON arrays rather than newline-delimited JSON, so that one
table stays materialized: 19 rows, and the only exception in the dataset.

**Lock-in.** External tables have no BigQuery-side schema management, so a column
that appears in one Parquet file and not another is resolved by BigQuery's own
inference rather than by an explicit union. It agrees with DuckDB's
`union_by_name` on today's zone, verified table by table, and there is nothing
asserting it will keep agreeing. Also, external table URIs must be
`<table>/*.parquet` and not `<table>/*`: the run manifests live inside the table
directory, and a bare wildcard picks up the JSON and fails the whole table with
"Incompatible partition schemas". That is a constraint on the zone layout now,
not just on this loader.

## Revisit if

- The zone passes about 10 GB, at which point the 6x read penalty on a full load
  stops being 9 seconds and starts being minutes, and HMAC keys plus native
  httpfs become worth the second credential.
- `dbt-bigquery` raises its `google-cloud-storage` bound to 3.11 or higher, which
  removes the `gcsfs<2026` ceiling and makes option C unconditionally cheaper.
- BigQuery query cost becomes visible. External tables scan GCS on every query
  and nothing is cached or clustered, so a mart rebuilt hourly would read the
  whole zone hourly. Nothing in this project is on that cadence today.
