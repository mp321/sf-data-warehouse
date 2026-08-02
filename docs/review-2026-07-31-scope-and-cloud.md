# Review, 2026-07-31: scope, cloud posture, and what to cut

Not one of the three canonical document kinds. This is an outside read of the
repo as it stands, meant to be harvested into `PLAN-4` and `ADR-9` and then
deleted. Nothing here is a decision until it is written as one.

## Headline

This is not a vibe-coded project. The reasoning quality in `docs/decisions/`
and in `docs/dev-notes/2026-07-31.md` is better than most production repos.

The defect is different, and it is worth naming precisely: the project is
**over-decided relative to what it has executed**. Eight ADRs, three plans,
22 models, two engines, three data zones and about 3,300 lines of Python, and
the single most load-bearing claim in the whole design, that the same SQL
produces the same rows on DuckDB and BigQuery, has never once been run.

For a portfolio artifact that is the thing that costs you. A reader gets to
ADR-1's cross-engine guarantee, asks "so what did the row-for-row diff show",
and the answer is that PLAN-1 step 4 is still open. Everything else in this
review is downstream of closing that gap and then cutting whatever is not
needed to hold it up.

## What genuinely works, and should not be touched

1. **The raw zone design (ADR-4).** Hive partitions by ingest date, run
   manifests, replace-on-load. Idempotent with no bookkeeping. The
   drop-the-warehouse-and-rebuild step in CI is the strongest single thing in
   the repo, because it converts "Parquet is the source of truth" from a claim
   into a test.
2. **The paging tie bug and its fix.** Ordering on `:updated_at` alone with
   `$offset` paging was losing rows silently: 36,112 fetched, 35,918 distinct
   `record_id`. Found by measurement, fixed with a total order, verified by
   re-ingest. This is the best interview story in the project and it is
   currently buried in a dev note.
3. **H3 as precomputed BIGINTs in a derived zone (ADR-5, ADR-6), with an
   exact point-in-polygon oracle test.** Correct call, and the reasoning for
   why the DuckDB extension was rejected is airtight.
4. **Credential-free end-to-end CI on fixtures that break on purpose.** Fork
   PRs run the whole pipeline. Rare, and worth more than it currently gets
   credit for.
5. **The doc conventions themselves.** Plan versus decision versus incident,
   immutable ADRs, supersession rules. Keep.

## What does not work today

| Problem | Severity | Evidence |
|---|---|---|
| Nothing is committed | Critical | 50+ untracked files on `branch1`: all of ADR-5 to ADR-8, `spatial.py`, `census.py`, `geometry.py`, `publish/`, every mart, every spatial staging model. One `git clean -fd` from gone. |
| `docs/plans` is gitignored | High | `.gitignore` line 10, inside the secrets block. `plan-3` is invisible to git. PLAN-1 and PLAN-2 survive only because they were tracked before that line was added. |
| BigQuery target never executed | High | PLAN-1 step 4 open since day one. CI compiles against BigQuery, which proves dialect validity, not row equality. |
| Remote `make publish` never executed | Medium | ADR-8 says so plainly. R2 and GCS upload paths are code that has never run. |
| No Python tests | Medium | `tests/` holds fixtures only. `geometry.py` is 280 lines of hand-rolled point-in-polygon and area, and is the highest-risk code in the repo. It is covered only indirectly, by assertions inside `spatial.py`. |
| Dual dataset registry | Medium | `ingestion/datasets.py` and `vars.pipeline_sources` in `dbt_project.yml` list the same nine datasets. The drift is documented, not prevented. |
| BigQuery holds orphaned tables | Medium | 4 `raw_` tables, 6.44 GB, written by the pre-ADR-4 code path. Not reproducible from the current raw zone. They are not the pipeline's output any more. |
| `meta_dbt_run_results` never pruned | Low | One row per node per run, forever. |
| Macro count at 8 of ADR-1's own ~10 threshold | Low | The tripwire the ADR set for itself is nearly triggered. Honor it or move it deliberately. |

## What will not work, and needs a pivot now

### 1. Mirroring the raw zone into BigQuery does not fit in the free tier

BigQuery's free tier is 10 GiB of storage and 1 TiB scanned per month, and the
sandbox carries the same limits plus a 60-day expiry on every table, view and
partition. Your `raw_datasf` already holds 6.44 GB from 311 alone, because the
raw contract stores every value as a STRING.

Add the other eight datasets, plus `derived_spatial` (`derived_point_h3` is one
row per point-bearing row, at three resolutions, across roughly 9.5 million
points once the registries are in), and you are over the ceiling. Storage is
the binding constraint here, not query volume: load jobs are unbilled and
1 TiB of scan is far more than these models will ever use.

