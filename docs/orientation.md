# Orientation

Why this repo is built the way it is, and where each reason lives in the code.

`CLAUDE.md` is the canonical context and wins on any disagreement. ADRs are
immutable once accepted: each iteration wrote a new ADR after review
and did not edit previous one. Every claim cites the file that implements it.

---

## 1. What this is

DataSF's Socrata API publishes 311 cases, building permits, business locations
and boundary sets. Most useful questions are spatial, and DataSF's own
`supervisor_district` column is null on many rows, stamped at report time, and
missing from some datasets. So the warehouse assigns points to polygons itself.

It cannot do that in SQL. Every model runs on both DuckDB and BigQuery (ADR-1),
and the two disagree near a boundary: one planar, one spherical. Geometry is
computed once in Python and stored as columns, so a query does an integer join
and a string equality (ADR-5, ADR-6).

Four layers:

- **Raw zone.** Parquet, all columns STRING, hive-partitioned by ingest date,
  append-only with three exceptions. The warehouse rebuilds from it without
  re-fetching (ADR-18).
- **Derived zone.** Parquet, typed. A function of the raw zone and the code
  that computed it: H3 cells, boundary membership, population per cell (ADR-5,
  ADR-11).
- **Warehouse.** dbt over both zones: staging views, one intermediate model,
  six marts (ADR-1).
- **Published export.** One Parquet file per mart with a manifest (ADR-8,
  ADR-12), plus the context pack that tells a language model what to refuse
  (ADR-13).

Pipeline: `make ingest`, `make spatial`, `make load`, `make build`,
`make publish`.

Five hops for one 311 record:

1. `ingest.py` reads its watermark from the zone alone (:218), pages by
   `(:updated_at, :id)` because a non-unique order lost rows, and turns every
   value into a string (:84).
2. `spatial.py` needs no network. It computes H3 at r8 and r10 as BIGINT
   (`h3_points.py:36`) and assigns each point a boundary: interior r10 cells
   are inherited, the rest get a point-in-polygon test (`boundaries.py:56`).
3. `load.py` is a separate command so an API failure and a warehouse failure
   stay independent. It rebuilds the whole table from the zone, so it is
   idempotent (:118). BigQuery gets external tables over the same files (:389).
4. `stg_datasf__311_cases.sql` keeps the newest version per case (:49), casts
   through the cross-engine macros, and LEFT joins geography so a case with no
   derived row keeps nulls (`dbt/macros/point_geography.sql:54`).
5. `mart_activity_by_neighborhood` groups by neighborhood, dataset, category
   and month (:51). No geometry operator appears in the plan.

---

## 2. Decision record

ADR-3 and ADR-4 are archived. Superseded ADRs stay in `docs/decisions/` when
`prose.yml` cites them.

**ADR-1. Warehouse targets.** Active. Parquet is the record, DuckDB the default
target, BigQuery a second target over the same files, every model runs on both.
BigQuery sandbox tables expire by deletion after 60 days, which could wipe the
watermark and trigger an 8.8M row backfill. Price: nine dispatch macros in
`dbt/macros/cross_engine.sql`.

**ADR-2. H3 filter, exact geometry at query time.** Superseded by ADR-6. It
kept a geometry engine in every query and made the targets disagree. Nothing
implements it.

**ADR-3. Dataset scope, first pass.** Archived. Four datasets, and no new ones
until both core sources had a mart. The rule deadlocked. It blocked the
budget-to-311 crosswalk (`prose.yml:112`).

**ADR-4. Raw zone layout, loading as its own step.** Archived; read ADR-18
instead. Found while measuring it: `$order=:updated_at` with `$offset` over a
column with 7,425-row ties skipped rows, so a 36,112-row slice held 35,918
distinct ids.

**ADR-5. H3 cells computed in Python, stored as BIGINT.** Active. BigQuery has
no H3 function, so a dispatch macro has nothing to dispatch to.
`RESOLUTIONS = (8, 10)` (`h3_points.py:36`) is load-bearing: changing it
invalidates every stored cell. Skipping the spatial step fails quietly, since
marts come out empty rather than erroring.

