---
status: done
date: 2026-08-04
related: [plan-1-duckdb-parquet, plan-4-cloud-first-storage, plan-5-narrow-and-polish, adr-1-warehouse-targets, adr-4-raw-zone-layout, adr-9-cloud-raw-zone, adr-11-derived-zone-code-stamp]
---

# PLAN-7. Check the two claims nothing currently checks

## Goal

Two assertions that run on demand and fail loudly: the run manifests agree
with the data they claim to describe, and the BigQuery external tables have
the same column sets as the DuckDB tables built from the same Parquet.

**Status: closed 2026-08-05. Both steps are done.** The plan went from `draft`
to `active` by being overtaken: the column-set risk stopped being hypothetical
and turned `make build-bigquery` red, so step 2 was implemented against a live
defect rather than a synthetic one. Step 1 followed later the same day as
`ingestion/check_runs.py` and `make check-runs`, and it runs in CI on the
fixture zone, which was the constraint separating the two halves.

## Why this plan exists at all

Both items below are PLAN-4 residue, and the manifest one has now been carried
forward three times: PLAN-1 appended it to PLAN-4, PLAN-4 closed without it,
and 2026-08-04's dev note said in as many words that appending it to PLAN-6
would be the fourth. The note offered two honest endings, "give it one or write
it off as a known gap", and this plan is the first.

They are together because they are the same kind of problem and neither is the
kind of problem PLAN-5 is solving. **PLAN-5 narrows; this verifies.** Folding
verification work into a plan whose thesis is "make it smaller" would have made
both plans harder to read, which is the exact failure PLAN-5 exists to correct.

They are small. This is a two-session plan, not a project.

## Constraints

- Neither check may need credentials to be useful. The manifest one must run
  in CI on the fixture zone; the BigQuery one cannot, and belongs beside
  `scripts/parity-check.py`, which is already run by hand for that reason.
- No new dependency. `duckdb` and `google-cloud-bigquery` are already here.
- Whatever these check, they check against the zone, not against a copy of it.
  There is one zone at a time (CLAUDE.md, ADR-9).
- No commits or pushes by an agent.

## Steps

1. ~~**Reconcile the run manifests against the data.**~~ **Done 2026-08-05**,
   as `ingestion/check_runs.py` and `make check-runs`, and it runs in CI on the
   fixture zone. `ingest.py` writes a run
   manifest per dataset under `<table>/_runs/`, and `load.py` materialises
   them as `raw_ingest_runs`, which `mart_pipeline_freshness` reads. Nothing
   asserts that what a run says it wrote is what the zone holds. Write the
   check as a script under `scripts/`, following the pattern the others set:
   the script is the implementation and the Makefile and CI call it rather
   than restating it.

   What to compare, in rough order of value: rows claimed per run against rows
   present per `ingest_date` partition; the newest watermark in the manifests
   against the newest `:updated_at` in the data; a manifest with no
   corresponding Parquet, and Parquet with no corresponding manifest. The last
   pair is the one most likely to fire, because a failed run can leave either.

   Decide explicitly whether a mismatch is an error or a warning, and write
   the reasoning into the script header. The append-only rule (CLAUDE.md)
   means a mismatch is a real defect rather than drift, which argues for
   error, but a partial run interrupted by a network failure is a legitimate
   state that should not wedge the pipeline.

   **What was built, and the three places it departs from the above.**
   `ingestion/check_runs.py`, `make check-runs`, and a line in `ci-build` and
   in `ci.yml` immediately after the fixture ingest. Two verdicts with distinct
   exit codes, matching `check_derived.py`'s arrangement: MISCOUNTED (3) when a
   manifest and the rows carrying its run id disagree, UNRECORDED (4) when rows
   carry a run id no manifest describes. Every line names the table, the run
   and both numbers. `tests/test_check_runs.py` is 15 tests over the two pure
   functions.

   - **It is in `ingestion/`, not `scripts/`.** Every property that matters
     here is `check_derived.py`'s rather than `parity-check.py`'s: it reads
     zones and not a warehouse, it needs no credentials, it imports its
     siblings directly, and it runs inside `make ci-build`. `scripts/` is the
     credentialed run-by-hand half. The step said `scripts/` because it was
     written before `check_derived.py` grew a third verdict and a sibling
     module.
   - **The grain is the run id, not the `ingest_date` partition.** `ingest.py`
     stamps `_ingest_run_id` on every row, so a manifest has a direct
     counterpart in the data. Two runs of one dataset on one day share a
     partition and their errors cancel there; per run they are two defects.
     The partition is still checked, as the weaker comparison for free: a
     run's rows have to be under the `ingest_date` its manifest names.
   - **A mismatch is an error, and the argument above for a warning turned out
     not to apply.** `_flush` increments `rows_written` and `files_written` as
     it writes each file and `_finish` writes the manifest on the failure path
     too, so a run killed mid-fetch claims exactly what it durably wrote and
     reconciles. The partial run this step worried about does not fire the
     check. What is left when it is excluded is a zone that has been edited,
     which ADR-4 says cannot happen. Two states are deliberately silent: a
     manifest claiming zero rows with no Parquet, which is the "ran, found
     nothing new" case the manifests exist to record, and a `status: failed`
     manifest whose numbers agree.

   Watermark disagreement warns rather than fails, in either direction. A
   manifest ahead of the data is a run killed between advancing `watermark_out`
   and flushing, and it costs nothing because `resolve_watermark` resumes from
   the data and never from a manifest. Data ahead of every manifest is the
   same finding as UNRECORDED seen from the other side.

   A dataset directory in the zone that the registry does not name warns and is
   reconciled anyway. That is `raw_city_budget` and `raw_street_trees` on the
   local zone, the same ADR-10 residue `make parity-columns` warns about, and
   it is the same call step 2 made about an extra table on the BigQuery side.

