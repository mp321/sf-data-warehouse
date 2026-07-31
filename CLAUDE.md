# CLAUDE.md

Canonical context for this repo. Read this before answering anything
architectural. If this file disagrees with README.md or SETUP.md, this file
wins and the other should be corrected.

## Working agreement (applies to every session)

- Plans live in `docs/plans/`, decisions in `docs/decisions/`, session notes in
  `docs/dev-notes/`. When we agree on a plan, write it to `docs/plans/` as a
  numbered file rather than leaving it in chat. Refer to documents as `ADR-1`
  and `PLAN-2`; see `docs/README.md` for the conventions.
- ADRs are immutable once accepted. To change a decision, write a new ADR and
  set the old one to `status: superseded` with a `related` pointer. Do not edit
  an accepted ADR's decision text.
- Plans and dev notes stay separate files. A plan is intent, a dev note is
  incident. Mixing them makes the plan unexecutable later.
- Output style: concise, one or two sentences per point unless the nuance is
  load bearing. No emojis, no em dashes or en dashes anywhere, including in
  generated docs, code comments, and commit messages. Plain hyphens only.
- End a working session by appending to `docs/dev-notes/YYYY-MM-DD.md`.

## What this project is

An analytics engineering project built on San Francisco open data. Python
pulls records from the DataSF Socrata API, lands them untyped in a raw zone,
and dbt models them into staging views and mart tables with tests and docs.
Everything runs on free tiers. Total cost is zero.

The design goal is ELT, not ETL: raw stays raw and is never mutated, and all
typing, renaming, and deduplication happens in dbt where it is versioned and
tested.

## Stack

- Ingestion: Python 3.10+, `requests`, `google-cloud-bigquery`
- Warehouse: BigQuery today, DuckDB as the intended canonical target, Parquet
  as the durable raw zone. See `docs/decisions/adr-1-warehouse-targets.md`
- Transformation: dbt, `dbt-bigquery` and `dbt-duckdb` adapters
- Orchestration: GitHub Actions
- Lint: ruff (Python), sqlfluff (SQL), pre-commit

## Current state versus intended state

Read this before assuming the ADRs describe running code.

| Area | Today | Intended | ADR |
|---|---|---|---|
| Default dbt target | BigQuery (`dev`) | DuckDB (`duckdb`) | ADR-1 |
| Raw zone | BigQuery `raw_datasf` | Parquet under `data/` | ADR-1 |
| Parquet export | `ingestion/export_parquet.py`, opt-in, not scheduled | Wired into the daily job | ADR-1 |
| Spatial | lat/long floats only | H3 coarse filter plus exact refinement | ADR-2 |
| Staging models | 311 only | one per in-scope source | ADR-3 |

ADR-1 is an accepted decision whose implementation is staged, not a
description of what runs in CI today.

## How to run everything

```
make setup            # venv, requirements, dbt deps, pre-commit hooks
make ingest           # python ingestion/ingest.py --all  (needs GCP creds)
make build            # dbt build against BigQuery (default target)
make test             # dbt test only
make docs             # dbt docs generate and serve
make rebuild          # clean + setup + ingest + build, full local rebuild
make lint             # ruff + sqlfluff
make leak-check       # scripts/leak-check.sh, exits nonzero on a hit
make compile-duckdb   # dbt parse + compile against the DuckDB target, no creds
```

`make build` needs `GCP_PROJECT_ID` and `GOOGLE_APPLICATION_CREDENTIALS`. Load
them with `set -a; source .env; set +a`. `make compile-duckdb` and
`make leak-check` need no credentials and are what CI runs on every PR.

## Read-first order for a new session

1. This file.
2. `docs/decisions/` in number order. These are the constraints you inherit.
3. `docs/plans/` for anything with `status: active`.
4. The most recent two files in `docs/dev-notes/`.
5. `ingestion/datasets.py` for what is in scope, then `ingestion/ingest.py`.
6. `dbt/models/staging/datasf/stg_datasf__311_cases.sql`, the reference model.

`SETUP.md` is the human onboarding path and is more detailed than this file on
Google Cloud setup. It is not authoritative on architecture.

## Directory conventions

```
ingestion/          datasets.py is the dataset registry; ingest.py is the loader.
                    export_parquet.py is opt-in and not yet scheduled.
dbt/models/staging/ one view per raw table. Rename, cast, deduplicate. No logic.
dbt/models/marts/   hand-written analysis tables. Reference staging only.
dbt/macros/         cross_engine.sql holds the adapter dispatch macros.
docs/plans/         plan-<n>-<slug>.md, forward-looking intent.
docs/decisions/     adr-<n>-<slug>.md, one decision each, immutable once accepted.
docs/dev-notes/     YYYY-MM-DD.md, append-only session log.
scripts/            leak-check.sh and other repo hygiene scripts.
data/               local Parquet raw zone. Gitignored, never committed.
keys/               service account keys. Gitignored, never committed.
```

Naming: staging models are `stg_<source>__<entity>`, marts are `fct_`, `dim_`,
or `agg_` prefixed. Source yml files are `_<source>__sources.yml`.

## Hard constraints

- **All data in this project is public.** Everything comes from
  data.sfgov.org. If a dataset is not public, it does not belong here.
- **No credentials in the repo, ever.** No service account JSON, no `.env`, no
  tokens, no project ids in committed files. Secrets reach code only through
  environment variables locally and GitHub repository secrets in CI.
- `keys/`, `.env`, `data/`, `*.duckdb`, `target/`, and `dbt_packages/` are
  gitignored. Do not add exceptions to those rules.
- `scripts/leak-check.sh` runs in CI on every PR and blocks the merge on a hit.
  If it fires, rotate the credential first, then clean the tree.
- Raw tables are append-only. Never write an UPDATE or DELETE against the raw
  zone. Deduplication belongs in staging.
- SQL must compile on both BigQuery and DuckDB. Use the dispatch macros in
  `dbt/macros/cross_engine.sql` instead of engine-specific functions such as
  `safe_cast` or `float64`.

## Pointers

- Decisions: `docs/decisions/` (ADR-1 warehouse targets, ADR-2 spatial
  strategy, ADR-3 dataset scope)
- Plans: `docs/plans/`
- Session log: `docs/dev-notes/`
- Human onboarding: `SETUP.md`
- Marts roadmap: `dbt/models/marts/README.md`
