# Setup guide

`CLAUDE.md` is the source of truth on architecture. Where this guide disagrees
with it, this guide is the one that is wrong.

**The credential-free path is the default and needs no Google account.** DuckDB
is the canonical warehouse (ADR-1) and both Parquet zones are local unless you
point them somewhere else, so the whole pipeline and the entire CI gate run on a
fresh clone at no cost.

```bash
git clone <this repo> && cd sf-data-warehouse
make setup        # venv, requirements, dbt deps, pre-commit hooks
make ci-build     # whole pipeline from committed fixtures. No network, no account.
```

If `make ci-build` is green, everything below is optional. Prerequisites are
Python 3.10 or newer, git and about 2 GB of disk: `make setup` installs DuckDB
as a Python package and there is no database server to run.

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

**Do not skip `make spatial`.** Skipping it does not raise an error: the
spatial models build empty and the marts build with no rows. A derived zone
that exists but is behind the raw zone fails less visibly again, so
`make build` runs `make check-derived` first and stops if it is stale.

The full target list with one line each is in `CLAUDE.md` under "How to run
everything". `make check` is what CI runs on a pull request.

## The two targets that need credentials

Only `make load-bigquery` and `make build-bigquery`, and they need two
variables: `GCP_PROJECT_ID`, the project id and not its display name, and
`GOOGLE_APPLICATION_CREDENTIALS`, an absolute path to a service account JSON
key. Put both in `.env` (copy `.env.example`) and load them with
`set -a; source .env; set +a` in each new shell. `keys/` and `.env` are
gitignored and `make leak-check` blocks a commit carrying either.

Pointing the zones at a bucket also requires them: with `RAW_ZONE_URI` and
`DERIVED_ZONE_URI` set, `ingest`, `spatial` and `load` read and write GCS using
the same key. `RAW_ZONE_DIR` takes precedence over `RAW_ZONE_URI`, and the same
for the derived pair, which is what keeps `make ci-build` local and
credential-free in a shell that has sourced `.env`.

**Where the bucket layout is documented:** ADR-18 for what the raw zone is and
what may delete from it, ADR-9 for where the files live and how BigQuery reads
them, `.env.example` for the variables, and `CLAUDE.md` for `data/raw`,
`data/derived` and `published/`.

## Google Cloud checklist

Only needed for the BigQuery target, and for putting the two Parquet zones in a
bucket. Follow Google's own documentation for the click paths. What is below is
the set of choices that keep this project inside the always-free tier.

- **Project and BigQuery sandbox.** Sandbox tables expire after 60 days until
  billing is attached. The `raw_datasf` dataset still carries that 60 day
  default partition expiry, which is inert only because nothing in it is a
  partitioned native table.
- **Service account and JSON key.** Put the key in `keys/`, which is
  gitignored, and point `GOOGLE_APPLICATION_CREDENTIALS` at it as an absolute
  path.
- **Bucket settings that keep it free.** A single region rather than
  multi-region `US`, soft delete cleared, versioning off, uniform bucket-level
  access on, public access prevention on.
- **IAM, the narrowest that works.** `roles/storage.objectAdmin` at the bucket,
  and `bigquery.user` plus `bigquery.dataEditor` at the project. Not
  `bigquery.admin`. Note that `objectAdmin` deliberately excludes
  `storage.buckets.get`, so `client.get_bucket()` returns 403 while every
  object read and write succeeds. That is correct, not a misconfiguration.
- **Verify with a probe** that uploads and deletes one object as the service
  account. `gcloud auth activate-service-account` switches your active account
  globally until you switch back. Impersonating instead of using a key needs
  `serviceAccountTokenCreator` on your own account.
- **Four repository secrets** for the scheduled workflows: `GCP_PROJECT_ID`,
  `GCP_SA_KEY`, `GCS_BUCKET` (bare name, no `gs://`) and `SOCRATA_APP_TOKEN`.
  `ci.yml` uses none of them, and should continue to use none.

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
