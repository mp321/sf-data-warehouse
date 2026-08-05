---
status: active
date: 2026-08-04
related: [plan-1-duckdb-parquet, plan-4-cloud-first-storage, plan-5-narrow-and-polish, adr-1-warehouse-targets, adr-4-raw-zone-layout, adr-9-cloud-raw-zone]
---

# PLAN-7. Check the two claims nothing currently checks

## Goal

Two assertions that run on demand and fail loudly: the run manifests agree
with the data they claim to describe, and the BigQuery external tables have
the same column sets as the DuckDB tables built from the same Parquet.

**Status: step 2 is done as of 2026-08-05, step 1 is open.** The plan went from
`draft` to `active` by being overtaken: the column-set risk stopped being
hypothetical and turned `make build-bigquery` red, so step 2 was implemented
against a live defect rather than a synthetic one. Step 1, the manifest
reconciliation, is untouched and is now the whole remaining plan.

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

1. **Reconcile the run manifests against the data.** `ingest.py` writes a run
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

- [ ] A manifest mismatch on the fixture zone fails `make check`, and the
      failure names the dataset and the discrepancy.
- [x] `parity-check.py --columns` reports a column present in one engine and
      not the other, demonstrated by making one differ on purpose. Done
      2026-08-05, and it did not have to be made to differ: it was already
      differing, which is why `make build-bigquery` was red. Demonstrated in
      both directions anyway, by recreating one external table with
      `autodetect` and watching the check go red and then green.
- [x] Both are documented where someone would look: the script headers, the
      Makefile target comments, and CLAUDE.md's `scripts/` entry. Done for
      step 2. Step 1 owns its own half of this box.

## Open questions

- Does the manifest check belong in `make check`, or in `check_derived.py`,
  which already exists to assert one zone is not behind another? They are
  adjacent problems and two scripts asserting neighbouring invariants may be
  one script. Look at `check_derived.py` before writing a new file.
- Is `raw_ingest_runs` the right thing to reconcile against, or should the
  check read the manifests from the zone directly? Reading the zone is the
  stronger check, since it does not assume `load.py` did its job, but it
  duplicates the manifest parsing that `load.py` already owns.
