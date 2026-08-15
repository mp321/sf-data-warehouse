# Dev notes archive, 2026-07-30 to 2026-08-07

Nine notes folded into one file. **Every finding below is verbatim**, under the
date it was written; what was dropped is the chronology those notes carried
around it, which is the part git history and the closed plans already hold:
Scope, Changes, Verification, Checkpoint, State at the end, Not done,
Follow-ups and For the next session.

**Read this for why something is the way it is, not for what is true today.**
A note is a record of a day and some of these were wrong when written and
corrected later in the same file; that is the point of keeping them. Anything
still true about running code was moved to CLAUDE.md on 2026-08-10 rather than
left here, because an archive is not a place anyone opens.

The two current notes, 2026-08-09 and 2026-08-10, are still loose files in this
directory. Notes are append-only: nothing here was rewritten, and a correction
to any of it goes in today's note rather than in this file.


---

# 2026-07-30

## Findings

**The `.gitignore` ignored itself.** It listed `.gitignore`, `README.md`,
`SETUP.md`, `PLAN.md` and `.env.example` among its entries, so none of them
were ever committed and `git ls-files` returned 10 files. The effect: a fresh
clone had no ignore rules at all, so the first person to follow SETUP.md, drop
a key into `keys/sa.json` and run `git add .` would have committed a live
service account private key. The ignores worked on this machine, and every
blob in history checks clean, so this was a loaded trap rather than a fire.
It is why `scripts/leak-check.sh` scans untracked-but-not-ignored files rather
than trusting the ignore rules it backstops.

**The one dbt model had never parsed.** Line 6 of
`stg_datasf__311_cases.sql` was a SQL comment containing `{{ source(...) }}`.
Jinja runs before SQL parsing and does not know that `--` starts a comment, so
it evaluated that and died on the literal `...` with "unexpected '.'".
Confirmed against the committed version at HEAD, not just the local edit:
`dbt parse` fails on the original file. So the weekly `dbt` workflow has failed
on every run since the initial commit, unnoticed, because a failing scheduled
workflow is a red icon on a tab nobody opens. Fixed by wrapping the header
comment in `{% raw %}`. A scheduled build providing no signal is worse than no
scheduled build, and is the main argument for the new PR gate.

**leak-check had a silent hole, caught only by a negative test.** The private
key pattern starts with `-----BEGIN`, and `grep -E "$regex"` parses a leading
`-----` as options rather than a pattern, so the most important rule in the
file matched nothing. It needs `grep -E -e "$regex" -- "$f"`. Found by
planting six fake secrets in a scratch repo and checking all six were flagged;
five were. A security script never shown to fail on a real positive is
decoration.

**A macro that was right by accident.** `bigquery__x_safe_cast` called the
dispatching `x_type` rather than `bigquery__x_type`. Dispatch resolves on the
connected adapter, so invoking the BigQuery branch while connected to DuckDB
produced `safe_cast(lat as double)`: a BigQuery function with a DuckDB type
name, valid on neither engine. The two normally agree, so this would only have
surfaced years later during a migration. Each branch now calls its own type
macro.

**`dbt-core>=1.8` is not a safe constraint.** A clean install resolved to a
dbt-core 2.0 alpha whose metadata generation downloads a wheel from GitHub
releases, which fails on any machine that cannot reach github.com with an
error that looks nothing like a version problem. Pinned to `<2.0`. Note that
the existing `.venv` contains `dbt-core-experimental-parser`, so that alpha is
already installed locally, which may explain behaviour differences against CI.

**Alias alignment and macros are incompatible.** The model aligned `as
<alias>` in a column. sqlfluff lints rendered SQL, and macro expansions are
not the width of their source text, so source-aligned code arrives at the
linter misaligned and hand-tuning cannot fix it. Switched to a single space
before `as`, with the reason recorded in `.sqlfluff`.

**Smaller things.** `get_watermark` takes `max()` of `_socrata_updated_at`, a
STRING column, and is correct only because Socrata's ISO-8601 timestamps are
fixed width and therefore sort lexically. Nothing documents that this is load
bearing. And `ingest.py`'s `from datasets import DATASETS` resolves only
because Python puts the script's directory on `sys.path`; it will collide with
the HuggingFace `datasets` package if that ever lands in the venv.

---

# 2026-07-31

## Findings

**Ingestion was silently losing rows, and had been from the start.** `$order`
was `:updated_at` with `$offset` paging. DataSF bulk-refreshes these datasets,
so ties in `:updated_at` are enormous: in one 36,112-row slice of
`building_permits`, 36,095 rows shared an `:updated_at` with another row and
the largest single tie was 7,425 rows, spanning two page boundaries. Ordering
by a non-unique column gives the API no reason to return a stable sequence
across separate requests, so pages overlap and, worse, leave gaps. The slice
contained 35,918 distinct `record_id` values against an upstream dataset where
`record_id` is unique across all 1,292,923 rows: 194 rows duplicated, and by
implication about as many never fetched at all. Fixed by ordering on
`(:updated_at, :id)`, which is a total order. Re-ingesting the same slice
afterwards gave 36,112 rows and 36,112 distinct `record_id`. This produced no
error and no warning, and the duplicates it created were invisible because the
staging model deduplicates them away.

**A freshness view was going to be seven hours wrong.** DuckDB's `now()` is a
`TIMESTAMP WITH TIME ZONE`, but `x_safe_cast(col, 'timestamp')` on an
offset-bearing string yields a naive UTC timestamp, because casting to
DuckDB's `TIMESTAMP` keeps the UTC wall clock and drops the offset. Subtract
one from the other and DuckDB converts to local time, so `hours_since_load`
measured -6.8 on a laptop in PDT: data appearing to arrive from the future.
BigQuery has no equivalent trap, its `TIMESTAMP` being an absolute instant, so
this would have been a DuckDB-only wrong answer in the one view whose whole
job is to be trusted at a glance. Hence `x_utc_now()`.

**`record_id`, not `permit_number`, is the permit grain.** `permit_number`
repeats up to 101 times, because revisions and addenda are filed as separate
records against one permit. A unique test on `permit_number` would have failed
on the first real backfill. Similarly, `city_budget` has no natural key at all:
the full combination of year, department, fund, program, object, sub_object
and character still returns groups of up to six, so the grain key is Socrata's
row id. Same for `film_locations`, where the same title at the same address
appears up to three times with nothing to tell the rows apart.

**A 15-row `LIMIT` nearly shipped a broken test.** The first pass at
`accepted_values` for `permit_status` came from a top-15 query and missed
`issuing`, `denied`, `inspection`, `incomplete`, `unknown`, `upheld`,
`granted` and `overruled`, all with single-digit or low-double-digit counts,
plus 12 rows where `status` is null. The local slice happened to contain three
of the missing values, which is the only reason it surfaced now rather than on
the first full backfill.

**Small samples cannot be fixtures here.** Socrata omits null fields per
record rather than sending nulls, so a column present in 2% of rows is simply
absent from a 25-row sample and the resulting Parquet file has no such column
at all. `stg_datasf__building_permits` failed with "Referenced column
street_number_suffix not found". That failure is correct in production, where
a column vanishing upstream should stop the build rather than quietly become
NULL, so the fix belongs in the fixtures and not in the model: the generator
now appends a synthetic coverage record carrying every field seen in a 400-row
scan. This is exactly the kind of thing that would otherwise be discovered by
a staging model breaking on a quiet Tuesday.

**`make check` was contaminating the raw zone, and I shipped it before
catching it.** `ci-build` ran the fixture ingest against the default zone, so
running the local CI gate appended a fixture row to `data/raw`. Caught by
diffing row counts after the first `make check`: `raw_film_locations` had gone
from 2,214 to 2,215, because the coverage record's `:updated_at` was newer
than that dataset's watermark. The other three were unaffected only by luck of
their watermarks being newer. `ci-build` now runs entirely inside `data/ci/`.
The zone was restored by deleting the four fixture-mode run manifests and the
one Parquet file, and verified back to its original content hash.

**The DuckDB path in `profiles.yml` is resolved against the caller's working
directory.** `../data/sf.duckdb` is right when dbt runs from `dbt/`, which is
what the Makefile does, and wrong from anywhere else. sqlfluff runs the dbt
templater from the repo root and was trying to open
`<repo-parent>/data/sf.duckdb`, a sibling of the repository, and crashing
with a raw `IOException` rather than anything mentioning configuration. The
Makefile and CI now export an absolute `DUCKDB_PATH`.

**`dbt compile --target bigquery` needs no credentials.** Compiling does not
open a connection, so the entire BigQuery dialect can be checked on a fork
pull request. This was assumed impossible when `ci.yml` was written, which is
why the cross-engine guarantee had no gate. It has one now.

**Jinja's `match` test does not exist in dbt's sandbox.** Used it to filter
node ids in the on-run-end hook. The hook compiles last, so the failure
arrived after a full successful build, reporting "No test named 'match'" with
`ERROR` and an otherwise green run above it.

## Second session: lint toolchain drift

Separate from the work above, and only about the quality gates.

### Symptom

`git commit` failed in `sqlfluff-lint` with a traceback that ended in
`_ForkingPickler.dumps` and never named a rule or a file. The models were
fine. The hook's own environment could not be built.

### Cause

pre-commit builds an isolated env per hook from `additional_dependencies`.
That env is a second toolchain, and nothing in this repo constrained it.
It pinned `dbt-core==1.9.1`, which caps `mashumaro` below 3.15, and
mashumaro 3.14 does not import on Python 3.14, where PEP 649 deferred
annotations changed how its type introspection resolves. The real error was
`UnserializableField: Field "schema" of type Optional[str]`, buried under
multiprocessing trying to pickle the worker exception.

CI never saw it. The lint job runs Python 3.11 and installs sqlfluff from
`requirements-dev.txt`, so it was resolving sqlfluff 4.2.2 and dbt-core
1.12.0 while the hook was frozen at 3.3.0 and 1.9.1. Three toolchains, one
of them broken, and the broken one was the only one a human ran.

### The part that mattered more

Bumping the ruff pin for consistency surfaced a live failure. The hook pinned
`v0.8.6`; `requirements-dev.txt` says `ruff>=0.8`, which now resolves 0.16.1.
Ruff 0.16 stabilised PLC0415, and `ingestion/load.py` has two deliberate
function-scope `from google.cloud import bigquery` imports. So `make lint` and
the CI lint job were both failing on HEAD while the pre-commit hook passed.
Silenced per line with the reasoning at the first occurrence, rather than
ignoring PLC0415 repo wide, because the rule is right everywhere else in
`ingestion/`. Hook is now `ruff-check` at v0.16.1, matching what CI installs.

# Third session: geography, marts and publishing (PLAN-3)

## The thing that had to be decided before anything could be built

**ADR-3 deadlocked itself.** It blocked new datasets until both core sources
had a mart, and the marts were all spatial, and there were no boundary
datasets to be spatial against because ADR-2 assumed a polygon source that was
never in scope. So the rule forbade the work it existed to protect. ADR-7
supersedes it: nine datasets, three tiers, with `reference` added for boundary
sets that need tests but not a freshness alarm.

**ADR-2's refinement could not be implemented as written.** It put exact
point-in-polygon at query time behind an engine-specific macro, which means a
geometry engine in every query, the DuckDB spatial extension back in the
build, and answers that differ between targets near boundaries. ADR-6
supersedes it by moving the refinement to precompute time. The scheme is
otherwise ADR-2's, and its coarse filter is what makes the exact step
affordable: refinement tests a point against the two or three boundaries whose
covering set includes its cell, not against all 41.

## Findings

**Film locations does carry usable coordinates, so no geocoding decision was
needed.** The plan flagged this as something to verify rather than guess at.
ADR-3 demoted the dataset partly because "its locations are free text rather
than coordinates, so it cannot participate in ADR-2". The free-text `locations`
column is real, but the dataset also publishes flat `latitude` and
`longitude`, populated on 2,127 of 2,214 rows. The staging model had even been
reading them since the last session, directly under a comment repeating the
ADR's claim. There was nothing to geocode; ADR-7 corrects the record.

**ADR-2's resolution estimate was out by two orders of magnitude, in the
helpful direction.** It guessed a resolution 9 seed table "in the low hundreds
of thousands of rows" and warned it would get awkward to keep in git. The
whole bridge, three boundary sets across three resolutions, is 98,655 rows.
Neighborhoods at r9 alone are 1,762. That is what made membership at
resolution 10 affordable, which ADR-2 had assumed it would not be.

**ADR-2's revisit threshold fires immediately and the ADR is right to have
had one.** It said to reconsider if more than about 20 percent of populated
cells are boundary cells. Measured for neighborhoods: 95.8 percent at r8, 66.3
at r9, 35.7 at r10. Even at the finest resolution it is passed nearly twice
over, because San Francisco's neighborhoods are small and intricate relative
to any hexagon worth aggregating on. This is the measurement that turned
"cells plus refinement" from an optimisation into a requirement.

**Cell-only membership is 94.7 percent right, which is not good enough and is
worth knowing precisely.** Sampled against exact point-in-polygon, 10,000
points per boundary set: neighborhoods 72.6 / 88.2 / 94.7 percent at r8 / r9 /
r10, supervisor districts 83.6 / 92.7 / 96.8. Interior cells alone agree 100
percent, which is what the design predicts and is why the split earns its
keep. The five percent error is not noise: it lives entirely at boundaries, so
it correlates with geography and would bias every neighborhood comparison in
the same direction. Hence exact refinement rather than accepting it.

**Population was silently disappearing at coarse resolutions.** Interpolating
block group population onto cells used `is_primary`, which is stripped to one
boundary per cell so that membership joins cannot fan out. At r8 a single cell
covers dozens of block groups, so stripping it discarded all but one of them
and their residents with it: 221,088 of 873,965 San Franciscans survived to
r8, and 733,272 to r9. Nothing failed. The rates would simply have come out
high, uniformly, forever. Fixed by splitting `is_allocation_cell` from
`is_primary`, and `spatial.py` now raises if the city total does not survive
interpolation at every resolution rather than trusting that it does.

**Covering cells came back with duplicates for MultiPolygons.** `h3` returns
a list, and a MultiPolygon whose parts share a cell yields that cell once per
part. Every downstream join would have fanned out and over-counted. Found by
a uniqueness check on the bridge, not by anything failing, which is the
uncomfortable part: the numbers would have been wrong and plausible.

**18 percent of registered business locations are not in San Francisco.**
Including Atlanta. This is correct data, not dirt: the registry records where
a business is, and plenty of businesses holding a San Francisco tax
certificate are located elsewhere. It forced the coordinate classifier to
separate `out_of_bounds` (a real place, not here) from `impossible` (not a
coordinate), because the first is a fact about the world that will be nonzero
forever and the second is a pipeline fault that should be zero. Only the
second counts against `is_healthy`.

**api.census.gov now requires a key on every endpoint**, including the
county-level ones that used to be keyless. An ACS 5-year fetch would have put
a credential on the critical path of `make ingest`, which ADR-1 spent a whole
decision removing. TIGERweb's Census 2020 block group layer carries POP100 and
HU100 alongside the geometry and needs no key, so the denominator is the 2020
Decennial enumeration instead. It totals 873,965, matching the published
figure exactly. The substitution is recorded in ADR-7 because it is a real
loss: the denominator is fixed at April 2020 and drifts further every year.

**TIGERweb rate-limits with HTTP 200 and an HTML body.** No status code, no
Retry-After. `response.json()` failed with a JSONDecodeError pointing at
character zero and mentioning neither the Census Bureau nor rate limiting.
Tripped by regenerating the fixtures, which is exactly the burst that provokes
it. `census.py` now checks the content type, backs off, and distinguishes the
WAF from a bad query, which is never worth retrying.

**A 400-row scan is not a column list.** The fixture generator built its
coverage record from the oldest 400 rows, and not one of the 400 oldest street
trees carries `latitude`, `longitude`, `location`, `xcoord` or `ycoord`. The
resulting fixture had a street tree dataset with no coordinates and broke
`make spatial` with "Referenced column latitude not found". This is invariant
1 in `make_fixtures.py` recurring in a form the existing mitigation did not
cover. The generator now reads the real column list from the dataset metadata
and fetches a value for anything the scan missed.

**Four street trees have trunk diameters of 9999, 3030, 1920 and 1530
inches.** Found by the `accepted_range` test the plan asked for, on its first
run. 9999 is the classic not-recorded sentinel; the others look like a lost
decimal point. The widest tree trunk ever measured is about 38 feet, so the
model nulls anything above 240 inches. The rows survive with every other
column intact.

**Two boundaries are legitimately enormous and broke a range test.**
Supervisorial District 4 covers 261 square kilometres because it reaches out
to the Farallon Islands 43 km offshore, and one block group is 248 square
kilometres of ocean. San Francisco's land area is 121. The first accepted
range was set from the land figure and failed on correct data.

## Third session: the stale derived zone, made loud

## What the failure actually was

Four `not_null` failures, on `coordinate_status` and `is_usable_coordinate`
across three point staging models, and 51 skips that were only the downstream
of those four. The counts were 4526 on 311 cases, 118 on building permits, 59
on business locations.