**ADR-6. Membership decided at precompute time.** Active, supersedes ADR-2.
Cell-only membership agrees with exact tests 94.7 percent of the time, and the
error correlates with geography, so it is bias rather than noise.
`assert_point_boundary_is_exact.sql` fails on one disagreement. Costs: a
boundary change needs `make spatial`, and this repo owns a point-in-polygon
implementation that can be wrong.

**ADR-7. Dataset scope, second pass.** Superseded by ADR-10. Nine datasets in
three tiers, because ADR-3's rule had deadlocked: polygons were blocked on a
mart that needed polygons. It also found `api.census.gov` now requires a key,
which would put a credential on the critical path of `make ingest`.

**ADR-8. Published exports, local first.** Active. `make publish` writes every
mart to `published/` and uploads only if given a destination, manifest last.
Publishing straight to a bucket would make this the one part a fresh clone
cannot exercise. The remote path has no automated coverage.

**ADR-9. Both zones in GCS, BigQuery reads them in place.** Active. Two quotas
forced it: the zone lived on one laptop behind an Actions cache, and copying
the all-STRING zone into BigQuery turned 162 MB of Parquet into 8.02 GB against
a 10 GiB tier. Local stays the default, with `DIR` beating `URI` and no test
covering that. `ingestion/remote.py` is the only module that authenticates.

**ADR-10. Seven datasets, two H3 resolutions.** Active, supersedes ADR-7. Cut
`city_budget` and `street_trees`, drop r9, move `mart_activity_by_h3` to r8.
This repo is read in twenty minutes or not at all, and nine datasets across 22
models buried the parts worth reading. `business_locations` is now the only
dataset dense enough to expose a broken cell assignment.

**ADR-11. The derived zone records the code that built it.** Active. On
2026-08-05 the bucket held r9 cells that ADR-10 had removed the day before and
nothing noticed, because row counts agreed and only the code had changed. A
hash over every deciding module now goes in the zone manifest
(`derived_state.py:78`), and `check_derived.py` grades `RECODED` at exit 4. A
version constant was rejected because it fails open.

**ADR-12. One published file per mart.** Active, amends ADR-8. Month
partitioning wrote 2,280 objects against a free tier of 5,000 Class A
operations, and cost 5.8 times the bytes, because the median partition held 40
rows and a 5 KB Parquet file is mostly footer. The partitioned branch
(`export.py:214`) is now unexercised.

**ADR-13. The context pack is committed, generated by hand, checked in CI.**
Active. CI is credential-free, so a pack generated there would carry seven-row
counts, and regenerating against an untouched warehouse produced a 40-line diff
of clock values. The check compares target, prose revision, spec version and
schema hash, so a month-old pack passes.

**ADR-14. A superseded snapshot partition may be deleted.** Superseded by
ADR-18. The raw prefix was 511.9 MB against a 5 GB allowance and would have hit
it by early October. A lifecycle rule deletes by object age and would destroy
311 and permit history.

**ADR-15. The BigQuery pack is declared and never generated.** Active. Its
model set is identical to DuckDB's, so the two packs would carry the same 20
refusals and 13 joins word for word. What differs is type names, row counts and
freshness, which nothing checks, so it would be the only committed file nothing
could prove current.

**ADR-16. A cut dataset is deleted from the zone by hand.** Superseded by
ADR-18. ADR-10 left 55.5 MB in the bucket for five days while two tools warned
on every run. The prune cannot do this job: its proof is that a surviving
partition holds every key, and a cut dataset has none (`prune_raw.py:278`).

**ADR-17. The proof runs on a schedule, the deletion never does.** Superseded
by ADR-18. In the two days after ADR-14 nobody ran the prune and the daily
crons took the zone from 214.1 MB to 323.4 MB. `retention.yml` runs the
read-only proof weekly and refuses to grow an `--apply`.

**ADR-18. The raw zone, and everything that may delete from it.** Active,
supersedes ADR-4, ADR-14, ADR-16 and ADR-17. Hive layout, all-STRING contract,
run manifests, watermark from the zone, append-only with three exceptions:
`--full-refresh`, refused against a bucket (`ingest.py:349`); the prune, which
exits 3 rather than delete what it cannot prove (`prune_raw.py:281`); and the
scope deletion, which has no code by design. Acceptance test is prune, `make
rebuild`, no row count moves. It held on 2026-08-07 and has not been run clean
since.

---

## 3. What the first cross-engine build found

