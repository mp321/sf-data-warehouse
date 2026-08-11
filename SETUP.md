# Setup guide

The human onboarding path. `CLAUDE.md` is authoritative on architecture; where
this file disagrees with it, this file is the one that is wrong.

**The credential-free path is the default and needs no Google account.** DuckDB
is the canonical warehouse (ADR-1) and both Parquet zones are local unless you
point them somewhere else, so the whole pipeline and the entire CI gate run on a
fresh clone with no cloud anything. Total cost is zero.

```bash
git clone <this repo> && cd sf-data-warehouse
make setup        # venv, requirements, dbt deps, pre-commit hooks
make ci-build     # whole pipeline from committed fixtures. No network, no account.
```

If `make ci-build` is green, everything below is optional. Prerequisites are
Python 3.10 or newer, git and about 2 GB of disk: `make setup` installs DuckDB
as a Python package and there is no database server to run.

## The pipeline, in order

Five steps, separate on purpose (ADR-18, ADR-5). `make all` runs the first four.

```bash
make ingest     # DataSF and TIGERweb APIs -> raw zone Parquet   network, no creds
make spatial    # raw zone -> derived zone (H3 cells, boundaries) no network, no creds
make load       # both zones -> the local DuckDB file             no network, no creds
make build      # dbt run + test against DuckDB                   no network, no creds
make publish    # marts -> published/ with a manifest             no network, no creds
```

**Do not skip `make spatial`.** It does not error if you do: the spatial models
build empty and the marts come out with no rows. `make build` runs
`make check-derived` first to catch the worse version, a derived zone that
exists and is behind.

The full target list with one line each is in `CLAUDE.md` under "How to run
everything". `make check` is what CI runs on a pull request.

## The two targets that need credentials

Only `make load-bigquery` and `make build-bigquery`, and they need two
variables: `GCP_PROJECT_ID`, the project id and not its display name, and
`GOOGLE_APPLICATION_CREDENTIALS`, an absolute path to a service account JSON
key. Put both in `.env` (copy `.env.example`) and load them with
`set -a; source .env; set +a` in each new shell. `keys/` and `.env` are
gitignored and `make leak-check` blocks a commit carrying either.

Pointing the zones at a bucket is the other reason to need them: with
`RAW_ZONE_URI` and `DERIVED_ZONE_URI` set, `ingest`, `spatial` and `load` read
and write GCS and need the same key. `RAW_ZONE_DIR` beats `RAW_ZONE_URI`, and
the same for the derived pair, which is what keeps `make ci-build` local in a
shell that has sourced a `.env` full of URIs.

**Where the bucket layout is documented:** ADR-18 for what the raw zone is and
what may delete from it, ADR-9 for where the files live and how BigQuery reads
them, `.env.example` for the variables, and `CLAUDE.md` for `data/raw`,
`data/derived` and `published/`.

## What used to be here, and can be written out again on request

This file was 523 lines of Google Cloud walkthrough. Each line below was a
step-by-step section; ask and any of them comes back in full.

- Creating a project and opening the BigQuery sandbox, whose tables expire after
  60 days until billing is attached.
- Creating the `sf-dw-pipeline` service account and downloading its JSON key.
- Bucket settings that keep it always-free: a single region and not multi-region
  `US`, soft delete cleared, versioning off, uniform access, public access
  prevention on.
- IAM grants: `storage.objectUser` at the bucket, `bigquery.user` and
  `bigquery.dataEditor` at the project, minus the broad `bigquery.admin`.
- The probe that uploads and deletes one file as the service account, and the
  warning that `gcloud auth activate-service-account` switches your account
  globally until you switch back. Impersonation instead of a key needs a
  `serviceAccountTokenCreator` grant on your own account.
- The four repository secrets for the scheduled workflows: `GCP_PROJECT_ID`,
  `GCP_SA_KEY`, `GCS_BUCKET` (bare name, no `gs://`), `SOCRATA_APP_TOKEN`.
  `ci.yml` needs none of them and must keep needing none.
- Getting a Socrata app token, which of the two values that page issues is the
  right one, and verifying it first: an invalid token is worse than none, since
  anonymous requests are served and a bad token gets a 403.
- A two-minute orientation to dbt: `source()`, `ref()`, tests as YAML.

## Where to go next

`dbt/models/marts/README.md` for the mart layer's rules,
`stg_datasf__311_cases.sql` as the reference staging model, `docs/decisions/` in
number order for why any of it is the way it is, and `CLAUDE.md` for the
canonical architecture. Adding a dataset is four files, and
`tests/test_dataset_registry.py` fails on any one of them being missing.
