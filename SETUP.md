# Setup guide

`CLAUDE.md` is the source of truth on architecture. Where this guide disagrees
with it, this guide is wrong.

**The default path needs no Google account.** DuckDB is the canonical
warehouse (ADR-1) and both Parquet zones are local unless you point them
elsewhere, so the whole pipeline and the CI gate run on a fresh clone at no
cost.

```bash
git clone <this repo> && cd sf-data-warehouse
make setup        # venv, requirements, dbt deps, pre-commit hooks
make ci-build     # whole pipeline from committed fixtures. No network, no account.
```

If `make ci-build` is green, everything below is optional. You need Python
3.10 or newer, git and about 2 GB of disk; DuckDB installs as a Python package,
so there is no database server.

## The pipeline, in order

Five steps, each a separate command (ADR-18, ADR-5). `make all` runs the first
four.

```bash
make ingest     # DataSF and TIGERweb APIs -> raw zone Parquet   network, no credentials needed
make spatial    # raw zone -> derived zone (H3 cells, boundaries) no network, no credentials needed
make load       # both zones -> the local DuckDB file             no network, no credentials needed
make build      # dbt run + test against DuckDB                   no network, no credentials needed
make publish    # marts -> published/ with a manifest             no network, no credentials needed
```

**Do not skip `make spatial`.** Skipping it raises no error: the spatial
models build empty and the marts have no rows. A derived zone that is behind
fails less visibly still, so `make build` runs `make check-derived` first and
stops if it is stale.

The full target list is in `CLAUDE.md` under "Commands", along with the few
steps where `make check` and `ci.yml` differ.

## The two targets that need credentials

Only `make load-bigquery` and `make build-bigquery`, and they need two
variables: `GCP_PROJECT_ID`, the project id and not its display name, and
`GOOGLE_APPLICATION_CREDENTIALS`, an absolute path to a service account JSON
key. Put both in `.env` (copy `.env.example`) and load them with
`set -a; source .env; set +a` in each new shell. `keys/` and `.env` are
gitignored and `make leak-check` blocks a commit carrying either.

Pointing the zones at a bucket needs them too: with `RAW_ZONE_URI` and
`DERIVED_ZONE_URI` set, `ingest`, `spatial` and `load` read and write GCS with
the same key. `RAW_ZONE_DIR` beats `RAW_ZONE_URI`, and likewise for the derived
pair, which keeps `make ci-build` local in a shell that has sourced `.env`.

**Where the bucket layout is documented:** ADR-18 for what the raw zone is and
what may delete from it, ADR-9 for where the files live and how BigQuery reads
them, `.env.example` for the variables, and `CLAUDE.md` for `data/raw`,
`data/derived` and `published/`.

## Google Cloud checklist

Only needed for the BigQuery target and for keeping the zones in a bucket.
Follow Google's documentation for the click paths; below are the choices that
keep the project inside the always-free tier.

- **Project and BigQuery sandbox.** Sandbox tables expire after 60 days until
  billing is attached, and a dataset created in the sandbox keeps a 60-day
  default partition expiry after that. Clear it:
  `bq update --default_partition_expiration 0 "${GCP_PROJECT_ID}:<dataset>"`.
- **Service account and JSON key.** Put the key in `keys/`, which is
  gitignored, and point `GOOGLE_APPLICATION_CREDENTIALS` at it as an absolute
  path.
- **Bucket settings that keep it free.** A single region rather than
  multi-region `US`, soft delete cleared, versioning off, uniform bucket-level
  access on, public access prevention on.
- **IAM, the narrowest that works.** `roles/storage.objectUser` at the bucket,
  and `bigquery.user` plus `bigquery.dataEditor` at the project. Not
  `bigquery.admin`, and not `storage.objectAdmin`: the pipeline only lists,
  reads, writes and deletes objects, and never sets object IAM. Note the role
  excludes `storage.buckets.get`, so `client.get_bucket()` returns 403 while
  every object operation succeeds. That is why `publish/export.py` calls
  `client.bucket()` instead, and is correct rather than a misconfiguration.
- **Verify with a probe** that uploads and deletes one object as the service
  account. `gcloud auth activate-service-account` switches your active account
  globally until you switch back. Impersonating instead of using a key needs
  `serviceAccountTokenCreator` on your own account.
- **Four repository secrets** for the scheduled workflows: `GCP_PROJECT_ID`,
  `GCP_SA_KEY`, `GCS_BUCKET` (bare name, no `gs://`) and `SOCRATA_APP_TOKEN`.
  `ci.yml` uses none of them and must stay that way.
- **Watch egress, not storage.** Internet egress is the free-tier limit this
  project reaches first: 100 GB a month, mostly the daily ingest reading the
  zone. Set a budget with alerts on forecasted as well as actual spend.

## Optional: a Socrata app token

Not required; it lifts DataSF rate limits. Get one from your DataSF developer
settings and put it in `SOCRATA_APP_TOKEN`. Verify it before relying on it:
anonymous requests are served normally, but an invalid token gets a 403, so a
bad token is worse than none.

## Where to go next

`dbt/models/marts/README.md` for the mart layer's rules,
`stg_datasf__311_cases.sql` as the reference staging model, `docs/decisions/` in
number order for the reasoning behind each decision, and `CLAUDE.md` for the
canonical architecture. Adding a dataset takes four files, and
`tests/test_dataset_registry.py` fails if any one of them is missing.