`data/derived` was built at 09:43Z. An ingest ran at 21:48Z. `make spatial`
never ran in between, so 4703 point rows existed in the raw zone with no row in
`derived_point_h3`, and `join_point_geography` is a LEFT join, so they reached
staging with null geography instead of disappearing. The fix was
`make spatial && make load && make build`: 196 nodes, no failures, no skips.

Worth writing down that every part of this failure was working as designed. The
LEFT join is deliberate and documented, the `not_null` tests are the contract
that caught it, and the skips are dbt refusing to build on a failed test. The
defect was that nothing in the output named `make spatial`.

## Findings

- **Follow-up 3 proposed a hash of the raw zone's file list. That would have
  been wrong.** Every incremental ingest adds files, including one that
  re-reads rows already present, so a file-list hash differs on every run and
  fires whether or not the row set changed. It cannot tell a coverage gap from
  a value update, and a check that fires after every ingest gets an override
  flag added and then ignored. Deduplicated row counts are the number the
  staging models actually have, which is why the comparison means something.
- **The two outcomes are genuinely different and only one is detectable
  downstream.** A missing row fails `not_null` eventually. A row whose
  coordinates were updated in raw after the zone was built has non-null
  geography computed from the old values, and no test in the project can see
  it. That is the DRIFT case, and it is a warning because it is unavoidable
  short of rebuilding on every ingest.
- **`load.py` was the wrong place for this**, which is where the follow-up
  suggested putting it. `load` runs happily on a stale zone and should; the
  zone is the record and loading it is faithful. `build` is where a wrong
  answer gets produced, so that is where the gate belongs.
- **`coordinate_quality.total` in the old manifest was already the number
  needed**, per table, deduplicated. It was written for a coverage report and
  would have made this diagnosable in March. Nothing read it.

## Fourth session: the BigQuery target, executed at last

## The headline

`dbt build --target bigquery` has been executed against real data for the first
time in this project's life. It came back `PASS=150 WARN=0 ERROR=2 SKIP=44`, and
chasing those two errors turned up four distinct cross-engine defects. All four
are fixed and both targets now build `PASS=196 ERROR=0`.

PLAN-1 step 4 is closed after being open since the plan was written.

## The four defects, in the order they surfaced

1. **`cast(x as varchar)` in three grain tests.** `varchar` is DuckDB's spelling
   and BigQuery has no such type, so three `unique` tests that concatenated
   their key columns died with `Type not found: varchar`. These were in
   `_marts__models.yml`, not in any model, which is why every review of the
   model SQL missed them.
2. **`accepted_values` on an integer column.** dbt quotes the accepted values by
   default, so the rendered predicate was `int64 in ('8','9','10')`. DuckDB
   casts implicitly, BigQuery raises `No matching signature for operator IN`.
   One `quote: false` fixes it.
3. **`dim_supervisor_district` nulled all 11 districts on BigQuery.** It cast
   `boundary_id` straight to int. DataSF publishes the district number as the
   string "1.0". DuckDB's `try_cast` truncates that to 1; BigQuery's
   `safe_cast` refuses a fractional string and returns null. Every local test
   passed, and on BigQuery the column was null in all 11 rows.
4. **`x_safe_int` was itself engine-dependent.** Building permit 1752022162216
   reports "2.5" stories. The macro was a float cast followed by an int cast,
   and BigQuery rounds 2.5 to 3 while DuckDB truncates to 2, so
   `stg_datasf__building_permits` genuinely held different data on the two
   engines. Found by the row comparison, not by any test: both values are
   non-null integers and no test asserts which one is right.

## Findings

- **Compiling is not agreeing, and the gap is bigger than it looked.** Three of
  the four defects were type errors, which `dbt compile --target bigquery`
  cannot see: compiling renders Jinja and never asks the warehouse whether
  `varchar` exists or whether `int64 in ('8')` is legal. CI has been green on
  that step for weeks while four defects sat behind it. The compile step is
  still worth having, it just proves less than its name suggests.
- **Two of the four were in yml, not in SQL.** The cross-engine rule in
  CLAUDE.md is written as a rule about models, and every macro and every review
  has treated it that way. Test definitions are SQL too, and nothing was
  looking at them.
- **The `x_safe_int` bug is the one worth remembering.** It was not a syntax
  error, it did not fail a test, and both engines returned a plausible integer.
  Only a row-for-row comparison could find it. That is the argument for
  `scripts/parity-check.py` existing as a committed tool rather than as a hash
  pasted into a dev note: the class of bug it catches is invisible to
  everything else in the repo.
- **The dedup tiebreak was not the culprit, though it looked like it.** The
  first read of "same key, different value" was that two raw copies with equal
  `_socrata_updated_at` and `_ingested_at` let each engine pick a different
  winner. The raw zone has exactly one row for that permit. Worth recording
  because the tiebreak genuinely is only total up to those two columns, so that
  failure mode remains available and has simply not happened yet.
- **Billing does not clear a sandbox expiry.** PLAN-4 step 1 assumed attaching
  billing removes the 60 day table expiration. It does not: the expiry lives on
  the dataset as `default_table_expiration_ms`, it survives the sandbox, and
  `raw_datasf` still had it. Every table `load.py` recreated in there was on a
  countdown, including the ones this session just loaded. Cleared with
  `bq update --default_table_expiration 0 raw_datasf`. Any dataset created
  later, `dbt_dev` included, needs the same check.
- **The four "orphaned" tables in `raw_datasf` were not really orphans.** They
  are the same four table names `load.py` writes, so step 3's load truncated and
  replaced them, which absorbed step 4. The consequence is that the 8.8M row
  pre-ADR-4 `raw_311_cases` is gone and is not reproducible from the zone, which
  only holds 2024 onward. That was a human decision taken with the tradeoff
  stated. `raw_datasf` still holds materialized raw tables; emptying it is
  step 7.
- **The service account cannot see the bucket.** `gs://<bucket>` was
  created under a human identity and never shared with the pipeline service account, which
  gets 403 on `storage.buckets.get`. Steps 5 to 9 are all blocked on one
  `add-iam-policy-binding`, recorded in the plan.

---

# 2026-08-01

## The decision, made by measurement

PLAN-4's open question was how DuckDB should read a `gs://` zone, and it had
been left open on purpose. Three routes, all three tested:

| Route | Credentials | Dependencies | Full scan, 3 largest tables |
|---|---|---|---|
| DuckDB httpfs, native | +1 HMAC key pair | none | not reachable, see below |
| fsspec plus gcsfs | reuses the service account | +2, ceiling on one | 8.83s |
| local disk, for reference | n/a | n/a | 1.48s |

httpfs is not a live option: `create secret (type gcs, provider
credential_chain)` is rejected outright, and a bare gcs secret returns 403
against a service account, because httpfs reaches GCS through the S3-compatible
interoperability layer and wants HMAC keys. So the real choice was fsspec versus
a fourth option, keeping GCS as the record and syncing a local cache.

Chose fsspec. The cache option was rejected for a specific reason rather than on
taste: it creates two copies with nothing to detect divergence, which is the
exact failure this repo had just spent a session fixing in the derived zone. A
version ceiling that fails at install time is a better trade than a cache that
can be silently stale. ADR-9 records all of it.

## Findings

- **`roles/storage.objectAdmin` does not include `storage.buckets.get`.** The
  first test of the grant failed with 403 and looked like the grant had not
  landed. It had; the probe was wrong. `client.get_bucket()` fetches bucket
  metadata and needs a permission the pipeline does not have and does not want,
  while `client.bucket()` plus object reads and writes all work.
  `publish/export.py` already used the right one, by luck rather than design.
- **DuckDB names a registered fsspec filesystem after the object's first
  protocol, which for gcsfs is `gs` and not `gcs`.** The idempotency guard in
  `register` checked the wrong name, so it re-registered and DuckDB rejected it
  outright. The name is now read off the filesystem object.
- **BigQuery external table URIs must be `<table>/*.parquet`, not `<table>/*`.**
  The run manifests live at `<table>/_runs/*.json`, inside the table directory,
  and a bare wildcard picks them up. The failure is
  `Incompatible partition schemas` on the whole table, which does not point at
  the JSON at all. This is now a constraint on the zone layout, not just on the
  loader.
- **Hive partitioning needs `mode=STRINGS`.** BigQuery otherwise infers DATE
  from `ingest_date=2026-07-31` and puts a non-STRING column in an all-STRING
  raw table. This is the exact counterpart of DuckDB's `hive_types_autocast = 0`,
  and the two now sit in code that says so.
- **`raw_ingest_runs` cannot be an external table.** The manifests are JSON
  arrays rather than newline-delimited JSON, so it stays materialized. 19 rows,
  and the only exception in the dataset.
- **`make publish` to a bucket costs 2,885 Class A operations.** The free tier is
  5,000 a month, so a second publish leaves it. It also took 6 minutes 39 for
  17 MB, because the cost is per object and `mart_activity_by_h3` partitions into
  thousands of small ones. Nothing is broken; the "cost is zero" claim just is
  not literally true above one publish a month.
- **The gcsfs ceiling fails late, not at install.** `pip install gcsfs` resolves
  2026.x, which needs `google-cloud-storage>=3.11` against dbt-bigquery's `<3.2`.
  Pinning the storage library back down leaves an importable gcsfs that raises
  `ModuleNotFoundError: google.cloud.storage.asyncio` at first use. Both bounds
  have to be pinned together, which is why both are now in requirements.txt.

# Second session, same day: the writer

Follow-up 1 above, done in the same day. PLAN-4 steps 6, 8 and 11. Steps 1 to 5,
7 and 9 were already done and were not revisited.

## The decision that had to be made rather than assumed

**Does a remote run still write `data/raw`? No. A run writes one zone, and the
zone is whichever one the environment names.**

PLAN-4 step 6 described `data/raw` as becoming "a local cache rather than the
record", and that wording is wrong, so the step is annotated rather than
followed literally. A cache implies something keeps it in step with the record,
and nothing would. ADR-9 already considered and rejected exactly this: option D
was GCS holding the record with a local copy synced down, rejected because it
creates two copies with no mechanism to detect divergence, which is the failure
this repo had just spent a session fixing in the derived zone. Writing both
zones on every run would be that same arrangement arrived at from the other
direction, and worse in one respect: the local copy would exist only on the
machines that happened to run `make ingest`, so "the local copy" would mean
something different on the laptop, on a runner, and on a colleague's clone.

So there is no mirror. `data/raw` and `data/derived` are the zones when no URI
is set, which is the default, all of CI, and every fresh clone; the bucket is
the zone when a URI is set. After a remote run the local directories hold
whatever the last local run left there, which as of tonight is 395,947 rows
fewer than the bucket. That is not staleness to be repaired, it is a different
zone. Recorded in CLAUDE.md, in `ingest.py` and `remote.py`'s docstrings, in the
Makefile header and in `.env.example`.

## Findings

- **`type=Path` on `--raw-root` was the silent full backfill, sitting in the
  open the whole time.** `Path("gs://b/raw")` collapses to `gs:/b/raw`, so
  `has_data` matched nothing, `read_watermark` returned None, and
  `resolve_watermark` fell through to `start_date`: 8.8 million rows for 311, no
  error at any point. Demonstrated rather than reasoned about, before any write
  code was touched: the same call returns None through `Path` and
  `2026-07-31T10:09:19.346Z` through `remote.zone_root`. It was inert only
  because writes raised; it would have gone live in the same commit that made
  them work. Four CLIs had it.
- **`--full-refresh` cannot be made atomic on object storage, so it refuses.**
  Locally it renames a finished tree into place, which is why ADR-4 grants it
  the one exception to append-only. GCS has no rename and no multi-object
  transaction, so the same swap becomes delete-everything then copy-everything,
  and any failure inside that window leaves a raw zone that is neither tree and
  that nothing can reconstruct. Doing it anyway would turn a bounded exception
  into an unbounded one. It now exits before the first API call with the
  local-refresh-then-upload recipe in the message. Checked at the start and not
  at the swap, because the alternative is discovering it after refetching 8.8
  million rows.
- **Deleting the cache step was the easy half of step 8.** The half that
  mattered was deleting `RAW_ZONE_DIR` from the job's `env:` block. DIR beats
  URI by design, so leaving that line would have sent the entire workflow back
  to a local zone on an ephemeral disk while every URI variable sat there
  looking correct: the cache would be gone, the backfill risk would be back, and
  the workflow would appear to be using the bucket.
- **Nothing about the zone layout had to change.** Hive partitioning recovered
  from the object prefix, `union_by_name` across files with different column
  sets, `_runs/*.json` read by `read_json`, BIGINT round trips, and
  append-not-replace all behave identically on GCS. Verified against a scratch
  prefix before the real zone was touched, which is also how the writer was
  proven without risking the zone.
- **"Incremental" is not a synonym for "small" here.** One day after the last
  sync, `business_locations` had 364,774 rows waiting against a table of
  729,403: DataSF bulk-refreshes it, which bumps `:updated_at` on rows whose
  contents did not change. The nightly job is not a few thousand rows, and the
  raw zone grows by most of a copy of that table whenever DataSF touches it.
  Nothing is broken, and the append-only zone plus staging deduplication handles
  it exactly as designed, but the growth rate is worth knowing before the 10 GB
  revisit threshold in ADR-9 gets quoted as far away.
- **There is a `.DS_Store` in `gs://<bucket>/raw/`**, left by the one-off hand
  sync that seeded the bucket. Harmless: every reader globs `**/*.parquet` or
  `*/_runs/*.json`, and the BigQuery external URIs are `<table>/*.parquet`, so
  nothing sees it. Left in place rather than deleted, because deleting objects
  from someone's bucket is not a thing to do in passing.

## Correction, same day, after the first manual run

**`ingest.yml` was committed with a workflow that could not start.** The first
`workflow_dispatch` on `branch1` failed at validation, before any step ran.

The job-level `env:` block had
`GOOGLE_APPLICATION_CREDENTIALS: ${{ runner.temp }}/sa.json`, and **the `runner`
context does not exist at job level.** It is available from `steps` downward
only; `jobs.<id>.env` admits `github`, `needs`, `strategy`, `matrix`, `vars`,
`secrets` and `inputs`, and nothing else. GitHub rejects the file with
`Unrecognized named-value: 'runner'`.

It was introduced by hoisting the line out of the BigQuery step, where it had
always been and where it was legal, into the job block so that the ingest and
spatial steps could see it too. The hoist was the right idea and the wrong
mechanism. Fixed by exporting it from the key-writing step instead:

    echo "GOOGLE_APPLICATION_CREDENTIALS=$RUNNER_TEMP/sa.json" >> "$GITHUB_ENV"

which reaches every later step, keeps the key in `RUNNER_TEMP` rather than in
the workspace, and uses the `RUNNER_TEMP` shell variable rather than the
`runner` expression context. The job block now carries only the two zone URIs,
which are `secrets` references and legal there.

Two things worth taking from it. **A workflow that fails validation fails
whole**, so nothing ran, nothing was written to the bucket, and there is no
partial state to unpick; that is the good version of this mistake. And
**`make check` cannot catch it**: nothing in the repo parses workflow files
against GitHub's context rules, and `yaml.safe_load` succeeds on the broken file
because it is valid YAML and invalid Actions. The audit that found it was a
one-off script comparing job-level `env` blocks across all three workflows;
`dbt.yml` references `runner.temp` correctly at step level and `ci.yml` uses
`github.workspace`, which is legal at job level, so `ingest.yml` was the only
file affected.

## Second correction: the run after that one failed too, for an unrelated reason

`JSONDecodeError: Expecting value: line 1 column 1 (char 0)`, which is what
`json.loads("")` says. Reproduced locally rather than guessed at, by pointing
`GOOGLE_APPLICATION_CREDENTIALS` at a zero byte file: the traceback matches
frame for frame and ends at `gcsfs/credentials.py:162`, in `_connect_token`,
which reads the token file as JSON.

**The `GCP_SA_KEY` repository secret is empty or unset**, so
`printf '%s' "$GCP_SA_KEY"` wrote a zero byte `sa.json`. Not a regression from
this session's work: the same secret was always required, and the only thing
that changed is when it is first needed. Ingestion now touches the bucket, so
the failure surfaces in the second step rather than in the BigQuery load at the
end. It is consistent with PLAN-1's "the scheduled workflow goes green" box
never having been ticked; that workflow may never have had a green run.

The interesting part is the error, not the cause. A missing credential
presented as a JSON parse error from inside a library, with no mention of a
credential, a file, or a secret anywhere in the message. Anything that reads a
secret from a file needs to check the file is what it claims before handing it
to a library, because the library will report the symptom rather than the cause.
The key step now checks for a zero byte file and for `"type": "service_account"`
and fails with a sentence naming the secret and where to set it. `grep -q`, so
no part of the key reaches the log. Tested by extracting the step's `run:`
block out of the YAML and executing it against an empty secret, a fragment of a
key, and a well formed one: exits 1, 1 and 0.

## Third correction: a space is not nothing

