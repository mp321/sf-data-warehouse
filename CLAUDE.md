# CLAUDE.md

Canonical context for this repo. Read this before answering anything
architectural. If this file disagrees with README.md or SETUP.md, this file
wins and the other should be corrected.

## Working agreement (applies to every session)

- **Never run `git commit`, `git push`, `git merge`, `git rebase`, `git reset`
  or anything else that writes to git history or a remote. Not even when asked
  to "finish up", "ship it", or "commit this". Leave every change in the
  working tree and say what is there.** Committing is the human's call.
  Never stage files with `git add`. Reading git (`status`, `diff`,
  `log`, `show`) is fine and encouraged.
- Plans live in `docs/plans/`, decisions in `docs/decisions/`, session notes in `docs/dev-notes/`. When we agree on a plan, write it to `docs/plans/` as a
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
- Geometry: `h3` and `numpy`, used only in `ingestion/spatial.py`. Neither
  engine computes H3; both read precomputed BIGINTs. See ADR-5.
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
| Raw zone | Parquet, read and written in whichever zone is configured: `data/raw`, or `gs://<bucket>/raw` when `RAW_ZONE_URI` is set | same | ADR-4, ADR-9 |
| Derived zone | Parquet, same arrangement as raw, under `DERIVED_ZONE_URI` | same | ADR-5, ADR-9 |
| Warehouse load | `ingestion/load.py`. DuckDB materialises; BigQuery creates external tables over GCS and stores no raw bytes | same | ADR-9 |
| Parquet durability | written to GCS by `ingest.py` and `spatial.py` themselves | same | ADR-9, PLAN-4 step 6 |
| Spatial | H3 cells at r8 and r10, plus exact boundary membership | same | ADR-5, ADR-6, ADR-10 |
| Staging models | one per source, all seven, plus five spatial | same | ADR-10 |
| Marts | 3 city marts, 2 dims, 1 metadata mart | same | ADR-10 |
| Published export | local `published/` on every PR, and uploaded to GCS once by hand on 2026-08-01 | stays manual until the upload is batched | ADR-8 |
| BigQuery build | last run by hand 2026-08-01 on external tables, `PASS=196 ERROR=0`, compared row for row against DuckDB. That node count predates ADR-10; the project is 19 models and 148 tests now | same, plus the weekly `dbt.yml` cron | PLAN-4 step 3 |
| Scheduled ingest | `ingest.yml`, daily at 09:17 UTC, reads and writes the bucket. Green on a manual dispatch 2026-08-03; no cron-triggered run yet | same | PLAN-4 step 8 |

The rows that used to be the embarrassing ones are closed. `dbt build
--target bigquery` ran for the first time on 2026-07-31 and found four
cross-engine defects, three of which `dbt compile --target bigquery` cannot
catch because compiling never asks the warehouse whether a type exists. All four
are fixed, both targets build green, and `scripts/parity-check.py` compares the
point staging models row for row on demand. The zones moved to GCS on
2026-08-01 (ADR-9), the writer followed the same day (PLAN-4 step 6), and
`make publish` has uploaded to a real bucket once. PLAN-4 closed on 2026-08-03
when `ingest.yml` went green on a runner.

ADR-10 narrowed the project on 2026-08-04: `city_budget` and `street_trees`
cut, H3 resolution 9 dropped, `mart_activity_by_h3` moved to r8. Nine datasets
became seven, and every remaining one is spatial. Steps 5 and 6 followed on
2026-08-03: `ingestion/geometry.py` has direct pytest coverage and
`ingestion/spatial.py` is four files. Steps 4, 7 and 8 followed on 2026-08-05:
there is one dataset registry rather than two, `ingestion/datasets.py` is
`dataset_registry.py` and PLAN-2 is closed, and `meta_dbt_run_results` is
bounded. PLAN-5 steps 9, 12 and 13 are still open.

**The publish intent changed from "scheduled" to "manual" on evidence, not on
preference.** One publish is 2,885 objects, so 2,885 Class A operations against
a free tier of 5,000 a month, and it took 6 minutes 39 for 17 MB because the
cost is per object and the H3 mart partitions into thousands of small ones. A
daily publish would leave the free tier on day two and break the zero-cost
claim at the top of this file. Batch the upload or coarsen that partitioning
before putting it on a cron; until then, running it by hand is the correct
behaviour rather than a missing feature.

**There is one zone at a time, and it is never two.** This is the thing to have
straight before reading anything else about storage. A run reads and writes
whichever zone its environment names: `data/raw` and `data/derived` with no URI
set, the bucket prefixes with them set. A remote run does **not** also write
`data/`, so after one, the local directories hold whatever the last local run
left there. They are not a cache, not a mirror, and not a stale-but-usable copy;
they are a different zone that happens to be on this machine. ADR-9 considered
keeping both in step and rejected it: two copies with nothing to detect
divergence is the exact failure this project had just spent a session fixing in
the derived zone, and a mirror written only by the machines that happen to run
`make ingest` is a worse version of it. If you want to know what is in the zone,
point at the zone.

