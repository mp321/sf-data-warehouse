# sf-data-warehouse

Analytics engineering project built on San Francisco open data: automated
Python ingestion, a Parquet raw zone, DuckDB and BigQuery warehouses fed from
it, dbt modelling with tests and documentation, an H3 spatial layer, and
scheduled CI. Built to be read, so the engineering decisions are documented in
`docs/decisions/` and summarised below.

Total cost: zero. Every part runs on a free tier, and the whole pipeline runs
on a fresh clone with no cloud account at all.

## The question it answers

**"How many 311 cases are inside this neighborhood, and is that a lot?"**

Both halves are the point. The first resolves through integer H3 cell
predicates with no geometry engine at query time. The second needs a
denominator, because a raw count per neighborhood is mostly a map of where
people live: 311 cases rank Mission first by count and Golden Gate Park first
per resident, and only one of those is interesting.

## Architecture

```mermaid
flowchart LR
    A[DataSF Socrata API] -->|ingest.py, incremental by :updated_at| B[(data/raw: Parquet)]
    A2[Census TIGERweb] -->|census.py| B
    B -->|spatial.py, H3 in Python| G[(data/derived: Parquet)]
    B -->|load.py, idempotent replace| C[(raw_datasf)]
    G -->|load.py| C2[(derived_spatial)]
    C -->|dbt staging: rename, cast, dedupe| D[(staging views)]
    C2 --> D
    D -->|dbt marts: hand-written SQL| E[(mart tables)]
    E -->|export.py, partitioned + manifest| H[(published/)]
    F{{GitHub Actions}} -. daily .-> A
    F -. every PR, DuckDB, end to end .-> D
    F -. weekly build + test, BigQuery .-> D
```

Five steps, separate on purpose (ADR-4, ADR-5). Only the BigQuery targets need
credentials:

```
make ingest    APIs      -> data/raw          network, no credentials
make spatial   data/raw  -> data/derived      no network, no credentials
make load      both      -> DuckDB            no network, no credentials
make build     dbt run + test                 no network, no credentials
make publish   warehouse -> published/        no network, no credentials
```

`make all` runs the first four in order. Running `ingest` without `spatial`
leaves the new rows with no geography, so `make build` checks that
`data/derived` is not behind `data/raw` before it runs anything.

`make ci-build` runs all of it from committed fixtures with no network at all.

## Data sources

Nine datasets in three tiers (ADR-7).

| Dataset | Tier | Why it is here |
|---|---|---|
| 311 cases | core | High volume, daily updates, a real record lifecycle. The anchor. |
| Building permits | core | Messy money and unit fields, and it joins to 311 on geography and time. |
| Registered business locations | core | Both a subject and a denominator: the only source that says where commercial activity is. |
| Street tree list | core | Dense, evenly spread, stable. The only dataset that would expose a broken cell assignment; 311 and permits cluster hard enough to hide one. |
| Analysis neighborhoods | reference | The 41 polygons every spatial mart joins to. |
| Supervisor districts | reference | The 11 polygons, 2022 boundaries. |
| Census block groups | reference | 681 polygons with 2020 population. The denominator. |
| City budget | demoted | One non-spatial mart. Does not join to 311; see below. |
| Film locations | demoted | The pipeline canary and the demo mart, because a portfolio should have one dataset that is fun. |

## Stack and decisions

- **ELT over ETL.** Python lands raw API records as Parquet with every column
  a STRING and zero transformation. All typing, renaming and deduplication
  happens in dbt, where it is versioned, tested and documented. Raw is never
  mutated. (ADR-1)
- **Parquet is the record; the warehouses are derived.** DuckDB is canonical
  and BigQuery is a supported secondary target fed from the same files, so a
  fresh clone builds with no Google account and losing any one vendor loses
  nothing. (ADR-1, ADR-4)