The run after the credential fix got further and is worth reading as a result
rather than a failure. `Raw zone: gs://***/raw` printed, the 311 watermark came
back from the bucket, and ingestion resumed from it. **That is the whole storage
path working from CI for the first time**, credentials, bucket, watermark and
all. What broke was Socrata.

    requests.exceptions.InvalidHeader: Invalid leading whitespace, reserved
    character(s), or return character(s) in header value: ' '

`SOCRATA_APP_TOKEN` was set to a single space, because **GitHub will not store
an empty secret value** and SETUP.md told the reader to "create the secret with
an empty value if you skipped it". That instruction cannot be followed, so the
obvious substitute is a space, and `" "` is truthy in Python:

    headers = {"X-App-Token": app_token} if app_token else {}

sets the header to `" "`, and `requests` refuses to send it. The token is
optional and this is a request that never needed a header at all.

Three things wrong, all three fixed. The advice in SETUP.md was unfollowable and
now says not to create the secret at all. The code treated "whitespace" and "a
token" as the same thing and now strips before deciding, which also covers the
likelier future version of this: a real token pasted with a trailing newline
would have failed identically, and would have been much more confusing because
the secret would look correct. And SETUP.md now says why the token is worth
having in CI even though it is optional: anonymous Socrata limits are per IP,
GitHub's hosted runners share IPs across customers, so the anonymous budget is
one you neither own nor can see.

Tested by building the header for all five shapes the variable arrives in and
letting `requests` validate each: absent, empty, one space, token with a
trailing newline, clean token. The first three go out anonymous, the last two
carry a clean token, and none raise.

The pattern across all three corrections in this session is the same and worth
naming: **each one reported its symptom in the vocabulary of the library that
noticed, not the thing that was wrong.** An invalid workflow context said
"Unrecognized named-value". A missing credential said "Expecting value: line 1
column 1". An unset optional token said "Invalid leading whitespace in header
value". None named the secret, the file, or the setting at fault. Guarding the
boundary where external configuration enters the program is what turns those
back into sentences, and it is cheap: two `if` statements and a `.strip()`.

## Fourth correction: an invalid app token is worse than no app token

`403 Forbidden` from Socrata on 311, after the whitespace fix. Diagnosed by
running the failing request twice from here rather than by reasoning about it:

    anonymous (no token)  -> HTTP 200
    with the .env token   -> HTTP 403 {"code": "permission_denied",
                                       "message": "Invalid app_token specified"}

**The token is invalid, and the request works fine without one.** That is the
finding worth keeping: an app token is optional, so setting it to something
wrong converts a working anonymous request into a refused one. It is the only
piece of configuration in this project where supplying a value is worse than
omitting it.

Cause is almost certainly the wrong value pasted. Socrata's developer settings
page issues an App Token and a Secret Token; `X-App-Token` takes the first, and
the value in `.env` is 49 characters against an App Token's usual 25. This was
induced by advice given earlier in this same session, which said to get a token
and pointed at that page without saying that the page hands out two things.

Also worth noting because it is the reverse of the previous correction: the
earlier runs succeeded *because* `SOCRATA_APP_TOKEN` was empty. Filling it in
is what broke ingestion.

Three fixes. `_check_app_token` inspects the response before
`raise_for_status`, so a rejected token is reported as a rejected token and
names the variable, the fact that the token is optional, and the App-versus-
Secret distinction. It raises `RuntimeError` rather than exiting, which matters
for two reasons that were checked rather than assumed: `RuntimeError` is not in
the retry loop's `except` clause, so it fails immediately instead of sleeping 15
seconds to fail three times, and it *is* caught by `ingest_one`, so the run
still writes a manifest with `status=failed` and the reason in `error`. SETUP.md
now has the two-value table and a `curl` one-liner that prints only an HTTP
status, so a token can be checked before it is trusted.

Verified: with the bad token, one immediate failure and a recorded manifest
(`status=failed`, `rows_written=0`); with the variable unset, `film_locations`
ingested 2,214 rows anonymously.

## Fifth correction: the credential-free BigQuery compile was not credential-free

The pull request to `main` failed on `dbt compile --target bigquery`, the job
whose whole purpose is to check the BigQuery dialect on a fork PR with no
secrets:

    [ERROR]: Encountered an error:
    Database Error
      [Errno 2] No such file or directory: ''

The empty path is `keyfile`, which `profiles.yml` sets from
`env_var('GOOGLE_APPLICATION_CREDENTIALS', '')`. With `method: service-account`
the adapter reads that file while building the connection, so the job that
claims not to connect was connecting.

**Could not be reproduced locally**, and that is the interesting part rather
than an aside. The same two commands, the same dbt 1.12.0, the same
`GCP_PROJECT_ID=compile-only-no-connection` placeholder, the same fresh
`target/`, no ADC file on the machine: exit 0, 193 compiled files. CI runs
Python 3.11 and this machine runs 3.14, so the resolved `google-auth` differs,
and whether the empty keyfile is opened eagerly or lazily differs with it. A
17 second pause after "Concurrency: 4 threads" locally looked like a hanging
connection attempt and was not; it is the Jinja rendering of 193 files. CI
fails in the same second it starts, which is the real tell.

Attributable to this work, though to the earlier half of it. `ci.yml` has not
changed since `d90f5c3`; `requirements.txt` changed in `6725516`, the previous
session, which added `gcsfs>=2025.5,<2026` and
`google-cloud-storage>=2.14,<3.2` for the GCS reader. This pull request is the
first CI run since, and those bounds constrain what `google-auth` resolves to
on 3.11. ADR-9 recorded the `gcsfs` ceiling as a cost that "will need attention
the first time dbt-bigquery relaxes its bound"; this is a second, unrecorded
edge of the same ceiling, and it landed on the one job that has to work without
credentials.

Two changes, both verified in both directions rather than one:

- `profiles.yml` picks the method instead of hardcoding it:
  `service-account` when `GOOGLE_APPLICATION_CREDENTIALS` is set, `oauth` when
  it is not. `oauth` looks for ambient credentials rather than a named file, so
  nothing is opened and nothing raises until something actually queries, which
  is what `dbt compile` needs. Verified with credentials that `dbt debug
  --target bigquery` still reports `method: service-account` and
  `Connection test: OK connection ok`, so the real BigQuery path is untouched.
  Verified without credentials that parse and compile both exit 0 and render
  193 files.
- `audit_run_results.sql`: the two `on-run-start` hooks return an empty string
  on `compile` and `parse`. They are plain DDL, `create schema if not exists`
  and `create table if not exists`, so they made every invocation open a
  connection including compile. `log_run_results` on the other end was already
  guarded; these two never were. Independent of the version question and
  correct on its own: there is nothing to audit on a compile.

**That was not enough, and the second attempt is the instructive one.** With
the two `on-run-start` hooks guarded, CI failed again, differently:

    Failed to authenticate with supplied credentials
    Your default credentials were not found.

The macro count had gone 590 to 591, so the guard was deployed and running; the
`oauth` fallback was reached and dbt was still opening a connection. The new
message is strictly better than `Errno 2` on an empty path, which is the only
thing the profile change bought on its own.

So the connection was never mainly about the on-run-start hooks. Three separate
things open one during a compile, and all three had to go:

  - **relation cache population.** dbt lists schemas before running. Turned off
    with `--no-populate-cache`, which is not an optimisation here but the point.
  - **the two on-run-start hooks.** Plain DDL, guarded first, necessary but not
    sufficient.
  - **the on-run-end hook.** The one that was actually still connecting, and the
    one whose guard looked correct. `log_run_results` skipped on
    `not execute or not results`, and a compile satisfies neither: `execute` is
    true and `results` holds one entry per compiled node, so it rendered a real
    INSERT. The existing guard was written for parse time and read as though it
    covered every non-run command.

Two lessons, and the second is the one worth carrying.

The comment in `ci.yml` said "Compiling does not open a warehouse connection".
It was written as an explanation rather than as an assertion, so nothing noticed
when it stopped being true. **A comment claiming a property that a dependency
bump can revoke is a test that never runs.**

And `ci.yml` restated the two dbt commands instead of calling
`make compile-bigquery`, so the Makefile target and the CI step were free to
drift, and they did: fixing one did not fix the other, and the local
reproduction attempt was running different commands from the failing job
without that being visible. This repo already has the rule that solves it,
stated in CLAUDE.md for the lint scripts: the script is the implementation and
pre-commit, the Makefile and CI call it rather than restating it. The dbt
commands were the exception. The CI step is now `make compile-bigquery`.

## Correction, 2026-08-02

The third bullet above is wrong. `dbt compile` does not run `on-run-end`, or any
hook, in dbt 1.12: `safe_run_hooks` is reached only from `RunTask`, and
`CompileTask` inherits a `before_run` that runs none. The `log_run_results` guard
was therefore not what was still connecting, and the change did not fix the job,
which failed again on the next run with a clearer message. See 2026-08-02 for
what the error text actually identifies and for the fix, which stops defending
the "compiling opens no connection" property rather than trying to restore it.
The guards themselves are kept, for the plain reason that there is nothing to
audit on a compile.

---

# 2026-08-02

## The failure

    dbt compile --target bigquery --no-populate-cache
    Found 22 models, 171 data tests, 3 operations, 16 sources, 591 macros
    Concurrency: 4 threads (target='bigquery')
    [ERROR]: Encountered an error:
    Database Error
      Runtime Error
        Failed to authenticate with supplied credentials
        error:
        Your default credentials were not found.

The macro count of 591 confirms the 2026-08-01 guard was deployed, and the
command line confirms `--no-populate-cache` was in effect. Both changes from the
previous session were live, and the job still failed.

## Findings

**The error names its raiser, and it is not a query.** `Database Error` wrapping
`Runtime Error` is the signature of `FailedToConnectError` (a `DbtDatabaseError`)
carrying the string form of a `DbtRuntimeError`. The only place that shape is
produced is `BigQueryConnectionManager.open`, which catches everything and
re-raises `FailedToConnectError(str(e))`; the inner exception is the
`DbtConfigError` raised by `_create_bigquery_defaults` when `google.auth.default`
finds nothing. So the failure is BigQuery client construction, not a statement
being executed. Something resolved the lazy connection handle. That is a much
narrower claim than "a hook ran", and it was available from the log text alone in
the previous two sessions.

**`dbt compile` does not run hooks in dbt 1.12, so 2026-08-01's diagnosis was
wrong.** `safe_run_hooks` is a method of `RunTask` and is reached only from
`RunTask.before_run` and `RunTask.after_run`. `CompileTask` extends
`GraphRunnableTask` directly, inherits its `before_run` (which is
`connection_named("master")`, `defer_to_manifest()`, `populate_adapter_cache()`
and nothing else), and overrides `after_run` only to pop the inline node before
calling a `super().after_run` that is `pass`. The on-run-end hook named in the
previous note as "the one that was actually still connecting" never ran on that
command. The guard added for it is still correct on its own terms, because there
is nothing to audit on a compile, and it is kept for that reason and no other.

**The caller that resolves the handle is still unidentified, and this note is not
going to pretend otherwise.** Ruled out by reading dbt 1.12 and dbt-adapters
1.24.5: hook execution, relation cache population (`populate_adapter_cache`
returns before touching the adapter when `--no-populate-cache` is set, and that
flag is accepted on `compile` because it is part of `global_flags`),
`defer_to_manifest`, connection release and `cleanup_all` (both return early on a
connection still in `INIT`), and every macro in this project, none of which calls
`run_query`, `statement`, or `adapter.get_relation`. Not ruled out: something
inside per-node compilation, and the possibility that the version resolved in CI
differs in some detail from the one read here. The next occurrence will have the
uploaded `dbt.log`, which carries the traceback the console omits, and that will
settle it in one look instead of a session.

**The invariant was untestable on the machine that kept changing it.** A shell
that has sourced `.env` has `GOOGLE_APPLICATION_CREDENTIALS` set, so the profile
takes the `service-account` branch and every connection dbt opens quietly
succeeds. A shell that has not still has a gcloud ADC login on this machine, so
the `oauth` branch succeeds too. `make compile-bigquery` therefore passed locally
under both conditions while failing in CI under neither. To reproduce the CI
condition before this change:

    env -u GOOGLE_APPLICATION_CREDENTIALS CLOUDSDK_CONFIG=/nonexistent \
      GCP_PROJECT_ID=compile-only-no-connection make compile-bigquery

**The fix is to change which invariant is being defended.** "Compiling never
opens a connection" is a property of dbt's internals. This project does not own
it, cannot test it locally, and has now had it revoked twice by upgrades.
"Opening a connection costs nothing" is a property this project does own:
building a BigQuery client from a service account key is local, google-auth
parses the PEM and fetches no token until a request is made, and compiling makes
none. If dbt opens a connection, it succeeds and reaches nothing. If dbt ever
issues a real query on a compile, it fails at token fetch with a message about
that query, which is a better failure than the one this session started with.

The compile gate remains what it claimed to be: no secrets, no network, and the
one cross-engine check a fork pull request can run.

---

# 2026-08-03

## The compile does open connections. 197 of them.

The `dbt-bigquery-compile` job on `2034062` uploaded its `dbt.log`, and that
artifact settles it. Counted over the whole file:

| Command | `Acquiring new bigquery connection` | `Opening a new connection` |
|---|---|---|
| `dbt deps` | 0 | 0 |
| `dbt parse --target bigquery` | 0 | 0 |
| `dbt compile --target bigquery --no-populate-cache` | 5 | 197 |

197 is 1 plus 196, and 196 is exactly the node count: 22 models, 171 data tests
and 3 operations. So every node opens one, and one more is opened before any
node runs.

**Which call did it, for each of the two kinds.**

The first is `master`, on MainThread, at `17:25:38.230704`, one line after
`Concurrency: 4 threads` and 54 ms before the first `Began running node`. That
position is `GraphRunnableTask.before_run`, which is `connection_named("master")`
and, with `--no-populate-cache` set, nothing else.

The other 196 are on the four worker threads, and the log pins them tighter than
the source reading did. Per node the order is always the same:

    Began running node ...
    Acquiring new bigquery connection 'model.sf_data_warehouse....'
    Began compiling node ...
    Writing injected SQL for node ...
    Began executing node ...
    Opening a new connection, currently in state init      <- here
    Finished running node ...

The open never falls between `Began compiling` and `Writing injected SQL`.
**Compilation itself opens nothing; the execute phase of each node does**, and
the handle is resolved there rather than at acquire time. 5 acquires against 197
opens is the pool being renamed per node and reopened, which is also why 192 of
the opens say `currently in state closed` rather than `init`.

Zero queries were issued on any of it. No `SQL status`, no statement text, no
traceback, and nothing cache-related.

## Both guards are load bearing. Neither is cargo. Nothing was removed.

The question was posed as which of `scripts/fake-bq-key.sh` and
`--no-populate-cache` had become redundant now that the other exists. The answer
is neither, and both halves were tested rather than argued.

**`--no-populate-cache`, tested here.** dbt 1.12 is installed on this machine,
so the flag could simply be dropped and the same command run against the fake
key. It exits 2:

    dbt/task/runnable.py: self.populate_adapter_cache(adapter)
    dbt/task/runnable.py: adapter.set_relations_cache(cachable_nodes)
    dbt/adapters/base/impl.py: self._relations_cache_for_schemas(...)
    dbt/adapters/bigquery/impl.py:426: list_relations_without_caching
    google/oauth2/_client.py: RefreshError:
      ('invalid_grant: Invalid grant: account not found', ...)

That is Google refusing the throwaway service account at token fetch. The flag
is not an optimisation and not belt-and-braces: it is the only thing between
this target and a real API call. Environment-independent, because the key is
fake everywhere, so it will fail identically in CI.

**The key, tested in CI rather than here.** The controlled comparison already
exists in the record: same profile logic, same flag, same job, key absent on
2026-08-02 and present on `2034062`. Absent it failed with `Failed to
authenticate with supplied credentials / Your default credentials were not
found`; present it is green and opens 197 connections that reach nothing. Given
the log now proves the opens are real, the key is what makes them free.

Deleting either one re-breaks the gate, in two different ways, at two different
moments. Recorded in the `compile-bigquery` header in the Makefile and in
CLAUDE.md's `scripts/` entry, both of which previously asserted this and now
cite the measurement.

## Why this could not be reproduced locally for three sessions

Trying to reproduce the no-key CI failure here produced exit 0, with `dbt debug`
reporting `method: service-account` in a shell where
`GOOGLE_APPLICATION_CREDENTIALS` was provably unset and `google.auth.default()`
provably raised `DefaultCredentialsError`.

**dbt loads `.env` itself.** `dbt/cli/main.py` line 27:

    load_dotenv(find_dotenv(usecwd=True), override=False)

`usecwd=True` searches upward from the working directory, so running dbt from
`dbt/` finds this repo's `.env` two levels up and puts the real service account
key back into the environment. Run the identical command from `/tmp` with
`--project-dir` and the method resolves to `oauth`, which is the proof.