**Pivot: stop loading raw into BigQuery. Point BigQuery at Parquet in GCS
through external tables, and materialize only marts.**

That one change takes BigQuery storage from 6.44 GB and rising to well under
a gigabyte, removes the replace-on-load rewrite entirely, and is a better
architecture to explain than "I copy Parquet into a warehouse". It is the
standard lakehouse shape. `load.py` stops moving bytes and becomes a thin
per-engine DDL step, with the same interface and the same schema names, so no
model changes.

The catch worth writing into the ADR: PLAN-1 step 3 rejected dbt-duckdb's
`external_location` precisely because it made the two engines' source
definitions structurally different. External tables on both engines restores
the symmetry at the conceptual level while accepting asymmetric DDL inside
`load.py`. That is a real change to ADR-1's reasoning and deserves its own
decision record rather than being slipped in.

### 2. The GitHub Actions cache as raw zone durability is a live landmine

`ingest.yml` restores `data/raw` from an Actions cache with a 7-day eviction
and a 10 GB cap. A cache miss means `read_watermark` returns nothing and the
run backfills from `start_date`. For 311 that is 8.8 million rows, on a shared
runner, unattended. The workflow comment calls this a mitigation rather than
an answer, which is correct, and it has been the answer for the whole life of
the project.

**Pivot: GCS becomes the raw zone.** Always-free is 5 GB-month of Standard
storage in `us-west1`, `us-central1` or `us-east1`, with 5,000 Class A and
50,000 Class B operations per month. Your current raw zone is 110 MB, so this
fits with room to spare, but only if the scope narrowing below happens first.
A full 311 backfill does not fit, and neither does the current 48 MB of
`business_locations` growing unbounded plus a per-run file count that eats
Class A operations.

This closes PLAN-1 step 5, which is the single item blocking PLAN-1 from
closing and has been open since the plan was written.

### 3. Three H3 resolutions on every point row is a storage multiplier

ADR-5 keeps r9 explicitly so that ADR-2's original guess "stays checkable".
That is a good instinct for a dev note and an expensive one for a schema: it
is 33 percent of the largest derived table, forever, to preserve a historical
footnote. Drop to r8 and r10, record why in the ADR that supersedes it.

## The local versus cloud question, answered

Local is not the problem. **Two of everything is the problem.**

Local DuckDB buys something concrete and rare: CI that runs the entire
pipeline, including fork pull requests, with no secrets. Drop it and CI needs
credentials, fork PRs stop working, and iteration goes from milliseconds to
network round trips. That property is a portfolio differentiator and giving it
up would be a downgrade.

So the recommended posture is not "cloud instead of local". It is:

> **GCS is the system of record. BigQuery is the cloud warehouse, reading that
> record through external tables. DuckDB is the development and CI mirror,
> reading the same Parquet locally. `data/` becomes a cache, not a zone.**

Concretely:

- `data/raw` stops being described as the durable zone. It is a local cache of
  `gs://<bucket>/raw/`, and losing the laptop costs nothing.
- `ingest.py` writes to GCS (or writes locally then syncs; either is fine, the
  first is simpler to reason about).
- `load.py --target bigquery` creates external tables over the GCS prefix.
- `load.py --target duckdb` keeps doing what it does, or switches to
  `read_parquet` over the same prefix.
- `make publish` finally has a bucket to publish to, so ADR-8's remote path
  stops being code that has never run.

This is cloud-first in the way that matters, and it does not throw away the
credential-free build.

**Before any of it: confirm whether billing is attached to the project.** If it
is not, everything in `raw_datasf` expires 60 days after creation and the
6.44 GB you are looking at may already be on a countdown. Attaching billing
removes the expiry and leaves the free tier intact.

## Ingestion: covering the difference without overworking

The current design is mostly right. The watermark comes from the zone and only
the zone, ordering is a total order, appends are never rewritten, and
full-refresh swaps atomically. Four things to fix:

1. **Switch from offset paging to keyset paging.** The order is already total,
   so `$offset` is no longer needed and only costs you: Socrata degrades
   badly at deep offsets. Page with
   `:updated_at > 'W' OR (:updated_at = 'W' AND :id > 'I')` and keep
   `$offset` at zero. Strictly better, and small.
2. **Make `spatial.py` incremental.** It recomputes the entire derived zone on
   every run, roughly 40 seconds per 700,000 points and growing linearly. At
   9.5 million points that is minutes on every scheduled run. Keying it on
   unprocessed `ingest_date` partitions is a contained change, and the zone
   stays a pure function of raw plus code as long as a code change forces a
   full recompute.
3. **Narrow the backfill windows before the first cloud run**, not after.
   311 from 2024 is roughly 1.5 to 2 million rows against 8.8 million for full
   history. The 2024 window answers every question the marts pose.