2. ~~**Assert the BigQuery external-table column sets against DuckDB's.**~~
   **Done 2026-08-05**, and the risk stopped being a risk on the way: it fired
   before the check did. From 2026-08-01, unowned since. Row counts are
   compared and agree; column sets are still compared by eye. The risk is
   specific rather than theoretical: DuckDB reads the raw zone with
   `union_by_name`, so a column that appears in only some Parquet files is
   present there, while a BigQuery external table infers its schema from the
   files it scans and can disagree. That is a silent difference until a model
   references the column.

   `scripts/parity-check.py` already connects to both engines, already knows
   how to line up a model across them, and already has a `--all-staging` mode.
   Extend it rather than writing a second script: a `--columns` mode that
   compares `information_schema` on both sides and reports columns present in
   one and not the other. Keep it credential-gated and out of CI, exactly as
   the row comparison is and for the same reason.

   **What was built, and the one place it departs from the above.** The
   `--columns` mode compares BigQuery's `INFORMATION_SCHEMA.COLUMNS` against
   **the zone**, read through `raw_zone.read_sql`, rather than against the local
   DuckDB warehouse. This plan's own constraint is what forced it: "whatever
   these check, they check against the zone, not against a copy of it". The
   local warehouse is a copy of whichever zone `make load` last read, so
   comparing warehouse to warehouse reports a `data/raw` versus `gs://` mismatch
   as a column defect. It is also the stronger check and needs no local build.
   `make parity-columns` runs it.

   The defect that fired was its acceptance test rather than a demonstration
   made up for the purpose: it named `raw_datasf.raw_building_permits` and all
   five missing columns on the zone as it was, and passes on the zone with
   `load.py`'s explicit union schema. `ingestion/load.py` is the cause fix;
   `reference_file_schema_uri` was rejected in writing, and `_external_table`'s
   docstring carries the argument.

   **It does not subsume PLAN-5 step 9.** Column sets are not values: the r9
   failure of the same morning was a change in the contents of an unchanged
   column, and this check passes on the zone that caused it. See PLAN-5 step 9.

   That step landed later on 2026-08-05 as ADR-11, and the checker went into
   `check_derived.py` rather than here, as this plan's open question expected.
   The two remain neighbours that do not overlap: `--columns` asks whether
   BigQuery and the zone agree about a table's shape, and `check-derived` asks
   whether the zone agrees with the code and the raw data. Neither would have
   caught the other's defect.