This is a sharper version of 2026-08-02's "the invariant was untestable on the
machine that kept changing it". That note blamed a sourced shell and an ambient
gcloud login. Neither was present here. The real mechanism is that **dbt
re-sources `.env` on every invocation regardless of the shell**, so a bare
`dbt compile --target bigquery` from anywhere inside this repo passes for the
wrong reason and can never reproduce CI.

`override=False` is the saving grace and is why `make compile-bigquery` is
still trustworthy: it exports both variables itself, and an already-exported
variable wins over the file. That target is now the only local reproduction of
the CI job that means anything. The 2026-08-02 recipe of
`env -u GOOGLE_APPLICATION_CREDENTIALS ... make compile-bigquery` was never
doing what it claimed, though it happened to reach the right answer because the
Makefile overrode it anyway.

## The published-export intent was wrong, not stale

CLAUDE.md's table gave the intended state of published exports as `scheduled`.
Nothing schedules it, ADR-8 never asked for it, and 2026-08-01 measured why it
should not be: one publish is 2,885 objects and therefore 2,885 Class A
operations against a free tier of 5,000 a month, taking 6 minutes 39 for 17 MB
because the cost is per object. A daily publish leaves the free tier on day two
and breaks the zero-cost claim in the first paragraph of CLAUDE.md.

Changed to "stays manual until the upload is batched", with the arithmetic in
the prose beneath the table. This is the more useful correction of the two made
to that table: an aspiration nobody has costed reads exactly like a plan, and
the next person to see `scheduled` would have implemented it.

Also corrected in the same table: the BigQuery build row still said "run by hand
2026-07-31" and predated the external-table migration by a day. Added a row for
the scheduled ingest, which is now a real running thing and had none.

---

# 2026-08-04

### Residue item 4 is answered. Nothing was on a countdown and nothing was changed.

`dbt_dev` carries no `defaultTableExpirationMs` and no
`defaultPartitionExpirationMs`. Neither does `derived_spatial`. There was
nothing to clear, so nothing was.

The stronger check is the one worth recording, because a dataset default only
stamps tables at creation time and clearing it later would not rescue a table
already stamped. All 23 objects in `dbt_dev` were listed and none carries an
`expirationTime`. The marts are not expiring.

`raw_datasf` does still carry `defaultPartitionExpirationMs: 5184000000`, which
is the 60 days PLAN-4 step 1 found. It is inert today and was left alone: 9 of
the 10 tables there are EXTERNAL and hold no bytes to expire, and the tenth,
`raw_ingest_runs`, is not time-partitioned. It would bite the first partitioned
native table anyone creates in that dataset, which is worth knowing and is not
worth a change now.

**Item 5 has been fixed by someone since the note above was written.**
`DERIVED_ZONE_URI` is now in `.env` as `gs://<bucket>/derived`, so a
sourced shell no longer configures a remote raw zone against a local derived
one. Not this session's work; recorded because the list above says otherwise.

### PLAN-5 step 1: `city_budget` is gone

**The `kind` field now has two legal values, not three.** `nonspatial` had one
member and that member was `city_budget`, so the docstring in `datasets.py`
would have gone on documenting a value nothing could take. It now says so and
points at ADR-10, which is step 10 and is not written yet. This is the part of
the cut the plan calls the point rather than a side effect: every dataset in
the registry is now spatial.

### Prose the step's file list did not name

Three files outside the list said things that the cut made false. Fixed, and
called out here because they are a scope judgement rather than an instruction:

- `README.md` said nine datasets and carried a budget row in the tier table.
  Now eight. Its "what it does not do" bullet promised a budget mart that stays
  inside one taxonomy, which no longer exists; rewritten to say the dataset was
  cut and that spend-versus-demand is still the unanswered question.
- `SETUP.md` line 281 told a new reader to run
  `python ingestion/ingest.py building_permits city_budget`, which would now
  fail on an unknown dataset. Changed to `film_locations`.
- `tests/fixtures/README.md` described the negative and unparseable budget
  amounts under "what is deliberately nasty in here". Removed with them.

`README.md` is stale in two further ways that this session did not touch,
because they are not step 1's business and fixing them here would bury the cut:
it still says `dbt build --target bigquery` has never run, and that the remote
half of `make publish` has never run against a real bucket. Both are false as of
2026-08-01 and CLAUDE.md's table is the correct version.

### The open question was answered by finding its premise was false

The previous session's follow-up 2 asked whether cutting `street_trees` leaves
the flat `latitude`/`longitude` path covered by only a 2,214-row dataset. The
plan states the premise directly: `street_trees` is "the larger of the two
`geometry: {latitude, longitude}` flat-column datasets".

There are three, not two. `311_cases` is flat as well, using `lat`/`long`, and
it is the largest dataset in the project. Listing the registry by geometry
shape takes ten seconds and settles it:

    311_cases            FLAT lat/lon
    building_permits     geojson_point
    business_locations   geojson_point
    street_trees         FLAT lat/lon
    film_locations       FLAT lat/lon

The fixtures then confirm nothing had to move. The adversarial coordinate cases
were already split across both shapes on purpose: unparseable lat and empty lon
on `311_cases`, which is flat, and out-of-bounds Atlanta plus State Plane feet
in a degree column on `business_locations`, which is GeoJSON. The two
`street_trees` cases were a 9999 diameter sentinel and a missing plant date.
Neither is a coordinate case. **No fixture changed, and the reason it did not
is stronger than "we checked and it was fine".**

Recorded in ADR-10's consequences and in the plan's open questions, with the
false premise named rather than quietly corrected. The plan's second open
question is more useful as a wrong question with its answer than it would have
been as a right one.

### Step 2, `street_trees`, and one more file than the plan listed

The plan's file list was right except in two places, both worth writing down
because the next cut will have the same shape.

**It missed `scripts/parity-check.py`.** `STAGING_MODELS` there lists the
models compared row for row across engines and carried
`("stg_datasf__street_trees", "tree_id")`. Nothing would have failed: the
script is run by hand, needs credentials, and is not in CI, so a dangling entry
sits there until someone runs it against BigQuery and gets a confusing error.
That is exactly the kind of reference an end-to-end cut is supposed to catch
and a `make check` cannot.

**It expected a `street_tree_count` description and `not_null` test in
`_marts__models.yml`, and there was none.** The column existed on
`dim_neighborhood` undocumented and untested. So the cut was smaller than
planned, and the finding is that a mart column reached production without a
description. Not worth chasing now; worth knowing when step 13 sweeps.

### Step 3, r9, was the one that reached furthest

`RESOLUTIONS` in `spatial.py` was one line of it. `h3_r9` was a real column
threaded through `dbt/macros/point_geography.sql`,
`stg_spatial__point_geography`, `stg_spatial__pip_sample`,
`int_point_activity` in five places, `mart_film_locations`, and four yml files
including an `accepted_values` test asserting `[8, 9, 10]`.

**`mart_activity_by_h3` goes to r8, and the number decided it.** Measured on
the real local warehouse at the mart's own grain, with `street_trees` already
excluded so the comparison is against the post-cut world:

| Resolution | Mart rows |
|---|---|
| 8 | 140,342 |
| 9 | 238,742 |
| 10 | 330,960 |

r8 is 41% smaller than r9 and 58% smaller than r10, and the mart is documented
as the readable map while r10 is the membership join key. The built mart came
out at 140,163 rows, 179 under the projection, which is the projection being
computed before the rebuild rather than a discrepancy.

Cell occupancy, which is what r9 was being kept to keep checkable: 705,067
points occupied 15,773 cells at r8, 29,040 at r9, 47,627 at r10. Both tables
are now in ADR-10, in prose, where they cost nothing.

The derived zone was deleted and rebuilt rather than migrated: 506,632 point
rows against 705,067 before, 84,296 bridge rows, 39,301 population cells,
24,000 pip samples, 32 MB on disk. The oracle check agreed on all 24,000.

**The bucket-backed derived zone is now stale against this code and nothing
will say so.** There is one zone at a time (ADR-9), so `make clean-derived &&
make spatial` rebuilt the local one only. A run pointed at `gs://` will read a
zone still holding `h3_r9` and street tree rows. `check_derived.py` compares
raw row counts and will not notice a column set change. Whoever next runs
against the bucket has to rebuild it there.

### ADR-10 was written early, on purpose

The plan sequenced it as step 10. Steps 1 to 3 made every decision it records,
and by the end of step 2 the code was citing `ADR-10` in eight comments. A
forward reference to a document that does not exist is worse than an ADR
written the day its decisions were made, which is what an ADR is for.

**ADR-5 is amended, not superseded, against the plan's own wording.** The plan
and its done-when list both said to supersede ADR-5 and ADR-7. ADR-7 was
superseded. ADR-5 was not, and the reason is that ADR-10 changes exactly one
line of it, the resolution list, while ADR-5's actual decision, that H3 cells
are computed in Python as BIGINTs because BigQuery has no H3 function to
dispatch to, is still a hard constraint in CLAUDE.md. Marking it `superseded`
files a live rule under history, and CLAUDE.md's read-first order explicitly
tells a new session to read superseded ADRs "for the reasoning" and get what
holds from elsewhere. That would have buried it.

`docs/README.md` now carries the general rule: if a new ADR changes only part
of another, say so in the new one and leave the old one active.

### The READMEs were wrong in ways the cut did not cause

Asked to bring them to current state, and most of what was wrong predates this
session by days.

`README.md` claimed **"`dbt build --target bigquery` has never run"** and
**"the remote half of `make publish` has never run against a real bucket"**.
Both false since 2026-07-31 and 2026-08-01 respectively, and both are the
project's headline caveats, so a reader was being told the two most impressive
things here do not work. Replaced with what is true, including the arithmetic
on why publish stays manual. Also corrected: nine datasets to seven, 196 tests
to 148, two spatial assertions to three, and a roadmap whose first two items
were done and whose last item cited PLAN-2 for the context pack, which is
PLAN-6.

`README.md` also described the zones as `data/raw` and `data/derived`
throughout, which is the default but not the rule. It now carries CLAUDE.md's
"one zone at a time, and it is never two" in short form.

`SETUP.md` needed one fix, already made in the previous session.
`tests/fixtures/README.md` and `make_fixtures.py` both used `street_trees` as
the worked example of why a Socrata column scan cannot be trusted. That example
is a real bug that really happened and the failure mode belongs to Socrata
rather than to the dataset, so it was kept and marked as history rather than
deleted with the dataset.

# 2026-08-04, fourth session

### Findings

### The edge and vertex cases have a contract, and it is not "inside"

`point_in_ring` is the standard crossing-number test, half-open on both axes:
`(y1 > lat) != (y2 > lat)` makes an edge span `[y_low, y_high)`, and
`longitude < crossing_x` counts a crossing only strictly east of the point. A
point exactly on an edge therefore has no right answer, and the existing
docstring says as much. Asserting today's floating-point outcome would have
frozen an accident, so what the tests assert is the property the callers
actually need:

- **Never both.** In a set of polygons that do not overlap, at most one claims
  a point on a shared edge or vertex. A point claimed twice fans out every join
  built on the bridge table, which is the worst failure available in ADR-6's
  design.
- **Exactly one** on an edge or vertex interior to the covered region.
- **Possibly none** on the outer perimeter of the region. This is the part
  worth writing down: it was measured, not assumed, and it means the obvious
  tightening of that test to `== 1` is wrong. A point on the northern edge of
  the tiling is claimed by nobody.

Those assertions would still pass for an implementation that picked the
opposite side of every edge, which is the point of writing them that way. Which
side it picks today, east of a vertical edge and north of a horizontal one, is
recorded in the test docstrings and deliberately not asserted.

### Mutation testing, because 95 passing tests prove nothing on their own

Nine changes to `geometry.py`, run one at a time against the suite. Eight were
caught: both boundary conventions, the vertex rule, holes ignored, holes added
rather than subtracted, a planar shoelace in place of the spherical excess, the
missing `abs()` that would make winding order matter, and a representative
point returned without checking it is inside.

The ninth was not caught and should not have been. Deleting the `count < 3`
guard in `_ring_contains` changes no answer for any input: a one-vertex ring
has a single zero-length edge that the `y1 == y2` skip removes, and a
two-vertex ring crosses the same segment twice and cancels. It is a fast path,
not a correctness guard. Worth knowing before someone writes a test to cover
it.

### A collinear ring does not have zero area, and that is correct

The obvious degenerate-area test, three collinear points, fails against the
expectation. `ring_area_sq_km` returns 1.883054158520965 km^2 for
(0,0), (1,1), (2,2), because the formula is a trapezoid rule in
(longitude, sin latitude), where those three points are not collinear. The
sliver between the two-segment path out and the one-segment path back is real.
Worked out by hand rather than copied from the output: the terms are
`d*sin1`, `d*(sin1 + sin2)` and `-2d*sin2`, so
`A = R^2 / 2 * d * (2 sin 1 - sin 2)`. Rings that retrace themselves, and rings
whose edges all lie on one meridian, are exactly zero.

The area anchors are the closed form for a spherical quadrilateral,
`A = R^2 * dlon * (sin lat_north - sin lat_south)`, which does not involve the
summation being tested. A one-degree square at the equator is 12363.718145180046
km^2, and the implementation matches it to the last bit. A second, looser
assertion checks that closed form against 110.574 x 111.320 km, the published
length of a degree at the equator, which is what guards the tight number
against being transcribed wrong.

### The split, and where the shared pieces landed

The section-comment banners in the old file were close to the boundaries, as
the prompt said. Two things did not fall cleanly:

- **`raw_input_state` stays in `spatial.py`.** `check_derived.py` imports it
  from there, and its comment explains that importing the function rather than
  reimplementing it is what keeps the comparison honest. Moving it would have
  meant editing that import to prove nothing.
- **`dedup_sql` went to `h3_points.py`**, which is where `RESOLUTIONS` went
  too, and both are read by `boundaries.py`. That makes `h3_points.py` the base
  of the three and keeps the dependency arrows in one direction, so there are
  no cycles. The tidier home for `dedup_sql` is arguably `raw_zone.py`, which
  CLAUDE.md already calls the only thing that reads the zone. That is a change
  to a module this step was not asked to touch, so it is left for step 13.

`build_h3_population` reads `is_allocation_cell` and the comment explaining why
sits in `build_boundaries`, three hundred lines away and now in a different
file. That cross-reference is the one thing the split made worse; the comment
now names `population.build_h3_population` explicitly.

**The old module docstring said "Five tables" and listed five.** The code
writes six: `derived_point_boundary` was missing from the list, which is the
table ADR-6 exists to produce. Corrected to six in the rewrite, each line now
naming the module that builds it.

### One number moved, and the refactor did not move it

Every derived table rebuilt to identical row counts. Five of the six are
identical in content as well, checked with an order-independent fingerprint
(`sum(hash(row))`) rather than by counting rows.

`derived_h3_population` was not. Same 39,301 rows, same cells, same totals to
the last bit, and one cell differing by 4.547e-13 residents. Chasing it rather
than waving it through is what found the real answer: **the table is not
reproducible run to run, and was not before this session either.** Five
consecutive runs of identical code over an identical raw zone produced five
different fingerprints for it, including two runs with the same
`PYTHONHASHSEED`, while `derived_polygon_h3` came out byte-identical every
time.

The cause is float accumulation order. Each block group's share is added into a
per-cell running total, and the order those additions happen in comes off a
Python `set` of H3 cell strings. The evidence that the refactor is not
implicated: `build_h3_population` is textually identical to the last committed
copy, checked by diffing the function out of `git show HEAD:` rather than by
reading it, and so are `build_boundaries`, `_resolve_primary_collisions`,
`build_point_boundary`, `build_pip_sample`, `classify_coordinate` and
`build_point_h3`, the one exception being a comment pointer that had to name
its new module.

This matters for step 9. The plan's incrementality check is "compare row counts
against a full rebuild, identical and not close", and a byte comparison of this
table will never satisfy that. Either make the sum order-independent first, in
its own step, or compare counts and sums with a tolerance. It is written into
session F's prompt.

### "Before the dbt job" is a graph question

`ci.yml` has four jobs and they run in parallel, so there was no step ordering
to change. `python-tests` is a new job and `dbt-duckdb` declares `needs:` on it.
It gates that job and not `dbt-bigquery-compile` because the dependency is real
in exactly one direction: `dbt-duckdb` runs `ingestion/spatial.py`, so a
geometry failure predicts what fails there, while the BigQuery job never
executes Python geometry at all.

`pytest` floats rather than pinning. The rule `requirements-dev.txt` already
states is that ruff is pinned because a second copy of it lives in
`.pre-commit-config.yaml` and the two must be the same linter, and that sqlfluff
is not pinned because it has no second copy. pytest has no second copy, so it
floats, and the comment says what would change that.

## The remote zone, concretely

Session 2 flagged the bucket derived zone as stale without saying what breaks.
It is this: `stg_spatial__polygon_h3.resolution` now carries
`accepted_values: [8, 10]`, and a zone written before ADR-10 has resolution 9
rows in the bridge. A `dbt build` over that zone fails that test. It is a loud
failure rather than a silent wrong answer, which is the better of the two.