## The pipeline is five steps

They are separate on purpose (ADR-4, ADR-5). Only the BigQuery ones need
credentials; `make publish` needs them only with a remote destination.

```
make ingest    APIs      -> raw zone/*.parquet     network, no credentials
make spatial   raw zone  -> derived zone/*.parquet no network, no credentials
make load      both      -> DuckDB                 no network, no credentials
make build     dbt run + test                      no network, no credentials
make publish   warehouse -> published/             no network, no credentials
```

"No credentials" holds for the local zones, which are the default. Point the
zones at the bucket and the first three need `GOOGLE_APPLICATION_CREDENTIALS`,
because that is where the Parquet then is. Nothing needs a Google account to run
the pipeline or the CI gate, which is ADR-1's constraint and is unchanged.

`make all` runs the first four in order. **Forgetting `make spatial` does not
error**: the spatial models build empty, and the marts come out with no rows.
`load.py` prints a warning naming the step when the derived zone is missing.

The worse version of that mistake is a derived zone that exists and is behind,
which is what `make ingest` then `make build` leaves. The new rows reach
staging with null geography, because `join_point_geography` is a LEFT join, and
the first symptom is a `not_null` test failing several models downstream.
`make build` therefore runs `make check-derived` first: `spatial.py` records the
raw row count it read per dataset in `data/derived/_manifest.json`, and
`check_derived.py` compares that against the raw zone as it is now. Override
with `make build DERIVED_CHECK=0` if you mean it.

The derived zone is a pure function of the raw zone plus `spatial.py`, so
unlike `data/raw` it is always safe to delete: `make clean-derived` then
`make spatial`.

## How to run everything

```
make setup            # venv, requirements, dbt deps, pre-commit hooks
make ci-build         # whole pipeline from fixtures. No network, no creds.
make all              # ingest, spatial, load, build
make ingest           # pull all datasets from DataSF and TIGERweb into data/raw
make spatial          # compute H3 cells and boundary membership into data/derived
make load             # load both zones into the local DuckDB file
make build            # dbt build against DuckDB (default target)
make publish          # export marts to published/ with a manifest
make rebuild          # drop the warehouse and rebuild it from the zones
make test             # dbt test only
make test-python      # pytest over ingestion/. Fastest gate in the set.
make docs             # dbt docs generate, refresh docs/dbt/ artifacts
make lint             # ruff + sqlfluff
make leak-check       # scripts/leak-check.sh, exits nonzero on a hit
make check            # everything CI runs on a PR
make check-derived    # is data/derived current with data/raw? Nonzero if not.
make clean-derived    # delete data/derived. Always safe; make spatial rebuilds it.
make load-bigquery    # (creds) load both zones into BigQuery
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
   ADR-2, ADR-3, ADR-4 and ADR-7 are superseded; read them for the reasoning,
   then read ADR-6, ADR-9 and ADR-10 for what actually holds. ADR-5 is the
   one to read carefully: it is `active`, but ADR-10 amended its resolution
   list, so its "8, 9 and 10" is the only line in it that is out of date.
3. `docs/plans/` for anything with `status: active` or `status: draft`.
4. The most recent two files in `docs/dev-notes/`.
5. `vars.pipeline_sources` in `dbt/dbt_project.yml` for what is in scope. That
   is the dataset registry itself and the only copy of it;
   `ingestion/dataset_registry.py` is the loader that hands it to Python. Then
   `ingestion/raw_zone.py` for the raw zone layout, then `ingest.py` and
   `load.py`.
6. `dbt/models/staging/datasf/stg_datasf__311_cases.sql`, the reference model.
7. If the work is spatial: `ingestion/spatial.py`'s header for what the step
   produces and which of the three modules produces it, then
   `ingestion/boundaries.py`'s header for the three containment modes, then
   `dbt/models/staging/spatial/stg_spatial__polygon_h3.sql` for the three
   flags on the bridge table and why using the wrong one is the easiest
   mistake available here. `tests/test_geometry.py` is where the contract for
   points on an edge or a vertex is written down.

`SETUP.md` is the human onboarding path and is more detailed than this file on
Google Cloud setup. It is not authoritative on architecture.

## Directory conventions

```
ingestion/          dataset_registry.py loads the dataset registry, which is
                    not here: it is vars.pipeline_sources in
                    dbt/dbt_project.yml, one list read by dbt natively and by
                    this module through PyYAML. There is no second copy, which
                    is the point (PLAN-5 step 4); the file's docstring has the
                    argument for why the one copy lives on the dbt side.
                    raw_zone.py owns the
                    Parquet layout and is the only thing that reads or writes
                    it; derived_zone.py is its sibling for the derived zone, and
                    remote.py is the one place that knows a zone can be a gs://
                    prefix rather than a directory, and the one place that
                    authenticates to it (ADR-9). Nothing else in ingestion/
                    should learn to talk to a bucket.
                    ingest.py writes the raw zone, census.py is its TIGERweb
                    transport, spatial.py computes the derived zone,
                    check_derived.py asserts the derived zone is not behind the
                    raw one, and load.py loads both into a warehouse. geometry.py is
                    pure-Python point-in-polygon and area, used only by the
                    spatial step and never at query time.
                    spatial.py is the entry point of four files, split in
                    PLAN-5 step 6: it owns the CLI, the Arrow schemas, the run
                    order and the manifest, and calls h3_points.py (coordinate
                    classification, cells, RESOLUTIONS, the shared dedup read),
                    boundaries.py (the covering-cell bridge, exact membership,
                    the pip-sample oracle, and the explanation of the three
                    containment modes) and population.py (block group
                    population spread over cells). h3_points.py is the base:
                    the other two import from it and nothing imports back.