## Out of scope

- The publish object count. That is PLAN-5 step 12, because PLAN-5 step 3 is
  what changed the number.
- Anything about `default_table_expiration_ms`. Checked on 2026-08-04: no
  dataset the project creates carries one, and none of the 23 objects in
  `dbt_dev` carries an `expirationTime`. That residue item is closed, not
  carried.
- Making either check part of the weekly `dbt.yml` cron. Get them working by
  hand first.

## Done when

- [x] A manifest mismatch on the fixture zone fails `make check`, and the
      failure names the dataset and the discrepancy. Done 2026-08-05.
      Demonstrated on a zone built by the same fixture ingest `ci-build` runs,
      broken three ways: a manifest edited to claim 999 rows (exit 3), a
      manifest deleted (exit 4), and a Parquet file deleted (exit 3). Each
      names the table, the run id and both numbers.
- [x] `parity-check.py --columns` reports a column present in one engine and
      not the other, demonstrated by making one differ on purpose. Done
      2026-08-05, and it did not have to be made to differ: it was already
      differing, which is why `make build-bigquery` was red. Demonstrated in
      both directions anyway, by recreating one external table with
      `autodetect` and watching the check go red and then green.
- [x] Both are documented where someone would look: the script headers, the
      Makefile target comments, and CLAUDE.md's `scripts/` entry. Done for
      step 2. Step 1's half done 2026-08-05, in its module header, the
      `check-runs` and `ci-build` comments, the `ci.yml` step comment, and
      CLAUDE.md's `ingestion/` and `tests/` entries and target list.

## Open questions

- Does the manifest check belong in `make check`, or in `check_derived.py`,
  which already exists to assert one zone is not behind another? They are
  adjacent problems and two scripts asserting neighbouring invariants may be
  one script. Look at `check_derived.py` before writing a new file. **There is
  now a precedent, and it went the second way.** PLAN-5 step 9's code-stamp
  check joined `check_derived.py` as a third verdict rather than becoming a
  script, and the reasoning transfers: that file already reads the zone rather
  than the warehouse, already parses a manifest, and now grades three verdicts
  with distinct exit codes. What does not transfer is the reader: the run
  manifests are `ingest.py`'s and live in the raw zone, so a fourth verdict
  there would make `check_derived.py` about two zones' manifests rather than
  one zone's currency. Decide that on its merits rather than on the precedent.
  **Answered 2026-08-05: a separate file, and the reader is what decided it.**
  The precedent did not transfer for exactly the reason above. The code stamp
  was a third record in the same manifest, in the same zone, read by the same
  reader, answering the same question. These manifests are `ingest.py`'s, in
  the other zone, and the question is whether one zone agrees with itself. The
  second argument is when each runs: `check-derived` is a prerequisite of `make
  build` because what it catches makes a build wrong, and `check-runs` gates
  nothing, because a manifest that misdescribes the zone makes
  `mart_pipeline_freshness` wrong and every model correct. Folding them
  together would have meant one exit code covering both, and a stale derived
  zone would have started wedging builds for a reason that does not.
- Is `raw_ingest_runs` the right thing to reconcile against, or should the
  check read the manifests from the zone directly? Reading the zone is the
  stronger check, since it does not assume `load.py` did its job, but it
  duplicates the manifest parsing that `load.py` already owns.
  **Answered 2026-08-05: the zone, and the duplication the question worried
  about does not exist.** `raw_zone.runs_read_sql` is the one reader of the
  manifests and `load.py` builds `raw_ingest_runs` from that same call, so
  `check_runs.py` calls it too and there is one parser rather than two. That
  leaves only the argument for the zone: this plan's own constraint says these
  checks read the zone and not a copy of it, the warehouse copy assumes
  `load.py` did its job when that is part of what is being checked, and reading
  the zone means the check runs before `make load` rather than after it.