**It self-heals and needs no manual GCS work.** `ingest.yml` runs
`spatial.py --all` and then `load.py --all --target bigquery` against the
bucket on every daily run, and `spatial.py` replaces every derived table
wholesale. So the fix is to commit and push; the next 09:17 UTC run rebuilds
the remote zone with this code. `make clean-derived` is a local-only
`rm -rf data/derived` and has no bucket equivalent, and does not need one.

The gap to know about: between pushing and that run, `make build-bigquery`
against the bucket fails the resolution test. Either wait for the cron or run
`make spatial` once with `.env` sourced, which writes the bucket directly.

---

# 2026-08-05

## The registry: the design was forced, not chosen

PLAN-5 step 4 offered two options and said "whichever is smaller". One of them
does not work, and finding out why is the useful part of this note.

**Generate the dbt vars from the Python registry at build time** fails on
sqlfluff. Passing `--vars` on the dbt command line means every dbt entry point
has to pass it, and `scripts/sqlfluff-lint.sh` is one: the dbt templater
compiles the whole project before it lints a line, and sqlfluff has no way to
forward vars to it. So `mart_pipeline_freshness` would fail to template and
`make lint` would go red. The obvious patch, `var('pipeline_sources', [])`,
makes it worse rather than better: a missing registry then renders as a mart
with no sources and no error, which is a silent empty pipeline report. That is
the exact failure this step exists to remove, so the option is not merely
larger, it is wrong.

**A YAML both read** works, but only in one direction, and the direction is
the surprising one. dbt cannot read an arbitrary YAML file: its Jinja sandbox
has no file access and `dbt_project.yml` has no include, so a neutral
`datasets.yml` at the repo root is not reachable from dbt at all. Python can
read anything. So the single copy has to live in the file the less capable
reader already opens, and the capable one comes to it.

The registry is therefore `vars.pipeline_sources` in `dbt/dbt_project.yml`,
carrying every field both sides need, and `ingestion/dataset_registry.py` is a
loader that reads it with PyYAML and presents `DATASETS`, `point_datasets()`
and `polygon_datasets()` exactly as before. No consumer in `ingestion/` changed
beyond its import line.

### What now catches a divergence, and when

The question the step was set to answer is whether someone can still add a
dataset in one place and have `make check` pass. They cannot, and it is worth
being precise about which failure fires at which moment, because the answer is
different for each direction.

- **Adding it to the registry only.** There is no second list to forget, so
  Python picks it up with no action at all. What fails is the surface a
  dataset needs beyond the registry. `tests/test_dataset_registry.py` fails at
  **`make test-python`**, the first and fastest gate in `make check`, naming
  the missing dbt source table, staging model or fixture. If that file were
  deleted, `dbt parse` fails at **parse** on a `source()` that resolves to
  nothing, and `make ci-build` fails at **build**, at the ingest step, on
  `No fixture for ...`.
- **Adding it to dbt only.** Declaring a raw table in `_datasf__sources.yml`
  and not in the registry means a table nothing ingests, and it is the one
  direction nothing used to notice at all: the model would build, empty.
  `test_every_declared_raw_source_is_in_the_registry` fails at
  **`make test-python`**. `raw_ingest_runs` is the one deliberate exemption,
  named in that file with its reason.
- **Adding it in Python only.** Not a state that exists any more. There is
  nowhere in Python to add one.
- **A malformed entry.** `load_registry` validates on import and raises, so
  every entry point dies immediately rather than KeyError-ing partway through
  a run that has already written Parquet.

The step's acceptance test was that the two cannot disagree silently. They
cannot disagree at all, which is a stronger property and came out cheaper than
a drift check would have.

### `tier` and `stale_after_hours`

Both were dbt-side fields the Python registry never had, and both survive the
merge unchanged and unread by Python, which is fine: the registry is one list
with fields for two consumers, not a lowest common denominator. They are not
unchecked, though. `test_tiers_and_freshness_slas_agree` asserts the pair
agrees: `core` carries a positive SLA, `reference` and `demoted` carry null.
A threshold on a source nothing calls stale is a number nobody reads, and a
core source with no threshold is a source that cannot go stale.

### A third copy, found on the way

`tests/fixtures/make_fixtures.py` had its own hardcoded `DATASETS` and
`BOUNDARY_DATASETS`, names and Socrata ids both. It is worse than the dbt
duplicate that step 4 was aimed at: a fixture built from the wrong id is a
green CI run against a dataset the pipeline does not ingest, and nothing
downstream can tell. Both dicts are now derived from the registry, split on
`kind` and filtered on the presence of a `socrata_id`, which is what drops the
tigerweb entry out to `fetch_census()`. Checked against the lists they replace:
identical, both dicts.

## The rename, and the names that did not get one

`ingestion/datasets.py` is `ingestion/dataset_registry.py`. Five importers, not
the three PLAN-5 step 7 was written against: the spatial split added
`h3_points.py` and `boundaries.py` to `ingest.py`, `load.py` and `spatial.py`.
Found by grep rather than from the plan's list, which is the reason to.

**Two of the files the step named needed nothing.** Neither the Makefile nor
`.github/workflows/ingest.yml` has ever mentioned `datasets.py`; both invoke
`ingest.py`, `spatial.py` and `load.py` by path and reach the registry through
them. Recorded because the step's list reads as exhaustive and a later reader
checking it off would go looking for an edit that was never needed. What did
need editing was `ruff.toml`, CLAUDE.md, README.md, `docs/README.md` and three
comments in dbt that pointed at the old path.

`known-first-party` in `ruff.toml` now lists `boundaries`, `census`,
`dataset_registry`, `derived_zone`, `geometry`, `h3_points`, `population`,
`raw_zone`, `remote` and `spatial`. The other nine keep their names, and the
reasoning is in that file rather than only here: the hazard is not
"generic-sounding name", it is "name a package this dependency tree could
plausibly acquire". HuggingFace `datasets` is a routine transitive arrival in
anything data-adjacent, and ours would shadow it, which breaks the thing that
pulled it in rather than us. Nothing in reach of dbt, pyarrow, h3, duckdb or
gcsfs claims `boundaries`, `population`, `h3_points`, `derived_zone`, `spatial`
or `raw_zone`. `census` and `geometry` are the nearest runners-up, both real
PyPI names and `census` even thematically apt, but nothing here imports either.
Renaming six modules against a hazard with no arrival path is churn, and it
would not fix the class anyway: the only real fix is making `ingestion/` a
package, which `tests/conftest.py` argues against on its own terms. Revisit the
day one of those names turns up in `pip list`.

## Run results retention

`prune_run_results()` is a third `on-run-start` hook, after the two that create
the schema and the table. It keeps the **50 most recent invocations** and
deletes whole runs rather than rows.

A run count and not an age, and the constraint comes from the macro's own
header: the mart reports the PREVIOUS completed run, because models build
before `on-run-end` fires. A window that can hold fewer than two runs therefore
breaks `mart_pipeline_freshness` rather than pruning it, and "older than 30
days" holds zero runs after a quiet month. "The last 50 runs" cannot hold fewer
than 50 while 50 exist, whatever the calendar does. Steady state is about 8,500
rows at 170 nodes a run, and it is a ceiling rather than a trend.

Whole invocations rather than a row budget, because a half-deleted run reports
a passing build with some of its tests missing, which is worse than no history.

The number lives in the macro and not in a `dbt_project.yml` var, deliberately:
the header comment states it, and a number stated in one place and configured
in another is precisely what the first half of this session spent its time
removing.

Verified by construction rather than by reading it: three synthetic
invocations inserted beside a real one, then the rendered statement run at
n=50, n=2 and n=1. At 50 it deleted nothing, at 2 it left the two most recent
whole, at 1 it left one. `make ci-build` shows 4 project hooks where it showed
3.

**Not exercised against BigQuery.** The audit hooks return an empty string on
`compile` and `parse`, so `make compile-bigquery` never renders this DELETE,
and the first BigQuery execution of it will be the weekly `dbt.yml` cron or the
next hand-run `make build-bigquery`. The SQL is deliberately plain, and `not in`
was chosen over an anti-join because both engines take it in DML and neither
takes the same join syntax there, but it is untested on that engine and this
line is the record of it.

## `make build-bigquery` is red, on two things this session did not cause

Run after the work above, on commit `a7f726b`, because the retention hook had
never executed on BigQuery. It executed: `4 project hooks`, all three
`on-run-start` ones completing before any node ran, and `TOTAL=171` matching
DuckDB exactly. That question is answered.

The run also came back `PASS=109 ERROR=2 SKIP=60`. Both errors predate this
session, both were invisible to `make check`, and the second is a real
cross-engine defect rather than an environment problem. Diagnosed but not
fixed here: fixing them is a different change with a different verification,
and mixing them into a registry refactor would make both unattributable.

### Error 1: the bucket's derived zone was built by code that no longer exists

`accepted_values_stg_spatial__polygon_h3_resolution` failed with one disallowed
value, expected to be 9. Local `RESOLUTIONS` is `(8, 10)` and `data/derived`
was rebuilt on 2026-08-04 when ADR-10 dropped r9. The bucket's derived zone was
not, so it still holds r9 cells.

This is CLAUDE.md's "there is one zone at a time, and it is never two" behaving
exactly as that section describes. The local zone was rebuilt, the bucket zone
was not, and they are different zones rather than copies of one. The fix is one
command, because `derived_zone.py` replaces the zone wholesale on every run.

**The part worth keeping is what did not catch it.** `check_derived.py` compares
a recorded per-dataset raw row count against the raw zone as it is now, and
those agreed: the raw zone had not moved, only the code had. A schema change in
the derived zone is invisible to a row count. PLAN-5 step 9 proposes a
code-version stamp for incrementality; this is the case that makes it a
correctness guard as well, and step 9 now says so.

### Error 2: DuckDB unions the column sets, BigQuery takes one file's

`stg_datasf__building_permits` failed with `Unrecognized name: unit_suffix`.
The model is unchanged since 2026-07-31 and DuckDB builds it green.

The mechanism is a disagreement between the two readers of the same Parquet,
and half of it was already written down:

- `ingestion/raw_zone.py` reads with `union_by_name = true`, and its comment
  says why: Socrata omits null fields per record, so a batch's column set
  depends on which rows it contained and files genuinely differ between runs.
- `ingestion/load.py` sets `external.autodetect = True` on the BigQuery
  external table and has no equivalent. BigQuery infers one schema for the
  whole table from a sampled file, so a column absent from that file is absent
  from the table.

The drift is measurable locally without touching the bucket. Two files in one
partition of `raw_building_permits`: 58 columns and 56, differing by
`tidf_compliance` and `voluntary_soft_story_retrofit`. Both happen to carry
`unit_suffix`, which is why DuckDB is fine; at least one file in the bucket does
not, and that is the one BigQuery sampled.

This is PLAN-7 step 2, "assert the BigQuery external-table column sets against
DuckDB's rather than comparing them by eye", except that it has now happened.
The symptom fix is to pin inference with `reference_file_schema_uri`; the cause
fix is to stop using `autodetect` for a zone whose contract is that every
column is a STRING, and pass an explicit union schema instead. `load.py`
already opens the zone with DuckDB in `_upload`, so the union is available
where it is needed.

### What this says about the gate

`make check` is DuckDB-only and local-zone-only, which is what keeps it
credential-free on a fork pull request (ADR-1) and is not a defect. The
consequence is worth stating once: a green `make check` says nothing about the
bucket zones or about BigQuery, and neither of these two errors was reachable
from it. Sessions that touch the registry, the zone layout, a staging model's
column list or `load.py` should carry `make build-bigquery` in their checkpoint.
`docs/handoff-prompt.md` now says so as a standing note.

## The end-of-files hook, and what it actually left behind

The last commit failed the `end-of-file-fixer` hook. That hook is a fixer: it
rewrites the file and fails the commit, so the commit was re-run and went
through, and no file in the working tree, the index or `HEAD` is missing a
trailing newline today. Checked all three, byte by byte.

What it did leave is real and worth one line of attention. `docs/dbt/catalog.json`
and `docs/dbt/manifest.json` are staged in the state the hook rejected and
present on disk in the state it fixed: the index copies end in `}`, the working
copies end in `}\n`, one byte apart in each file. That is why `git status` shows
them as `MM`. Nothing is broken by it, and nothing downstream reads the index
copy, but the next commit that includes them fails the same hook again unless
they are re-added first. `git add docs/dbt/catalog.json docs/dbt/manifest.json`
clears it. Not done here, since this session does not stage.

The underlying cause is that `dbt docs generate` writes both files without a
trailing newline, so `make docs` will re-create this every time. Worth either a
newline append in the `docs` target or an exclude on the hook, which is a
PLAN-5 step 13 sized decision rather than one to make in passing.

## The two red things, fixed. One of them was already fixed.

Continues the section above, same day, on commit `6dfdba6`. `make build-bigquery`
is green: `PASS=171 ERROR=0 TOTAL=171`, matching DuckDB node for node on the
same zone. Both errors are dealt with, but only one of them turned out to need
code.

### Error 1 could not be confirmed, because it was gone

The instruction was to confirm the disallowed value was 9 before re-running, so
that the fix would be attributable. It is not 9 and there is no disallowed value:
`accepted_values_stg_spatial__polygon_h3_resolution` passes against BigQuery
untouched, and the bucket's `derived_polygon_h3` holds r8 and r10 and nothing
else. **So `make spatial` was not re-run.** Re-running it would have rewritten a
correct zone, spent a few minutes doing it, and destroyed the only evidence of
when the zone was actually built.

What the evidence is, since it is not in the zone:

- Every object under `gs://.../derived/` was written at 2026-08-05 02:20:01 to
  02:20:07 UTC, and every external table over both zones was recreated at
  02:20:30 to 02:20:58. That is one `make spatial` followed by one
  `make load-bigquery`, against the bucket, 17 minutes after commit `a7f726b`
  at 02:03 UTC.
- The bucket's cell counts match the local zone's exactly: 3,516 at r8 and
  80,780 at r10. Same code, same inputs.

So the red build recorded in the section above ran before 02:20 and someone
rebuilt the zone after diagnosing it, without amending the note. The fix was
correct and the record of it was the thing missing.

**This is a second argument for PLAN-5 step 9's stamp, and a different one from
the first.** The first was that a zone built by code that no longer exists is
undetectable. The second is that a zone built by code that now exists is equally
undetectable: "correct now" and "never wrong" are the same observation from
inside the zone, and so are "someone rebuilt it" and "someone widened the test".
Establishing which of those had happened took object mtimes and a cell count
comparison, which is forensics rather than a check. Step 9 now carries both, and
its open question about where the checker belongs is answered in favour of
`check_derived.py` over PLAN-7 step 1: it already reads the zone rather than the
warehouse, already parses the manifest a stamp would live in, and already grades
two verdicts that a third would join.

### Error 2 was the real defect, and the cause fix is nine lines

`autodetect` on a BigQuery external table infers one schema for the whole table
from a sampled file. DuckDB reads the raw zone with `union_by_name`. Measured on
the bucket zone rather than inferred: `raw_building_permits` is 59 columns to
DuckDB and was 54 to BigQuery, missing `reroof`, `structural_notification`,
`tidf_compliance`, `unit_suffix` and `voluntary_soft_story_retrofit`. It is the
only one of the seven that drifts today; the other six agree exactly.

`load.py` now computes the union column list with `_union_columns`, a `describe`
through `raw_zone.read_sql`, and passes it to `_external_table` as an explicit
all-STRING schema with `autodetect` off. Three things about that, all of which
took a test to establish rather than a guess:

- **`describe` reads Parquet footers, not row groups.** The union of a 2.19
  million row table costs a listing, so this is not a scan added to every load.
- **The hive partition key can be in the explicit schema or not, and both work.**
  `ingest_date` comes back from DuckDB's `describe`, so it is in the list.
  Checked both ways against the real table: 59 columns and four distinct
  partition values either way, so BigQuery reconciles the declared column with
  the partition key rather than looking for it inside the files. Passing the
  describe output verbatim needs no filtering step, so that is what it does.
- **`external.autodetect = columns is None`** keeps the derived zone on
  autodetect, deliberately. Its tables are one file each, written against a
  fixed pyarrow schema, and its types are real BIGINTs and BOOLEANs, so there is
  no union to take and an all-STRING schema would be actively wrong there.

`reference_file_schema_uri` was rejected rather than skipped, and the argument is
in `_external_table`'s docstring: it pins inference to one named file, which
fixes today's symptom and fails the next column Socrata adds. New columns arrive
in new files, the reference URI keeps pointing at an old one, and the zone is
append-only so that file can never grow the column either. It would need a human
to repoint it at exactly the moment nobody knows a column appeared. The union
list is recomputed from the whole zone on every load.

`load.py`'s module docstring claimed both targets read through DuckDB so the two
warehouses cannot disagree about the raw zone. That claim was false for the
external path, which is how this happened. It now says what is actually true:
BigQuery reads the Parquet itself, and what goes through the single reader is the
column list.

### PLAN-7 step 2, and where it departs from what step 2 asked for