4. **Watch the file count, not just the bytes.** GCS always-free allows 5,000
   Class A operations per month. One write per 50,000 buffered rows across
   nine datasets daily is comfortably inside that, but a change to
   `ROWS_PER_FILE` or a full-refresh loop is not.

## Narrowing scope: what to cut

Nine datasets, 22 models, seven marts. For an artifact meant to be understood
in twenty minutes, that is roughly double.

**Cut two datasets outright:**

- `city_budget`, 15 MB. Non-spatial, joins to nothing, and ADR-7 already
  concedes it exists mostly to have a non-spatial mart. Removing it removes a
  staging model, a mart, and a section of `_datasf__models.yml`.
- `street_trees`, 25 MB. ADR-7 justifies it as the H3 stress test.
  `business_locations` at 365k rows is a better stress test and is already
  load-bearing as a denominator.

That is 40 MB of 110 MB, two staging models, one mart, and no loss to the
thesis. Keep `film_locations` despite being decorative: it is 204 KB and it is
the pipeline canary.

**Keep as core:** `311_cases` (the event log), `analysis_neighborhoods` and
`census_block_groups` (the geography and its denominator),
`business_locations` (second denominator, second point dataset),
`building_permits` (the second event log, and the dataset the tie bug was
found in, which is worth keeping for the story), `supervisor_districts` and
`film_locations`.

## Do not start over

The reasoning artifacts are the asset. A rewrite discards precisely the thing
that makes this a portfolio piece rather than another dbt tutorial repo. The
refactor list is short and contained:

1. `load.py` becomes external-table DDL for BigQuery. Contained, high payoff.
2. `spatial.py`, 883 lines, splits into `h3_points.py`, `boundaries.py` and
   `population.py`. It is the largest file and the least directly tested.
3. `pytest` around `geometry.py`. Pure functions, trivially testable, and
   currently the highest-risk untested code in the project. This is also the
   cheapest credibility win available.
4. One registry, not two. Either generate the dbt vars from `datasets.py` at
   build time, or move the registry to YAML that both languages read.
5. Prune `meta_dbt_run_results` to a rolling window.

## The documents themselves

- **ADRs are strong.** The weakness is ratio: eight accepted decisions against
  zero executed cloud builds reads as over-documentation to anyone checking.
  Closing PLAN-1 step 4 fixes the ratio without writing a word.
- **PLAN-1 is a zombie.** Status `active`, five of eight steps done, blocked
  entirely on step 5. Doing step 5 and step 4 closes it.
- **PLAN-2 is in the worst possible state:** status `draft`, partly completed
  as a side effect of other work. Either finish it or close it as superseded.
- **PLAN-3 is done and gitignored.** Fix the ignore rule.
- **`docs/dev-notes/2026-07-31.md` is the most valuable file in the repo.**
  The findings section, particularly the tie bug and the seven-hour timezone
  error, is the actual evidence of engineering judgment. Consider promoting a
  condensed version of it into the README, because nobody reads dev notes.
- **`PLAN.md` at the root duplicates the plans folder.** Goal1 is done and
  Goal2 is the unstarted context pack. Move Goal2 to `docs/plans/plan-4-...`
  and delete the root file.

## Suggested sequence

**Phase 0, today, under an hour.** Commit the working tree. Fix `.gitignore`
line 10. Everything else is worthless if the tree is lost.

**Phase 1, cloud proof, half a day.** Confirm billing. Narrow 311 to 2024. Run
`make load-bigquery` and `make build-bigquery` for real. Diff
`stg_datasf__311_cases` row for row against DuckDB. Write the result as a dev
note whether it passes or fails. This closes PLAN-1 step 4 and is the highest
credibility item available.

**Phase 2, durability, half a day.** GCS bucket in `us-central1`. Raw zone
lands there. Delete the Actions cache step. Write ADR-9.

**Phase 3, external tables.** `load.py --target bigquery` creates external
tables over GCS. BigQuery storage drops to marts only. `make publish` gets a
real destination, which retires ADR-8's untested-remote caveat.

**Phase 4, narrow and polish.** Drop the two datasets and r9. Add pytest on
`geometry.py`. Collapse the dual registry. Prune the run-results table.

**Phase 5, the context pack.** `PLAN.md` Goal2 is the most distinctive idea in
this project and it is unstarted. A published warehouse that ships an explicit
statement of what it cannot answer is a far more memorable artifact than an
eighth mart. Do it after the foundation is proven, not before.

## Sources

- [BigQuery pricing](https://cloud.google.com/bigquery/pricing)
- [BigQuery sandbox and the 60-day expiry](https://docs.cloud.google.com/bigquery/docs/sandbox)
- [Cloud Storage pricing and always-free limits](https://cloud.google.com/storage/pricing)
