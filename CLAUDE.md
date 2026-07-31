# CLAUDE.md

Canonical context for this repo. Read this before answering anything
architectural. If this file disagrees with README.md or SETUP.md, this file
wins and the other should be corrected.

## Working agreement (applies to every session)

- **Never run `git commit`, `git push`, `git merge`, `git rebase`, `git reset`
  or anything else that writes to git history or a remote. Not even when asked
  to "finish up", "ship it", or "commit this". Leave every change in the
  working tree and say what is there.** Committing is the human's call, and it
  is the one action in this repo that cannot be reviewed after the fact.
  Staging files with `git add` is also out. Reading git (`status`, `diff`,
  `log`, `show`) is fine and encouraged.
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
pulls records from the DataSF Socrata API and writes them untyped to a Parquet
raw zone, a separate step loads that zone into a warehouse, and dbt models it
into staging views and mart tables with tests and docs. Everything runs on
free tiers. Total cost is zero.

The design goal is ELT, not ETL: raw stays raw and is never mutated, and all
typing, renaming, and deduplication happens in dbt where it is versioned and
tested.

## Stack

- Ingestion: Python 3.10+, `requests`, `pyarrow`, `duckdb`,
  `google-cloud-bigquery`
- Warehouse: DuckDB is canonical, BigQuery secondary, Parquet is the durable
  raw zone. See `docs/decisions/adr-1-warehouse-targets.md` and
  `docs/decisions/adr-4-raw-zone-layout.md`
- Transformation: dbt, `dbt-duckdb` and `dbt-bigquery` adapters
- Orchestration: GitHub Actions
- Lint: ruff (Python), sqlfluff (SQL), pre-commit

## Current state versus intended state

Read this before assuming the ADRs describe running code.

| Area | Today | Intended | ADR |
|---|---|---|---|
| Default dbt target | DuckDB | DuckDB | ADR-1 |
| Raw zone | Parquet under `data/raw/` | same | ADR-1, ADR-4 |
| Warehouse load | `ingestion/load.py`, both engines | same | ADR-4 |
| Parquet durability | local, cached in CI for 7 days | off-machine storage | PLAN-1 step 5 |
| Spatial | lat/long floats only | H3 coarse filter plus exact refinement | ADR-2 |
| Staging models | one per source, all four | same | ADR-3 |
| BigQuery build | compiles in CI, never executed | run and verified by hand | PLAN-1 step 4 |

The last row matters: no session so far has had Google Cloud credentials, so
`dbt build --target bigquery` has never actually run. CI compiles against
BigQuery on every PR, which proves the SQL is valid there, not that it returns
the same rows.

## The pipeline is three steps

They are separate on purpose (ADR-4). Only the BigQuery ones need credentials.

```
make ingest    Socrata  -> data/raw/*.parquet   network, no credentials
make load      data/raw -> DuckDB               no network, no credentials
make build     dbt run + test                   no network, no credentials
```

## How to run everything

```
make setup            # venv, requirements, dbt deps, pre-commit hooks
make ci-build         # whole pipeline from fixtures. No network, no creds.
make ingest           # pull all datasets from DataSF into data/raw
make load             # load data/raw into the local DuckDB file
make build            # dbt build against DuckDB (default target)
make rebuild          # drop the warehouse and rebuild it from data/raw
make test             # dbt test only
make docs             # dbt docs generate, refresh docs/dbt/ artifacts
make lint             # ruff + sqlfluff
make leak-check       # scripts/leak-check.sh, exits nonzero on a hit
make check            # everything CI runs on a PR
make load-bigquery    # (creds) load data/raw into BigQuery
make build-bigquery   # (creds) dbt build --target bigquery
```

Only `load-bigquery` and `build-bigquery` need `GCP_PROJECT_ID` and
`GOOGLE_APPLICATION_CREDENTIALS`. Load them with `set -a; source .env; set +a`.
Everything else, including the full CI gate, runs on a fresh clone with no
Google account.

`make rebuild` is the load-bearing one: if dropping the DuckDB file and
reloading from `data/raw` does not reproduce every model, the raw zone is not
the source of truth it claims to be. CI runs it on every PR.

## Read-first order for a new session

1. This file.
2. `docs/decisions/` in number order. These are the constraints you inherit.
3. `docs/plans/` for anything with `status: active`.
4. The most recent two files in `docs/dev-notes/`.
5. `ingestion/datasets.py` for what is in scope, then `ingestion/raw_zone.py`
   for the raw zone layout, then `ingest.py` and `load.py`.
6. `dbt/models/staging/datasf/stg_datasf__311_cases.sql`, the reference model.

`SETUP.md` is the human onboarding path and is more detailed than this file on
Google Cloud setup. It is not authoritative on architecture.

## Directory conventions

```
ingestion/          datasets.py is the dataset registry. raw_zone.py owns the
                    Parquet layout and is the only thing that reads it.
                    ingest.py writes it; load.py loads it into a warehouse.
dbt/models/staging/ one view per raw table. Rename, cast, deduplicate. No logic.
dbt/models/marts/   hand-written analysis tables. Reference staging only,
                    except mart_pipeline_freshness, which is about the
                    pipeline and says why in its header.
dbt/macros/         cross_engine.sql holds the adapter dispatch macros.
                    audit_run_results.sql persists dbt's own run results.
docs/plans/         plan-<n>-<slug>.md, forward-looking intent.
docs/decisions/     adr-<n>-<slug>.md, one decision each, immutable once accepted.
docs/dev-notes/     YYYY-MM-DD.md, append-only session log.
docs/dbt/           committed manifest.json and catalog.json. Refresh: make docs.
tests/fixtures/     committed Socrata JSON so CI can run without network.
scripts/            leak-check.sh and other repo hygiene scripts.
data/raw/           local Parquet raw zone. Gitignored, never committed.
keys/               service account keys. Gitignored, never committed.
```

Naming: staging models are `stg_<source>__<entity>`, marts are `fct_`, `dim_`
or `agg_` prefixed. `mart_` is reserved for metadata marts, which describe the
pipeline rather than the city; there is one. Source yml files are
`_<source>__sources.yml`.

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
- **The Parquet raw zone is append-only.** Files are added, never edited or
  deleted, the one exception being `ingest.py --full-refresh`, which swaps a
  whole tree atomically. Warehouse raw tables are derived mirrors of the zone,
  rebuilt wholesale by `load.py`; never write an UPDATE or DELETE against
  them either. Deduplication belongs in staging.
- SQL must compile on both BigQuery and DuckDB. Use the dispatch macros in
  `dbt/macros/cross_engine.sql` instead of engine-specific functions such as
  `safe_cast`, `float64`, `try_cast` or `json_extract_string`. Do not compare
  a timestamp against a bare `current_timestamp`; use `x_utc_now()`, and read
  the comment above it before deciding you know better.
- Every model description states its grain as "one row per ...", and every
  model carries a grain test plus `not_null` on its keys.

## Pointers

- Decisions: `docs/decisions/` (ADR-1 warehouse targets, ADR-2 spatial
  strategy, ADR-3 dataset scope, ADR-4 raw zone layout)
- Plans: `docs/plans/`
- Session log: `docs/dev-notes/`
- Human onboarding: `SETUP.md`
- Marts roadmap: `dbt/models/marts/README.md`
- Fixtures and what they deliberately break: `tests/fixtures/README.md`