`dbt build --target bigquery` ran for the first time on 2026-07-31 and returned
`PASS=150 ERROR=2 SKIP=44`, after months of clean BigQuery compiles in CI.

1. **`varchar` in a yml test.** DuckDB's name for BigQuery's `string`. Routing
   through `x_type` would make the test's node name differ per target, so the
   fix was a group-by grain test that needs no cast
   (`test_unique_combination.sql`).
2. **An integer compared against quoted strings.** `accepted_values` rendered
   `int64 in ('8','10')`. DuckDB casts implicitly, BigQuery rejects. Fix:
   `quote: false` at `_spatial__models.yml:101`.
3. **A cast that returned nulls on one engine.** DataSF publishes
   `boundary_id` as `"1.0"`. DuckDB's `try_cast` truncates to 1, BigQuery's
   `safe_cast` returns null, so all eleven supervisor districts were null on
   BigQuery while local tests passed. Fix: `x_safe_int`, which already existed.
4. **The macro itself was engine-dependent.** `x_safe_int` cast float then int,
   assuming integral values. Permit 1752022162216 reports "2.5" stories, and
   float-to-int rounds on BigQuery and truncates on DuckDB: 3 against 2. Fix:
   an explicit `trunc()` (`cross_engine.sql:144`), keeping DuckDB's answer so
   no published number moved.
5. **Autodetected external-table schemas.** On 2026-08-05 the build failed with
   `Unrecognized name: unit_suffix`. `autodetect` infers one schema from a
   sampled file while DuckDB reads the zone with `union_by_name`, and Socrata
   omits null fields per record, so five columns were missing. `load.py` now
   computes the union list (:95) and declares every column STRING (:389).

Compilation checks the text you are about to send, not the system receiving it:
which types exist, what a cast does to a malformed value, how a float rounds,
which columns a table has. Four of the five defects returned a plausible number
rather than an error, so only executing on both engines finds them.

---

## 4. Known limits

A scope cut has an argument attached. A gap has a to-do attached.

**Cuts.** No city spending or crosswalk to it: it needs budget
`department_code` matched to 311 `agency_responsible`, two taxonomies with no
reason to agree (`prose.yml:112`). No street trees (`:201`). No H3 r9. No
BigQuery pack (ADR-15). No distance, buffers or routing; an r8 cell is about
460 metres across, so "same cell" is the nearest honest substitute for "nearby"
(`:219`). No rates per parcel or street mile; `dim_neighborhood` carries four
denominators instead (`:248`). No historical data before 2024
(`dbt_project.yml:141`).

**Gaps.**

- No ground-truth measure of anything. 311 measures reporting, permits measure
  filings, the registry counts registrations. Every pack preamble says so.
- The newest month is partial and nobody has measured by how much.
- Every per-capita rate divides by an April 2020 denominator, because ACS
  5-year now needs an API key. Population is spread onto cells and re-summed,
  so `dim_neighborhood.population` is an estimate twice over (`:763`, `:895`).
- Supervisor district lines moved in 2022 and only the 2022 set is ingested, so
  a pre-2022 row's upstream and recomputed districts disagree forever without
  either being wrong (`:605`).
- No consumer has ever read a pack, so the refusal format is designed against
  how models fail rather than measured against it.
- A pack's numbers can be stale and nothing says so.
- 178 columns have no description in the yml.
- The remote publish path and the partitioned export path are unexercised.
- The prune's acceptance test has not run clean since 2026-08-07.
- Three rules have no mechanism: nothing forbids a geometry function or an H3
  call in a model, nothing tests `DIR` beating `URI`, and nothing keeps
  `--apply` out of a workflow file.

---

## 5. Where documents do not align

The ADR is immutable and stays as written. These are the mechanical
differences.

- `CLAUDE.md` calls `make check` "everything CI runs on a PR". The two
  context-pack drift checks are not reachable from it, so a PR can be green
  locally and red in CI.
- `CLAUDE.md` says three of the four first-build defects are uncatchable by
  compiling. The three counts type errors; defect four is also uncatchable,
  because both engines accept the SQL and return different numbers.
- ADR-6 says boundary sets are a closed list in `datasets.py`. That file is now
  `ingestion/dataset_registry.py` and the registry is at
  `dbt/dbt_project.yml:134`.