`parity-check.py --columns`, `make parity-columns`. It compares BigQuery's
`INFORMATION_SCHEMA.COLUMNS` against the zone read through `raw_zone.read_sql`
and `derived_zone.read_sql`, per table, and names the dataset, the table and
every column on either side of a disagreement.

**Against the zone, not against the local DuckDB file, which is the opposite of
what the row mode does.** PLAN-7's own constraint forced it: "whatever these
check, they check against the zone, not against a copy of it". The local
warehouse is a copy of whichever zone `make load` last read, and on this machine
that was `data/raw`: 36,611 building permits against the bucket's 37,364. A
warehouse-to-warehouse column comparison would have reported that as a defect and
sent the reader after the wrong thing. Reading the zone is also the stronger
check and needs no local build, so it runs straight after `make load-bigquery`.

Two smaller decisions, both recorded in the script rather than only here:

- **An extra table on the BigQuery side warns and does not fail.**
  `raw_city_budget` and `raw_street_trees` are still live external tables from
  before ADR-10 cut them, created 2026-08-04 11:40 UTC. That is not a column
  disagreement, nothing references them, and nothing can break on them. A
  missing table is an error, because every model reading it fails.
- **`raw_ingest_runs` is skipped, and says so in the output.** It is the one raw
  table that is materialized rather than external, and `runs_read_sql` declares
  its columns instead of inferring them. This check exists to catch two readers
  inferring differently and that table has no inference in it.

Demonstrated in both directions, and the first direction was free: the defect was
live, so the check was written against a zone that was already failing. Then
`raw_building_permits` was recreated with `columns=None`, which is exactly the
pre-fix call, and the check went red naming all five columns, and green again
after a reload. Exit 1 and exit 0 respectively.

`ruff.toml` gained `load` in `known-first-party`. `parity-check.py` is the first
thing to import that module rather than run it, and without the entry ruff sorted
`import load` into the third-party block above the siblings it sits beside in
`ingestion/`, which reads as a dependency. The four names it imports,
`RAW_SCHEMA`, `DERIVED_SCHEMA`, `DERIVED_TABLES` and `RUNS_TABLE`, are the
alternative to a second copy of four things that have to agree.

### What `make check` cannot catch, and what catches these now

`make check` is green and was green while both of these were red, on the same
commit. It cannot catch either, and that is a property rather than a gap:
`test-python`, `lint`, `leak-check` and `ci-build` are DuckDB-only and
local-zone-only, `ci-build` sets `RAW_ZONE_DIR` and `DERIVED_ZONE_DIR` so a
sourced `.env` cannot change what it tests, and `compile-bigquery` renders SQL
without connecting to a warehouse. That is what keeps the PR gate credential-free
on a fork, which ADR-1 requires. Nothing in it reads a bucket or asks BigQuery a
question, so neither a bucket zone built by dead code nor a BigQuery table
missing a column is reachable from it.

What catches them now:

| Failure | Caught by | Needs credentials |
|---|---|---|
| External table missing a column the zone has | `make parity-columns` | yes |
| Model referencing that column | `make build-bigquery` | yes |
| Staging models disagreeing row for row | `make parity-check`, both sides built from one zone | yes |
| Derived zone behind the raw zone | `make check-derived`, a prerequisite of `make build` | no |
| Derived zone built by code that no longer exists | nothing. PLAN-5 step 9 | n/a |

The last row is the one to keep in view. It is the only failure in this table
with no check at all, and it is the one that was found this morning.

## PLAN-5 steps 9 and 12. Both closed; only step 13 is left.

Continues the same day, after the two red things above were fixed. The
derived zone now records the code that built it and rebuilds only what has
moved (ADR-11), and one publish is 7 objects rather than 2,280 (ADR-12).
Nothing committed or pushed.

### Step 9's premise was stale, and measuring first changed the work

The step, and ADR-5 before it, say the spatial precompute is about 40 seconds
per 700k points and grows linearly. Timed by phase on the 506,632-point local
zone before touching anything:

| phase | seconds |
|---|---|
| `build_pip_sample`, the exact point-in-polygon oracle | 18.86 |
| `build_point_boundary` | 1.58 |
| `build_boundaries`, 733 polygons | 1.06 |
| `build_point_h3`, the cells the step names | 0.95 |
| everything else | ~1.6 |
| total | ~24 |

So the H3 precompute is 4% of the run and the oracle is 78%, and the oracle
does not grow with the raw zone at all: it is a fixed 2,000 rows per source
tested against every polygon in a set. The 40 second figure predates the r9
cut and, more likely, a faster `h3` binding; either way it is out of date and
ADR-5 now carries a note saying so.

That reordered the work rather than cancelling it. The stamp is a correctness
guard and its value never rested on the timings. What changed is where the
caching went: the oracle sample is what makes a daily incremental run cheap,
and the point cache is a wash locally.

**The point cache is a wash locally, and that is recorded rather than hidden.**
Reading last run's `derived_point_h3` back takes 0.85 seconds against 0.95 to
recompute it from the raw zone. Both scale with the same row count, so it stays
a wash here. It is not a wash remotely, where the raw read is roughly 6x slower
(ADR-9) and the raw zone carries every superseded version while the derived zone
carries none: 902,681 raw rows against 506,632 derived ones today.
`INCREMENTAL_SHARE = 0.5` in `derived_state.py` is what keeps the wash from
becoming a loss, and the local zone, which is one partition per table, always
takes the full path because of it.

### What the stamp covers, and why it is a hash

A hash over the source of `spatial.py`, `h3_points.py`, `boundaries.py`,
`population.py`, `geometry.py` and `derived_state.py`, plus each dataset's
table, kind, grain key and geometry spec from the registry. Not `tier`,
`stale_after_hours` or `description`, which dbt reads and the zone does not.

The argument against a version constant is asymmetry and not aesthetics. A
constant is quiet through a comment edit, which a hash is not, and it is silent
when someone forgets to bump it, which is the failure of this morning exactly.
A spurious rebuild costs 23 seconds. The full argument is in
`derived_state.py`'s header, where someone hitting the surprise will be.

The manifest carries readable fields beside the hash, because a hash can only
say "different". With them the checker says `resolutions: zone built for
[8, 9, 10], code computes [8, 10]`, which is this morning's failure named at
its cause rather than four models downstream of it.

### `raw_input_state` moved, against a decision recorded on 2026-08-04

The split note said it stays in `spatial.py` because `check_derived.py` imports
it and moving it would prove nothing. It now has two siblings with the same
property, so `derived_state.py` holds all three and both sides import it. The
part that is more than tidying: the checker no longer imports the module whose
source it is checking, and it reads that source as bytes, so it still answers on
a `spatial.py` that does not parse.

### Verification, including the half that found a bug

The done-when box asks for less work on a second run. That is 23.2 seconds to
0.3, with all six Parquet files byte-identical afterwards rather than rewritten.

The harder half is that it is still correct, and it was done on a scratch copy
of the real raw zone rather than on fixtures: full build, then a synthetic daily
partition of 800 rows (500 new keys, 300 existing keys re-ingested with a newer
`:updated_at` and moved coordinates), then an incremental run, then
`clean && spatial` over the same raw zone. **All six tables agree row for row,
not only in count.** Repeated for the boundary path, by adding a partition to
`raw_analysis_neighborhoods`, which rebuilds the boundaries and reuses the
points: same result.

The first incremental run crashed, which is the useful part. A table with no
changed partitions was asking `read_sql` for an empty partition list, which is
refused deliberately so that "read nothing" can never be mistaken for "read
everything". The fix is that an unchanged table does not touch the raw zone at
all, which is the case a scheduled build hits for every dataset but the one it
just ingested.

`tests/test_derived_state.py` covers the decision table directly, 20 cases.
Two of them failed on the first run and both were the test being wrong, and one
of those is worth keeping: a single partition holding a whole table can never
take the incremental path, because any change to it is a change to most of it.
That is the local zone's shape, so the local zone always rebuilds whole.

### `derived_h3_population` is not reproducible bit for bit, confirmed

Two full builds over an identical zone differ by 4.55e-13 residents on a few of
the 39,301 cells. Each block group's share is summed into a per-cell float in an
order that comes off a Python set of H3 cell strings, and string hashing is
salted per process. Totals are exact and every other derived table is
byte-stable, which is why the comparison above is rows and not Parquet bytes.

The pleasant surprise: **incremental runs are more reproducible than full ones
here**, because the table is reused rather than recomputed when the boundaries
have not moved, and reuse is exact. Making the sum order-independent is still
worth doing if anyone wants a byte comparison as the incrementality check, and
it is its own change with its own verification.

### Step 12 was a measurement problem, and the measurement was not the one expected

The object count is real but the deciding number was the byte count. For the two
partitioned marts, on the real warehouse:

| layout | objects | bytes |
|---|---|---|
| by month, as built | 2,275 | 11.0 MB |
| by year | 248 | 4.2 MB |
| one file per mart | 2 | 1.9 MB |

Month partitioning cost 5.8x the bytes of the same data. The median monthly
partition held 40 rows, and a 5 KB Parquet file is mostly footer, schema and
dictionary pages with nothing for compression to work across. So it was not a
trade of bytes for pruning; it was worse on both. Year partitioning is a real
improvement and still leaves 248 objects, over the plan's bar of 200.

Two numbers in the step's text were measured differently here: 2,280 objects
rather than 2,885, and a date range starting in 1849 rather than 1967. Neither
changes the argument.

**Flooring the date range was rejected rather than deferred.** 65.6% of
`mart_activity_by_h3`'s rows are dated before 2020, so it is a scope cut wearing
a partitioning fix's clothes, and it belongs in its own ADR if anyone wants it.

One publish is now 7 objects and 3.0 MB. `MANIFEST_VERSION` is 2 and the
published paths changed, which breaks a consumer of the single hand-run upload
from 2026-08-01. The `partition_by` field and its code path stay, unexercised,
which is a real cost and is written into ADR-12 rather than argued away; step 13
is where to decide whether it earns its keep.

### One late addition: `--sample-size` is recorded in the manifest

Found by reading the finished `main()` rather than by a failure.
`--sample-size` is an input to the derived zone that the code stamp cannot
cover, because it arrives on the command line rather than in the source, so
`spatial.py --all --sample-size 5000` on a current zone would have printed
"nothing to do" and left the 2,000-row sample. The size is now recorded on the
`derived_pip_sample` manifest entry and a change to it forces a full rebuild.
Verified in both directions, 2000 to 500 and back.

## PLAN-5 step 13, the obsolescence sweep. PLAN-5 is closed.

The last step, and the last session of the plan. Read every README, module
docstring, function header, Makefile target comment and ADR pointer against the
code as it is now.

### The finding: the code was clean and the documents about the code were not

This is the part worth carrying forward, because it is the opposite of what the
step expected to find. Every module docstring in `ingestion/`, every Makefile
target comment, every dbt model header and the whole `vars.pipeline_sources`
comment block already described the code as it is. Nothing in any of them
needed correcting except two plan pointers.

That is not luck. Steps 1 to 12 each updated the header sitting next to the
code they changed, in the same change. What drifted instead was the three
places that describe the project from outside it and that no single step owned:

- **USER-NOTES.md.** The worst of them, and stale in a way that was invisible
  because its top half had been kept current. It still carried the nine-dataset
  table with `street_trees` and `city_budget` in it, "22 models and 171 data
  tests ... 196 nodes", a four-mart list including
  `mart_budget_by_department_year`, and "the dataset registry lives in
  `ingestion/datasets.py` **and** as `var('pipeline_sources')` ... adding a
  dataset means adding it in both places", which is the exact failure step 4
  removed. It also carried a paragraph flagging a disagreement with CLAUDE.md
  over "5 city marts" against "4 in the tree"; both numbers were wrong by then
  and the answer is 3.
- **SETUP.md.** Described a two-layer dbt project (`staging` and `marts`, no
  intermediate), told the reader to check their work in the BigQuery console
  for "a `raw_datasf` dataset containing four tables" when ingestion writes
  Parquet and the default target is DuckDB, and set as the first task writing
  `stg_datasf__film_locations`, which has existed since PLAN-3. **It had no
  spatial step in it at all**, so following it top to bottom produced empty
  marts. Its troubleshooting section told a reader that a 403 means the service
  account is missing BigQuery Admin, which section 1.3 of the same file tells
  them to remove.
- **`_spatial__sources.yml`.** `derived_point_h3` still described as carrying
  cells "at resolutions 8, 9 and 10". The only live r9 claim left in the repo.

**The rule that follows is the one step 4 already found, arriving in a
different shape.** A fact written next to the code it describes gets corrected
by whoever changes that code. A fact written in a document about the code does
not, and nothing checks it. Step 4 removed a second copy of the dataset
registry for that reason; this step found three second copies of the
architecture. The mitigation is the same and is already in the plan's own
wording: put context at the top of the file or function it concerns, and treat
any document that restates it as the copy that will rot.

### One real defect in CLAUDE.md, found by counting rather than reading

CLAUDE.md said "Eight logical macros now, against ADR-1's revisit threshold of
about ten". `cross_engine.sql` defines nine in the surface models call:
`x_type`, `x_cast`, `x_safe_cast`, `x_safe_int`, `x_json_extract_scalar`,
`x_utc_now`, `x_month_start`, `x_safe_divide`, `x_hours_between`. Six of those
dispatch per adapter and three are wrappers over ones that do, which is
probably where "eight" came from, but no reading of "logical" gives eight.

It matters because the number is a tripwire rather than a statistic. ADR-1 asks
to be re-read when the layer "passes about 10 macros, meaning the engines have
diverged more than this decision assumed". At eight there is room for two more;
at nine the next macro is the one that triggers it. Corrected in CLAUDE.md and
USER-NOTES.md, with the count spelled out so the next reader can check it in a
grep rather than trusting it.

### The known candidates, and what happened to each

- **`docs/review-2026-07-31-scope-and-cloud.md`: kept, with a harvest table.**
  `docs/README.md` conditions its deletion on PLAN-4, PLAN-5 and PLAN-6, and
  PLAN-6 is `draft` and unstarted, so deleting it now would break a rule the
  repo wrote for itself over a file that git holds anyway. It now opens with a
  table saying where each of its recommendations landed, which is what stops a
  reader mistaking a snapshot of 2026-07-31 for a description of the repo. Its
  one recommendation still open and still small: keyset paging instead of
  `$offset`, confirmed still unimplemented in `ingest.py`. Deleting the file is
  a one-line follow-up whenever PLAN-6 closes.
- **`docs/handoff-prompt.md`: kept and cut from 748 lines to about 200.** It
  deletes itself when PLAN-6 and PLAN-7 close, and both are open, so it stays.
  What went was six archived session prompts marked "kept for the record only",
  which is precisely the "only records that work happened" the step says to
  delete: what each session did is in the dev note for its date, and what it
  decided is in the plan step or ADR it produced. What was lifted out of them
  into the standing notes: the two prompt-writing lessons (make a session
  confirm the defect before fixing it; point it at the specific docstring that
  constrains the change) and the orphaned-BigQuery-tables item.
- **`USER-NOTES.md`: corrected, not deleted.** It earns its place as the
  read-outside-the-repo document, and everything wrong with it was factual
  rather than structural.
- **`SETUP.md` against CLAUDE.md: SETUP.md corrected, per the rule.** It is now
  seven phases with the spatial precompute as its own phase 4, and its opening
  banner says plainly that CLAUDE.md wins any disagreement.
- **The marts README "Review workflow with Claude" section: deleted.** Three
  sentences of generic advice, nothing in it specific to this repo, no finding
  or measurement or tradeoff.
- **ADR-2 and ADR-3 in the read-first order: split apart.** They had been
  listed together with ADR-4 and ADR-7 as "superseded; read them for the
  reasoning", which is four documents' worth of undifferentiated instruction.
  They do not repay equal attention. ADR-4 repays it in full and ADR-9 says so
  in its own first paragraph. ADR-7 repays it because ADR-10 argues against it
  point by point. ADR-2 repays a skim, for option B alone, which is what ADR-5
  cites as the reason cells are not written into the raw zone. **ADR-3 is
  archive**: it scopes four datasets around a city-spending headline that is
  two supersessions gone, and its one live idea, why the budget-to-311
  crosswalk was never built, is in README.md. Recorded in CLAUDE.md as prose
  rather than by touching the ADRs, which are immutable.

### `main` caught up, which flips a standing warning

Every prior session carried a note that `origin/main` was 13 commits behind,
carried `ingestion/datasets.py`, had no spatial step, and therefore that the
daily cron wrote to a runner's disk and evaporated. PR #3 merged this evening
and `origin/main`, `main` and `HEAD` are now the same commit. The cron will now
run the pipeline as `ingest.yml` describes it. It has not fired since the merge,
so the first scheduled run against the bucket is still unobserved, and that is
the version of the warning that survives into the handoff prompt.

### The Done-when list, re-verified rather than trusted