- **Incremental ingestion, ordered by a total key.** Each run resumes from the
  newest `:updated_at` in the zone. Paging orders by `(:updated_at, :id)`,
  because DataSF bulk-refreshes these datasets and ties of several thousand
  rows across a page boundary were silently losing records. (ADR-4)
- **H3 computed in Python, not by either engine.** BigQuery has no H3 support
  of any kind, so an H3 call in a model cannot compile on both targets. Cells
  are computed once and stored as BIGINTs that both engines read, which is a
  stronger guarantee than matching dialects: the two warehouses cannot
  disagree, because neither derives the answer. (ADR-5)
- **No geometry at query time.** Covering cells are the coarse filter and the
  exact point-in-polygon refinement runs once, at precompute, against only the
  two or three boundaries a cell touches. Membership is exact, and a test
  asserts it against an independently computed oracle with no threshold to
  relax. (ADR-6)
- **Every count mart has a normalised companion.** Rates per 1000 residents,
  per 1000 housing units, per 1000 businesses and per square kilometre, which
  disagree with each other on purpose. Bayview Hunters Point is 4th by 311
  count and 18th per resident.
- **Testing and observability.** 196 tests on every build, including accepted
  ranges on coordinates, relationship tests from every point table to the
  neighborhood dimension, a population reconciliation check, and two spatial
  assertions comparing the H3 machinery against exact geometry.
  `mart_pipeline_freshness` reports staleness and the per-source coordinate
  drop rate.
- **CI runs the whole thing.** Every pull request builds the raw zone from
  fixtures, runs the spatial precompute against real polygons, loads, builds,
  tests, publishes, then drops the warehouse and rebuilds it to prove the
  zones are the source of truth. It also compiles every model against
  BigQuery, which needs no credentials.

## What it does not do

Stated plainly, because a portfolio project that overstates itself is worse
than a small one that does not.

- **It does not join city spending to 311 demand.** That needs a crosswalk
  between budget department codes and the 311 `agency_responsible` field, two
  independently maintained taxonomies with no reason to agree. Building it is
  a project in itself. The budget mart stays inside one taxonomy. (ADR-7)
- **`dbt build --target bigquery` has never run.** No session has had Google
  Cloud credentials. CI compiles every model against BigQuery on every PR,
  which proves the SQL is valid there, not that it returns the same rows.
- **The remote half of `make publish` has never run against a real bucket.**
  The local export is exercised on every PR; the R2 and GCS upload paths are
  code that has not been executed.
- **Population is the 2020 Decennial count**, not a current estimate, because
  the ACS API now requires a key and ADR-1 keeps credentials off the ingestion
  path. Every per-capita rate divides by an April 2020 denominator.
- **No rates per parcel or per street mile.** Neither dataset is in scope.

## Repo layout

```
ingestion/          registry, raw zone, TIGERweb transport, H3 precompute, loader
publish/            export.py: marts to partitioned Parquet with a manifest
dbt/                models/staging, models/intermediate, models/marts, macros, tests
docs/decisions/     ADRs. Start here for why anything is the way it is.
docs/plans/         forward-looking intent
docs/dev-notes/     append-only session log, including what broke
tests/fixtures/     committed JSON so CI runs with no network
.github/workflows/  ci.yml (every PR), ingest.yml (daily), dbt.yml (weekly)
CLAUDE.md           canonical context. Authoritative on architecture.
SETUP.md            step-by-step reproduction guide
```

## Roadmap

- Run both warehouses and compare `stg_datasf__311_cases` row for row
  (PLAN-1 step 4). Until that happens, "compiles on both engines" is all
  anyone can honestly claim.
- Off-machine durability for the raw zone (PLAN-1 step 5), which still needs
  its own ADR.
- Per-boundary-set H3 resolution. The measurements in ADR-6 show block groups
  want a finer one and supervisor districts would be fine with a coarser one.
- A model-agnostic context pack so any capable LLM can query this warehouse
  correctly, and knows what it must refuse to answer (PLAN-2).