publish/            export.py writes marts to published/ with a manifest.
                    Standalone: it imports nothing from ingestion/.
dbt/models/staging/ one view per raw or derived table. Rename, cast,
                    deduplicate. No logic. Subfoldered by source system:
                    datasf/, census/, spatial/.
dbt/models/intermediate/
                    models that are neither staging nor a mart: they union or
                    reshape staging models and nothing queries them directly.
                    int_point_activity is the only one.
dbt/models/marts/   hand-written analysis tables. Reference staging and
                    intermediate models, except mart_pipeline_freshness,
                    which is about the pipeline and says why in its header.
dbt/macros/         cross_engine.sql holds the adapter dispatch macros.
                    audit_run_results.sql persists dbt's own run results and
                    prunes them to the last 50 runs, a window its header
                    explains and which cannot go below 2 without breaking
                    mart_pipeline_freshness.
                    point_geography.sql attaches geography to point staging
                    models. generic/ holds custom generic tests.
dbt/tests/          singular tests. The three spatial assertions live here
                    because each compares two models rather than checking a
                    column.
docs/plans/         plan-<n>-<slug>.md, forward-looking intent.
docs/decisions/     adr-<n>-<slug>.md, one decision each, immutable once accepted.
docs/dev-notes/     YYYY-MM-DD.md, append-only session log.
docs/dbt/           committed manifest.json and catalog.json. Refresh: make docs.
tests/              the pytest suite, run by `make test-python` and by the
                    python-tests job in ci.yml, which the end-to-end dbt job
                    waits on. test_geometry.py covers ingestion/geometry.py
                    directly, edge and vertex contract included.
                    test_dataset_registry.py checks the registry against the
                    things that have to agree with it and are not in the same
                    file: the dbt source tables, the staging models and the
                    ingest fixtures, in both directions. conftest.py is what
                    puts ingestion/ on sys.path, since it is scripts and not a
                    package. Everything else here is tested through dbt.
tests/fixtures/     committed JSON so CI can run without network. Includes
                    real boundary polygons with thinned vertices, so the
                    fixture run genuinely exercises the H3 machinery.
scripts/            leak-check.sh, sqlfluff-lint.sh, check-lint-pins.sh, and
                    parity-check.py, which compares a model row for row across
                    DuckDB and BigQuery and needs credentials, so it is run by
                    hand rather than in CI. fake-bq-key.sh mints a throwaway
                    key so make compile-bigquery needs none; it is the reason
                    that target is credential-free, and the on-run hook guards
                    and the profiles.yml oauth fallback are not. It is exactly
                    half the reason: the other half is --no-populate-cache on
                    the compile. Both were measured on 2026-08-03 and neither
                    is redundant, so do not delete one to tidy up. The
                    Makefile target's header carries the evidence.
                    These are the real implementation; pre-commit, the
                    Makefile and CI call them rather than restating the
                    command, so a hook and a make target cannot disagree
                    about what the check is. check-lint-pins.sh is the
                    exception that proves it: ruff genuinely is installed
                    twice, so something has to assert the two agree.
data/raw/           the raw zone when no RAW_ZONE_URI is set, which is the
                    default, all of CI, and every fresh clone. Gitignored,
                    never committed. Not a copy of the bucket: with a URI set
                    the bucket is the zone and this directory is only whatever
                    the last local run left behind.
data/derived/       the derived zone under the same rule. Gitignored. Always
                    safe to delete; make spatial rebuilds it exactly.