Every box was checked against the repo again rather than taken from the date it
was ticked. Ten of eleven were met. Numbers at close: 7 datasets;
`make ci-build` `PASS=171 ERROR=0` on both passes, 19 models, 148 data tests,
4 hooks; `RESOLUTIONS = (8, 10)` with `make check-derived` reporting the zone
current with the raw zone and the code; `make test-python` 127 passed in 0.12s;
a second `make spatial` on an unchanged zone 0.27 seconds with nothing
rebuilt; one publish 7 objects and 3.0 MB.

**The eleventh box was resolved by judgement rather than met, and this is the
one thing in the close that someone might reasonably want to argue with.**
"`spatial.py` is three files, each under about 350 lines" is now five files,
two of them over 350: `spatial.py` 521, `boundaries.py` 503,
`derived_state.py` 364, `h3_points.py` 235, `population.py` 88. Step 6 left
the box open and named step 13 as the place to settle it, so settling it is in
scope; closing the plan around it would not have been.

The decision is not to split further. Splitting the pip-sample oracle out of
`boundaries.py` is what would fix the number, and it is the one split that
should not happen: the oracle exists to measure the error in the membership
computed a few functions above it, and a check in a different file from the
thing it checks stops being read alongside it. The line count was a proxy for
"one subject per file, and a direct test on the risky parts", written when
`spatial.py` was 942 lines and had no direct test anywhere. That condition
holds now. Reaching 350 would buy the number and sell the reason for it. The
box records the argument so the next reader does not re-open it by reflex.

## PLAN-6 step 1. The context pack spec, written before any generator.

`docs/specs/context-pack.md`. No code this session, which was the instruction
and is also PLAN-6's own first constraint: the spec is the thing the generator
gets verified against, so writing them together would mean neither constrained
the other.

### The open question, and why it did not resolve the way it looked like it would

PLAN-6 asked whether the pack describes the DuckDB warehouse, the BigQuery one
or the published Parquet, and called answering it most of the format decision.
It is.

The tempting answer is one pack with a `distributions` block, on the grounds
that ADR-1 makes both engines return the same rows and `parity-check.py` proves
it, so only freshness differs and freshness is small. That reasoning is wrong on
its premise. **The three surfaces do not hold the same models.** The published
export is the six marts in `PUBLISHED_MARTS` and nothing else, so a consumer of
the bucket has no staging models at all: no `stg_spatial__polygon_h3`, so the
three-flag trap does not apply to them; no `int_point_activity`, so the join map
is shorter; and several questions that are answerable in the warehouse are
refusals there for a reason that has nothing to do with the data being missing.

So: **one pack per target**, three self-contained artifacts, one hand-maintained
YAML behind all three. What that buys and costs is in section 2 of the spec. The
costs are worth naming here because they are the part a future session will feel:

- Three artifacts is three chances for the prose to drift. The mitigation is
  structural rather than disciplinary. Every hand-written entry carries
  `applies_to`, and generation renders an entry only where every citation in its
  `evidence` resolves against that target's model set. An entry claiming the
  published target while citing a staging model fails the build rather than
  rendering a refusal about a table that is not there.
- The BigQuery pack needs credentials, so it is hand-generated and it is the one
  that will rot. It gets a staleness guard instead of a pretence.
- `publish/export.py`'s schema hash renders DuckDB type names, so it carries
  across the `duckdb` and `published` packs and not into the `bigquery` one,
  where `VARCHAR` is `STRING`. PLAN-6 says reuse that hash rather than invent a
  second, so the spec reuses it unmodified and scopes its claim: the BigQuery
  pack states the hash is deliberately absent and names `make parity-columns` as
  the guarantee in its place.

### The refusal section, which is most of the document

The instruction was that this is the part most likely to be written as filler.
The thing that keeps it from being filler is not effort, it is sorting. Refusals
here are in three classes that fail differently, plus a fourth thing that is not
a refusal at all:

- `absent`, the data is not here. Cheap, and enumerated anyway because it is the
  commonest question shape a city warehouse gets.
- `mismeasured`, a query returns a number and the number does not mean what the
  question assumes. The class an LLM answers happily and wrongly.
- `misnormalised`, the measure is right and the arithmetic invites a wrong
  conclusion. Refuses a form of answer rather than a question.
- Mandatory disclosures, which are answerable with a measured error the consumer
  must be told. Filing those under refusals would be crying wolf, and a refusal
  list that cries wolf is discounted whole.

**ADR-10 made one sentence true that was not true before it**, and most of class
2 falls out of it: every dataset here is an administrative record of an
interaction with the city, so this warehouse contains no ground-truth measure of
the underlying state of anything. That could not have been said in a project
carrying `city_budget`, and it generalises past the enumerated cases, which a
list of individual refusals does not. `census_block_groups` is the stated
exception and it is a denominator, never a subject.

**The disclosure worth reading is the one about the two activity marts, because
it is sharper than the brief was.** "Boundary membership has a measured error"
is not quite right for this warehouse: ADR-6 moved refinement to precompute
time, so point-level membership and `mart_activity_by_neighborhood` are exact,
and `assert_point_boundary_is_exact.sql` fails on a single disagreement. The
error is in the other mart. `mart_activity_by_h3` labels each cell with the
neighborhood that owns it, `is_primary`, at r8, where ADR-6 measured cell-based
membership against exact point-in-polygon at 72.6 percent for neighborhoods and
83.6 for supervisor districts. So summing the H3 mart by neighborhood is not the
same query as the neighborhood mart, it disagrees at the edges, and the
disagreement correlates with geography rather than being noise. Naming which
mart and which number is the difference between a warning a consumer can act on
and one they will discount.

Three rules in the spec are the ones that will do the work later:

1. **Refusals are never trimmed to fit the token budget.** The generator drops
   examples, then column descriptions, then profile statistics, in that order,
   and fails rather than emitting a pack with a refusal missing. Without a stated
   order a generator trims whatever is easiest, which is the prose.
2. **Every refusal and disclosure cites something that resolves**, and generation
   fails when a citation names a model, column or measurement the target does not
   have. This is the anti-filler mechanism and it is the one that would have
   caught the `h3_r9` and `street_trees` residue by itself.
3. **Evidence is measured or it says it is not.** `refuse.newest-month-is-partial`
   is the current example of the second kind: this project has never measured the
   arrival lag, so the entry says so rather than carrying a plausible percentage.

### The closed-world rule

A finite refusal list against an infinite space of questions is a blacklist, and
every blacklist is escapable. The spec closes it with one sentence: if answering
needs a column not in the pack, a join not in the join map, or a dataset not in
the identity block, refuse and name what is missing. It is the only part of the
refusal section that stays correct when the warehouse changes and the prose does
not.

### `docs/specs/` is a fourth kind of document

`docs/README.md` documented three and PLAN-6 asked for a folder that is none of
them. Added as a fourth row with the distinction stated: an ADR is immutable
because the record of what we believed is the point, a spec describes something
still being built and is amended when it has to change. The ADR PLAN-6's
Done-when asks for is still owed, and it is the one that records what the format
left out; section 10 of the spec is written to be its raw material.

## PLAN-7 step 1. The manifest reconciliation, and PLAN-7 is closed.

Continues the same day, after PLAN-6 step 1. `ingestion/check_runs.py`,
`make check-runs`, `tests/test_check_runs.py`, and a line in `ci-build` and in
`ci.yml`. Nothing committed or pushed. PLAN-7 is `done`, which leaves PLAN-6 as
the only open plan.

### The open question went against its own precedent, and the reader is why

The step asked whether the reconciliation belongs in `check_derived.py`, which
already asserts one zone is not behind another, and noted that PLAN-5 step 9 had
just answered the same shape of question the other way by putting the code stamp
into that file rather than beside it.

It does not transfer, and the plan had already written down why without
believing it: the code stamp was a third record in the same manifest, in the
same zone, read by the same reader, answering the same question. These manifests
are `ingest.py`'s, they live in the other zone, and the question is whether one
zone agrees with itself rather than whether one zone is behind another.

The second argument is the one that settled it, and it is about when each runs.
`check-derived` is a prerequisite of `make build` because what it catches makes
a build wrong: rows reach staging with null geography and a `not_null` test
fails four models downstream. `check-runs` gates nothing, because a manifest
that misdescribes the zone makes `mart_pipeline_freshness` wrong and every model
correct. One file would have meant one exit code covering both, and the milder
failure would have started wedging builds for a reason that does not warrant it.

**It went into `ingestion/` rather than `scripts/`, which is a departure from
what the step says.** The step was written on 2026-08-04, before
`check_derived.py` grew a third verdict and a sibling module, and every property
that matters here is that file's rather than `parity-check.py`'s: reads zones
and not a warehouse, needs no credentials, imports its siblings directly, runs
inside `make ci-build`. `scripts/` is the credentialed run-by-hand half. Plan
amended rather than quietly ignored.

### Three things the code said that the plan did not

- **The grain is the run id, not the `ingest_date` partition.** The step
  proposes comparing rows claimed per run against rows present per partition.
  `normalize_record` stamps `_ingest_run_id` on every row, so a manifest has a
  direct counterpart in the data and the weaker comparison is unnecessary. It is
  weaker in a way that matters: two runs of one dataset on one day share a
  partition, so one over-claiming by ten and the next under-claiming by ten is
  silent per partition and two defects per run. `test_two_runs_on_one_day_do_not_cancel`
  is that case. The partition is still checked, as a free extra: a run's rows
  have to be under the `ingest_date` its manifest names.
- **The plan's argument for a warning does not apply, so mismatches are
  errors.** The worry was that a run interrupted by a network failure is a
  legitimate state that should not wedge the pipeline. It is legitimate and it
  does not fire the check: `_flush` increments `rows_written` and
  `files_written` as it writes each file, and `_finish` writes the manifest on
  the failure path too, so a run that died mid-fetch claims exactly what it
  durably wrote. What is left once the honest partial run is excluded is a zone
  that has been edited, which ADR-4 says cannot happen.
- **The second open question dissolved rather than being decided.** It asks
  whether to reconcile against `raw_ingest_runs` or read the zone, and gives
  duplicated manifest parsing as the only cost of the zone. There is none:
  `raw_zone.runs_read_sql` is the one reader and `load.py` builds
  `raw_ingest_runs` from that same call, so `check_runs.py` calls it too. That
  leaves only the arguments for the zone, including that the check then runs
  before `make load` rather than after it.

### What is deliberately silent, which is the part worth reviewing

A check that fires on a healthy zone gets switched off, so the two branches that
report nothing are the ones carrying the risk.

- A manifest claiming zero rows with no Parquet behind it. That is "ran, found
  nothing new", it is the commonest manifest in a healthy zone, and it is the
  reason the manifests exist at all: a run that fetches nothing writes no file,
  so the data alone cannot tell it from "ingestion has not run in three days".
  Verified for real rather than in a unit test: a second fixture ingest of
  `311_cases` into the same zone wrote a second manifest, no Parquet, and the
  check stayed green at 8 manifests.
- A `status: failed` manifest whose numbers reconcile. That is a correctly
  recorded incident. Whether the newest run failed is
  `mart_pipeline_freshness`'s question, and answering it here would mean one
  historical failure warning forever.

Watermark disagreement warns in both directions and never fails. A manifest
ahead of the data is a run killed between advancing `watermark_out` and
flushing, and it costs nothing, because `resolve_watermark` resumes from
`raw_zone.read_watermark`, which reads the data and never a manifest.

A dataset directory the registry does not name warns and is reconciled anyway.
That is `raw_city_budget` and `raw_street_trees` on the local zone, the same
ADR-10 residue `make parity-columns` warns about by name, and it is the same
call step 2 made about an extra table on the BigQuery side.

---

# 2026-08-07

## Which warehouse CI checks against, and the measurement that decided it

The plan favoured "check against the fixture warehouse for the parts that do not
depend on the data", and that survived checking. The reasoning it rests on is
narrower than the plan stated it, and worth writing down in the narrow form.

`--check` compares four things: the target name, the `prose_revision`, the
`spec_version`, and the per-model schema hash. **Three of the four are file
reads.** Only the schema hash needs a warehouse at all, and it is over column
names, types and ordinal position, so a seven-row fixture table hashes to what a
360,000-row real one does.

Measured, rather than assumed: **all 19 hashes from `data/ci/sf.duckdb` are
identical to the ones the real warehouse produced.** Then both failure modes
were fired against that fixture warehouse rather than argued.

- A tampered hash on `mart_activity_by_neighborhood`, checked against the
  fixture warehouse: exit 3, naming the model.
- The pack as committed at `b4c9733`, checked after this session amended the
  spec: exit 3, "the contract moved and the pack was not regenerated". That one
  was free, since amending the spec is what bumps `spec_version`.

**One thing the plan had not noticed kills the other option outright.**
Regenerating in CI and failing on `git diff --exit-code` was never available,
because generation is not deterministic against an unchanged warehouse.
`generated_at`, the dbt `invocation_id`, the manifest timestamp and the
clock-derived columns of `mart_pipeline_freshness` all move on a re-run with no
data having moved. The proof was already sitting in the working tree at the
start of the session: `b4c9733` plus a regeneration, 40 lines of diff, zero
schema hashes moved, every line of it a clock. So the fixture-check option is
not the better of two gates, it is the only one of the two that is a gate.

**What it cannot see**, stated plainly because it is the cost: row counts,
profiles, freshness and example results are not compared, so a pack whose
numbers are a month old passes. The alternative fires on every ingest.

## The traps block is in the markdown now, and the spec was amended

The reading PLAN-6 step 2 shipped was defensible: section 9's rendering order
lists nine blocks and traps is not one of them. It is still wrong, for a reason
that is in the spec rather than in taste.

**Section 4.6 defines a trap as a disclosure object without the trigger
condition.** So a trap is an unconditional disclosure, and the pack was
rendering the conditional warnings while withholding the unconditional ones.
That is the wrong way round however the rendering order is read.

The second argument is about what the artifact is for. The JSON is about 9,000
lines and nothing puts it in a prompt, so a block that is JSON-only is a block
the answering model does not see. Three of the four traps prevent a query that
returns a plausible number rather than an error: a `group by category` that
pools three unrelated vocabularies, an H3 cell compared as a hexadecimal string,
and an inner join that discards the null neighborhoods without saying so.

Cost: 585 estimated tokens. The pack is now 25,219 against a budget of 26,000,
and traps are never dropped. They are not in the budget ladder because the
ladder sheds detail from the models block, and 585 tokens of trap is worth more
than the profile statistics stage 3 drops.

Amended in `docs/specs/context-pack.md`: section 9's order and its never-dropped
rule, section 4.6's definition, and section 2's table, which claimed CI
generates two of the three packs. It checks one and generates none. The
frontmatter `date` moved to 2026-08-07, which is what makes every older pack
stale until regenerated.

## What is in CI

One step in the `dbt-duckdb` job, after `dbt build`:

    python tools/context_pack/generate.py --target duckdb --check

It writes nothing. The comment above it is longer than the step and says what
the fix is when it fires, because the wrong fix is available and cheap:
regenerating from fixtures would make it green and would commit an artifact
describing a warehouse nobody reads. The same warning is now in CLAUDE.md.

## PLAN-6 is closed with one target of three, and the other two are PLAN-8

The Done-when list asked for the artifact, the gate and the decision, and all
four boxes are ticked. Carrying the plan open for the `published` pack would
turn it into a plan about targets.

**The published target was not started, and the reason is that it is not the
cheap piece of work the handoff called it.** The audit is on PLAN-8 so the next
session does not repeat it. The connection factory is genuinely small, and the
prose is already published-aware: 19 of 20 refusals, 5 of 6 disclosures and 3 of
4 traps carry `published` in `applies_to`, validated when the duckdb pack was
built. What is not small is step 4 of that plan. **Not one of the six examples
applies to published**, and an example is verified against the target whose pack
it appears in and nowhere else, so the duckdb SQL cannot be inherited. Four
class 3 refusals apply to published and each needs its own example or generation
fails, which is the rule working. Each one means writing the SQL over the
Parquet views, executing it, reading the result and only then stamping the
hash. Half-doing that would put an unverified example in the one hand-maintained
file, which PLAN-6 says is worse than no example.

Also on PLAN-8: the published-only refusals the spec commissions and that do not
exist yet, the "this is in the warehouse and not in this export" class, which
are what make the published pack a different document rather than a shorter one.

# 2026-08-07, second session

## What the bucket actually held, and one number in the plan was wrong

`make build` first, per the last session's handoff, so `dbt/target/manifest.json`
is a real build and not the fixture one.

The plan's bytes were exact and its object count was not. 511,937,211 bytes is
right to the byte. The count is **236**, not 329, by
`gcloud storage ls --recursive gs://$GCS_BUCKET/raw/**`. Corrected in the plan in
place. The bytes are what the argument rests on, so nothing downstream moved,
but it is the number a reader checks first.

Per dataset, and this is what step 1 was for:

| dataset | objects | MB | partitions |
|---|---|---|---|
| raw_business_locations | 78 | 397.1 | 7, one per day |
| raw_311_cases | 29 | 49.9 | 7 |
| raw_city_budget | 27 | 30.2 | 2 |
| raw_street_trees | 15 | 25.3 | 1 |
| raw_building_permits | 24 | 7.8 | 7 |
| the four reference sets | 62 | 1.7 | 1 each |