published/          the export. Gitignored, regenerated by make publish.
keys/               service account keys. Gitignored, never committed.
```

Naming: staging models are `stg_<source>__<entity>`, intermediate models are
`int_`, dimensions are `dim_`. `mart_` was originally reserved for metadata
marts describing the pipeline rather than the city; the marts commissioned in
PLAN-3 are named `mart_` too, so that rule no longer holds and the prefix now
means "a table meant to be queried directly". `mart_pipeline_freshness` is
still the one that describes the pipeline, and its header says so. Source yml
files are `_<source>__sources.yml`.

## Hard constraints

- **All data in this project is public.** Everything comes from
  data.sfgov.org. If a dataset is not public, it does not belong here.
- **No credentials in the repo, ever.** No service account JSON, no `.env`, no
  tokens, no project ids in committed files. Secrets reach code only through
  environment variables locally and GitHub repository secrets in CI.
- `keys/`, `.env`, `data/`, `*.duckdb`, `target/`, and `dbt_packages/` are
  gitignored. Do not add exceptions to those rules.
- **`RAW_ZONE_DIR` beats `RAW_ZONE_URI`, and the same for the derived pair.**
  `make ci-build` sets the DIR variables, and it has to stay local, bucket-free
  and credential-free in a shell that has sourced `.env`. Reversing that
  precedence makes `set -a; source .env` silently change what `make check`
  tests. See ingestion/remote.py.
- `scripts/leak-check.sh` runs in CI on every PR and blocks the merge on a hit.
  If it fires, rotate the credential first, then clean the tree.
- **The Parquet raw zone is append-only.** Files are added, never edited or
  deleted, the one exception being `ingest.py --full-refresh`, which swaps a
  whole tree atomically. That exception is local only: a directory rename is
  atomic and object storage has no rename, so `--full-refresh` against a
  `gs://` zone refuses and explains itself rather than doing a delete-then-copy
  that can leave the zone holding neither tree. Refresh into a local zone and
  upload the result. Warehouse raw tables are derived mirrors of the zone,
  rebuilt wholesale by `load.py`; never write an UPDATE or DELETE against
  them either. Deduplication belongs in staging.
- **The dataset registry has one copy, and it is `vars.pipeline_sources` in
  `dbt/dbt_project.yml`.** Python reads it through
  `ingestion/dataset_registry.py`. Do not add a Python-side list "for
  convenience": that is what was there until 2026-08-05, and two lists with
  nothing checking they agree is how a dataset gets ingested and never
  reported on. Adding one means an entry there, a source table in the
  relevant `_<system>__sources.yml`, a staging model, and a fixture;
  `tests/test_dataset_registry.py` fails on any of the four being missing.
- SQL must compile on both BigQuery and DuckDB. Use the dispatch macros in
  `dbt/macros/cross_engine.sql` instead of engine-specific functions such as
  `safe_cast`, `float64`, `try_cast` or `json_extract_string`. Do not compare
  a timestamp against a bare `current_timestamp`; use `x_utc_now()`, and read
  the comment above it before deciding you know better. Eight logical macros
  now, against ADR-1's revisit threshold of about ten.
- **No geometry at query time (ADR-6).** No `ST_` function, no spatial
  extension, no GEOGRAPHY column in any model. Boundary membership is a
  precomputed column and cell coverage is an integer join. The only geometry
  code in the project is `ingestion/geometry.py`, which runs in
  `make spatial` and never in a query.
- **Do not compute an H3 cell in SQL.** BigQuery has no H3 function at all, so
  there is nothing to dispatch to. Cells come from the derived zone as
  BIGINTs (ADR-5).
- Every model description states its grain as "one row per ...", and every
  model carries a grain test plus `not_null` on its keys.
- Every count mart exposes at least one normalised companion measure. A raw
  count per area is mostly a map of where people live, so a mart offering only
  counts invites a conclusion the data does not support.

## Pointers

- Decisions: `docs/decisions/`. ADR-1 warehouse targets, ADR-2 spatial
  strategy (superseded by ADR-6), ADR-3 dataset scope (superseded by ADR-7),
  ADR-4 raw zone layout (superseded by ADR-9), ADR-5 H3 computation (amended
  by ADR-10), ADR-6 polygon membership, ADR-7 dataset scope second pass
  (superseded by ADR-10), ADR-8 published exports, ADR-9 cloud raw zone,
  ADR-10 narrowed scope.
- Plans: `docs/plans/`
- Session log: `docs/dev-notes/`
- Human onboarding: `SETUP.md`
- Marts roadmap: `dbt/models/marts/README.md`
- Fixtures and what they deliberately break: `tests/fixtures/README.md`