**The snapshot-versus-delta split was confirmed against the data rather than
against the document, which is what step 1 asked for.** Every one of the six days
after the backfill wrote a fresh full copy of `business_locations`: eight files
and about 49.6 MB each, with the distinct `grain_key` count moving 364,731 to
365,006 across the week. That is not a backfill and not an increment; it is the
whole dataset, daily. The delta datasets look nothing like it: 311 adds about
4.7 MB a day and permits about 0.13.

Two `.DS_Store` objects are in the raw prefix, from the hand sync of 2026-08-01.
Harmless, left alone, noted because they are in the object count.

## The prune, and the two things it will not do

`ingestion/prune_raw.py`, `make prune-raw` and `make prune-raw-apply`. The
registry gained `refresh: snapshot | delta`, required rather than defaulted, and
`dataset_registry.snapshot_datasets()` is the filter everything else runs behind.

**The proof is two counts and both must be zero**, checked before anything is
deleted, against a partition that will survive the prune:

- `unreachable`, grain keys in the candidate that the survivor does not have;
- `regressed`, keys the survivor has at an older `_socrata_updated_at`.

The second is not in PLAN-9 and was added on the way, for a reason that is worth
keeping: staging picks the newest row per key, so a survivor that is behind on
one key changes what a model returns while leaving every row count identical.
The plan's acceptance test compares row counts, so it structurally cannot see
that, and the check is the only thing that can. It costs nothing; it is two more
columns in the same query.

**A `refresh: snapshot` dataset is still not a prunable partition**, and
conflating the two is the mistake this design is shaped around. `snapshot` says
a partition of this dataset *can* be complete. A run that fetched 200 changed
rows writes one that looks identical from outside. Four of the 14 tests in
`tests/test_prune_raw.py` are refusals for that reason: a key the survivor
lacks, a key it has at an older value, a null grain key, and a delta dataset
named on the command line.

## Both open questions, answered in ADR-14

**Where it runs: by hand.** Same as `make publish`, and for a stronger reason. A
cron that deletes data is a different risk appetite from a cron that writes
some, and this tool's whole design is that a human reads a refusal. The cost is
named rather than argued away: the zone is now bounded by someone remembering.

**Does `check_runs.py` get a third state: no.** The prune deletes the manifests
with the partitions and `check_runs.py` is unchanged. Re-reading PLAN-7 step 1
first, as the plan asked, is what settled it, and it settled it against the
cheap answer's favour rather than for it: that call kept `check_runs.py` out of
`check_derived.py` because the reader and the moment were different, and here
they are the same file and the same run. What decided it is where a third
state's marker would have to live. Inside the manifest means editing a file in
the zone, which is a *larger* break of ADR-4 than deleting a superseded
snapshot; beside it means a second record type to keep in step with the first.

What is genuinely lost is in ADR-14's Costs: `mart_pipeline_freshness` now sees
8 runs of `business_locations` where 14 happened. **Manifests of runs that wrote
no rows are never touched**, and that is the half that matters, because a run
that found nothing new writes no Parquet and its manifest is the only record it
ran at all.

## The acceptance test, and the baseline the plan named could not be used

PLAN-9 step 5 says compare against the committed context pack. That could not be
done as written, and finding out why took one query rather than a debate.

**The committed pack describes a build from the LOCAL zone.** `data/raw` holds
one partition per dataset, the 2026-07-31 backfill, 162 MB. The bucket holds
seven days. A build from each is a different warehouse: 311 is 103,457 rows
locally against 118,357 from the bucket. The pack's 19 row counts match the local
build exactly, which is how this was confirmed rather than assumed. So the
committed pack is the right baseline for a local zone and the wrong one for a
prune that happens on the bucket.

What was done instead is the same test with a baseline that describes the thing
being changed: `make rebuild` against the bucket **before** the prune, row counts
recorded, prune, `make rebuild` again, compare.

**0 of 19 model row counts moved**, after deleting 54 objects, 297.8 MB and
2,188,619 raw rows of `raw_business_locations`. `dbt build` `PASS=172 ERROR=0`
both times. That is the whole safety argument and it held: staging was
deduplicating exactly what the argument assumed it was.

`make check-runs` against the bucket was clean before the prune (128 manifests,
0 failing) and clean after (122, 0 failing), which is step 4 demonstrated on the
real zone rather than only in the tests.

`make context-pack-check` still agrees with the live target. It compares schema
hashes and not row counts (ADR-13), so it passes against either warehouse, and
saying that plainly is more useful than reporting it as though the prune had
been what it survived.

## The published prefix, and a mart nobody had noticed was still being served

Step 7's end state, by step 6's mechanism. `--prune` was run rather than
`gcloud storage rm --recursive`, because rm-then-upload leaves a window with no
export in the bucket at all, which is the state ADR-8's manifest ordering exists
to prevent, and because it exercises the new flag against the exact condition it
was written for.

2,885 objects and 18.6 MB before; 7 objects and 3.2 MB after, `manifest_version`
2. The 2,880 removed were 2,879 objects of the pre-ADR-12 month-partitioned
layout and **`mart_budget_by_department_year`**, a mart from before ADR-10 cut
`city_budget`. Nothing in the repo produces it and the bucket had been serving
it since 2026-08-01. That is the orphan class the flag exists for and it was not
the one anyone was looking for.

`--prune` refuses a destination with no prefix. Under a bare `gs://bucket`,
"everything this export did not write" is the raw and derived zones.

# 2026-08-07, third session

## What the warehouse was pointed at first, and why it was rebuilt

`data/sf.duckdb` was the bucket build the last session left, and the committed
duckdb pack describes a build from the local zone. **`make rebuild` with no
`.env` sourced put it back**, `PASS=172 ERROR=0`, and `make context-pack-check`
then agreed with the live target, which is what confirmed the pack's numbers are
the local zone's rather than assumed it.

`make publish` was then re-run so the export describes that same build. It had
been written from the bucket build at 17:54 and its numbers were the bucket's:
142,740 rows in `mart_activity_by_h3` against 140,163 from the local zone. Two
committed packs describing two different zones would have been a difference a
reader could only explain by knowing this paragraph. The cost is named: the
local `published/` is no longer a copy of what is in the bucket, and the pack's
self-refusal is what covers that, exactly as spec section 8 says it does.

## The audit in the plan was right about the numbers and wrong about the claim

PLAN-8 recorded that 19 of 20 refusals, 5 of 6 disclosures and 3 of 4 traps
carry `published` in `applies_to`, and called those claims rather than guesses
because "an entry claiming a target it cannot resolve against already fails
generation". **They were guesses.** An entry is only resolved against the target
being generated, so a `published` claim was unchecked until a published pack was
built. Generation failed on the first attempt with **12 entries** that could not
resolve: six refusals, three disclosures and three traps.

That is not a defect in the rule; it is the rule doing the only thing it can do.
It is worth writing down because it is the same shape as the two-copies failure
this repo has paid for twice: a field that looks checked and is checked only
where someone happened to look.

## Two ways to fix an entry, and the choice between them is the finding

Every one of the 12 fails because a citation names a staging model, the
intermediate spine or the H3 bridge. There are two honest repairs and they cost
different things.

**Nine were repaired in place**, three refusals, three disclosures and three
traps, by dropping a citation that was not load bearing and rewording the
sentence that named it. `disclose.coordinate-drop-rates`
cited `stg_datasf__business_locations.coordinate_status` to support a statement
about drop rates that `mart_pipeline_freshness` already carries; the citation
went and the disclosure now holds in both packs. The price is paid by the
warehouse pack: `refuse.no-city-spending-data` used to offer "which agency 311
records as responsible" as its substitute and now offers a count by category,
because there is no `agency` column in the export to offer.

**Three were split**, because the substitute is not the same answer in the two
places. `refuse.permits-are-filings-not-construction` sends a warehouse reader to
`stg_datasf__building_permits` to count distinct `permit_number`, and there is
nothing in the export to send anyone to, so the export gets its own refusal
saying the deduplicating column is upstream of it.

**Five published-only refusals now exist and they are what step 3 was for**:
`refuse.export-has-no-row-level-records`,
`refuse.export-has-no-staging-or-intermediate-models`,
`refuse.export-has-no-h3-bridge`,
`refuse.export-counts-permit-records-at-filing` and
`refuse.export-counts-registrations-not-businesses`. Plus one disclosure,
`disclose.export-cell-population-is-interpolated`, which exists because the
interpolation warning the warehouse pack carries is about choosing the right
flag on a bridge the export does not have, while `cell_population` and every
rate built on it are in these files.

## Step 4, four examples, none inherited

An example is verified against the target whose pack it appears in and nowhere
else, so the six duckdb examples could not be carried over. Four class 3
refusals apply to published and each one has an example written over the
export's Parquet, executed against it, and its result read before the hash was
stamped.

They are not the duckdb queries with the table names changed. The marts carry
their own denominators, so **the joins are gone**: the per-capita example reads
`population` and `business_count` off `mart_activity_by_neighborhood` and shows
the two denominators disagreeing in one result set, Golden Gate Park at 307.9
reports per 1000 residents and 1093.0 per 1000 businesses.

**One of the four proves its refusal instead of asserting it.**
`ex.export-h3-cells-ranked-by-rate` ranks the same cells three ways in one
query, and `rank_by_events_per_sq_km` equals `rank_by_count` on every row it
returns while the per-resident rank is unrelated. That is
`refuse.events-per-sq-km-on-the-h3-mart` made checkable by the reader rather
than believed.

**One was rewritten because the data would not support it.** The vintage example
was first written as a 2024-against-2025 comparison with one denominator, which
is the sharpest form of "no change in this rate is a population change". The
local zone holds 65 events in 2024, 289 in 2025 and 101,811 in 2026, so the
query answered the question with numbers that invite a wrong conclusion about
311 volume. It now states the vintage and the window it divides in the same row:
April 2020 residents under events running 2024 to 2026. Executing an example and
reading the result is what caught that, which is the whole argument for the
attestation.

## Two findings from the machinery, both worth keeping

**HUGEINT has no Parquet type.** Three of the six marts hash differently in the
export than in the warehouse, because `publish/export.py` writes
`active_business_count`, `tests_passed` and their kind as DOUBLE. Neither hash is
wrong and the difference is a property of the format, so generation does not
fail on it: the pack carries both, says which is which, and says that a count
read from the export is a float. Spec section 8 calls
`published/manifest.json` the authority on the export, and it is the authority
on what was exported rather than on what the file holds.

**A refusal can point at an example that is in another pack.**
`instead.example` is written once in prose and read by every target, so the
published pack would have said "see example ex.reports-per-capita-by-neighborhood"
with no such example in it, which is the closed-world rule broken by the pack
itself. The pointer is now rewritten at selection time to an example this pack
carries that demonstrates this refusal, and dropped when there is none. The
first version of that rule also overwrote pointers that were fine, and cost
`refuse.311-is-not-a-safety-measure` a cross-pointer its author meant; a named
example that is present is now left alone.

## The Makefile question, answered with the second pack in hand

**One target and a `TARGET=` variable**, defaulting to duckdb. The code path was
never the argument: it is one line either way. What decided it is that the
difference between the two commands is which artifact has to exist first, and
`generate.py` refuses each with the sentence that names the missing one, so the
Makefile does not have to. A target per pack would have duplicated the comment
above them, and that comment is the one place saying why generating after
`make check` produces a pack whose row counts are real and whose invocation id
is a fixture run's. The cost is that a variable does not show up in `make help`,
so both help lines name it.

## Did the three-pack argument survive a second pack

**The three-pack argument survived a second pack.** It survived in the form the
spec makes it, that one prose file with `applies_to` beats three hand-kept
documents, and it survived a real test rather than a formality: 12 entries
claiming the second target could not resolve against it, every one of them was
found by the generator rather than by a reader, and every one named the exact
citation that failed.

What it survived on is `model_set` and the resolution rule. The published pack
is 21 refusals against the warehouse pack's 20 and shares 16 of them; with the
disclosures and traps that is 24 entries in both packs. A duplicate-document
arrangement would have had those 24 in two files with nothing checking they
agree, and this session would have been the one that let them drift, because
nine of them were reworded here.

The cost the argument did not have before is now visible and should be stated
rather than argued away. **Five refusals and one disclosure exist only to say
what the other pack's substitute cannot say**, and two of them are the same
mismeasurements as entries above them with a different `instead`, one of those
two folding a pair of warehouse entries into one. That is not duplication of a
fact, it is duplication of a subject, and the day there are eight of those is
the day this is worth re-reading. The second cost is that the
shared entries are now written to a lower common denominator: two lost a
warehouse-specific substitute so that one sentence could be true in both packs.

# 2026-08-07, fourth session

## The state check the last session asked for, and it came out the other way

The previous note said `dbt/target/manifest.json` was the fixture build's again
and to run `make build` before anything. It was not: its `invocation_id` is
`c0e3245a`, which is the one the committed duckdb pack carries, so the last thing
to write it was the real build and not `make check`. No rebuild was needed and
none was run. Comparing the manifest's invocation id against the committed pack's
is the cheap way to tell which build wrote it, and is worth doing rather than
believing the previous note.

Nothing needed the warehouse anyway: the tests build their own export, and the
only reason to touch the real one would have been regenerating a pack, which
nothing this session changed the content of.

## Step 5, and what an in-memory export has to contain to be worth testing

Eleven new tests, plus six existing ones parameterised over both targets rather
than duplicated. `make test-python` is 210 against 194.

The export the fixtures write is six Parquet files of one row each, one per
`PUBLISHED_MARTS` entry, with the manifest written last the way `make publish`
writes it. The first test is the one PLAN-8 names, and it is written as two
assertions because there are two routes into the published pack for a refusal
citing a staging model and both have to be closed: an entry claiming `published`
fails generation naming the citation, and an entry that does not claim it is
silently not rendered.

**The fixture's `all_models` has to hold models the export does not.** Three
staging and intermediate names sit in it for that reason. A fixture built from
`PUBLISHED_MARTS` alone would pass just as well against a `model_set` that had
stopped filtering, which is the function the whole three-pack argument rests on,
so the test would have been decorative. That is the same shape as the thing that
made the last session interesting: a check that only looks where the answer is
already known.

Four more beside it, all of them refusals rather than behaviours: an export with
no `manifest.json` is not an export, an export missing a mart is refused rather
than described, a published pack with no publish time fails rather than quietly
reporting the build time, and the freshness block carries the publish time as its
headline with the build time beside it. Three cover the `instead.example`
rewrite from the last session, including the regression that one caused: a
pointer this pack already carries is left alone, because an author may point a
refusal at a neighbouring refusal's example on purpose.

Parameterised rather than duplicated: the four drift-check tests and the two
citation-resolution ones now run for `duckdb` and `published` both, and the
cross-target rejection runs in both directions. CI checks two packs, so a test
suite that checked one target was describing a gate that no longer exists.

## The bigquery question, answered no, and the second pack is what answered it

ADR-15. **The bigquery pack is declared and not generated**, and step 6 is struck
rather than deferred so no plan carries it as work someone might pick up.

The evidence decided it against the plan's own expectation. The published pack
earned its cost because its model set is six marts against nineteen models:
twelve entries could not resolve, five refusals and one disclosure exist only
because the substitute differs there, four examples could not be inherited.
**None of that transfers, because `bigquery` declares `models: "all"`, which is
duckdb's model set.** Every entry that resolves against one resolves against the
other, so the two packs would carry the same 20 refusals, 6 disclosures, 4 traps
and 13 joins word for word. What would differ is type names, row counts,
freshness and six examples re-executed with credentials, and those are exactly
the parts that ADR-13's missing schema hash means nothing can gate. The third
artifact would have been the first committed thing here that nothing could prove
current, regenerated only by someone with credentials who remembered.

What stands in its place is not nothing: `make parity-columns` and
`scripts/parity-check.py` answer the cross-engine question with the credentials
the pack would have needed, and the column drift of 2026-08-05 was found by the
first of those and could not have been found by a pack. The cost is stated in the
ADR and accepted: a consumer querying BigQuery has no pack, and the nearest one
renders `VARCHAR` for a `STRING` column. The day such a consumer appears this
decision is wrong, which is ADR-13's first revisit trigger and is now ADR-15's.

The target stays declared in `pack_target.py`. Deleting it would make
`applies_to: [bigquery]` meaningless and would turn `open_target`'s paragraph
into `Unknown target`, and spec section 2's three-target commitment is not
reopened here.

## The spec amendment that did not invalidate anything, and why that is luck

Section 2's table row and the bullet under it now say the pack is not generated.
The spec's `date` is what a pack records as `spec_version`, and this amendment
lands on the same date as this morning's, so `spec_version` does not move and
both committed packs stay current. That is the right outcome here, because
nothing in the amendment changes what either generated pack contains. It is
still worth writing down that a date-granular version cannot tell that case from
an amendment that should have invalidated every pack, so two amendments in one
day is a thing to notice rather than a thing the mechanism handles.
