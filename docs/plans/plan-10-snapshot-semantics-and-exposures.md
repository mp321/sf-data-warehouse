---
status: draft
date: 2026-09-10
related: [adr-18-the-raw-zone, adr-19-withdrawn-registrations, adr-20-registry-history-as-dbt-snapshot]
---

# PLAN-10. Finish snapshot semantics, record registry history, declare consumers

First of three monthly milestones under the 2026-09-10 career roadmap
(outside this repo). Written as a stub so the first working session starts
from a decision rather than a blank page. Every step here is also the
project's own next step; nothing is added for portfolio reasons alone.

## Goal

A snapshot dataset's staging model returns the newest partition and nothing
older, the warehouse keeps a compact history of withdrawn registrations
without depending on the raw zone keeping every partition, and dbt docs
show what consumes the marts. `make check` and `make build-bigquery` are
green throughout.

## Why now

ADR-19 landed steps 1 to 3 on 2026-09-07 and left step 4, the shared macro,
unimplemented. Until it lands the marts still return 27 records the city's
registry no longer holds, the completeness guard gates nothing, and the
zone's 55 MB/day growth has no next decision to feed. The dbt certification
sit is planned for late October and its domains (materializations,
snapshots, exposures, tests) are exactly this work.

## Constraints

- The raw zone's append-only rule, its three exceptions, the superset proof
  and the 1 GB threshold stand as ADR-18 states them. This plan deletes
  nothing from the zone and does not touch `prune_raw.py`'s proof.
- Order is the decision, per ADR-19: the macro (step 4 there) lands only
  after the completeness guard gates `make build`.
- Every model must still compile and build on DuckDB and BigQuery (ADR-1).
  Steps that touch a staging model's column list carry `make build-bigquery`
  in their checkpoint (CLAUDE.md).
- The PR gate stays credential-free (ADR-1). Anything needing the bucket or
  BigQuery is run by hand and recorded in the dev note.
- H3 cells stay precomputed BIGINTs (ADR-5). No spatial functions in models.

## Steps

1. **Baseline before touching anything.** On the bucket zone, after the
   09:17 UTC ingest: `make rebuild` wall time, row count of every staging
   model and mart, and `make check-snapshots` output. Record all of it in
   the dev note. This is the evidence the row-count movement in step 3 is
   measured against.
2. **Gate `make build` on the completeness guard.** `check-snapshots`
   becomes a `BUILD_PREREQS` entry so a SHORT verdict stops the build.
   Add the fixture case to `make ci-build` so the gate is exercised on
   every PR without credentials.
3. **ADR-19 step 4, the shared macro.** One macro in `dbt/macros/` that,
   given a snapshot source, restricts the staging model to `grain_key`s
   present in the newest `ingest_date`. Applied to every `refresh: snapshot`
   model (`business_locations`, the two boundary sets, census block groups,
   film locations), not only the one that forced it. Capture the one-time
   row-count movement per model against step 1. Expected: only
   `stg_datasf__business_locations` moves, by 27.
4. **Argue and settle ADR-20** (draft in `docs/decisions/`). If accepted,
   add `dbt/snapshots/snap_business_locations.sql` with
   `invalidate_hard_deletes: true` (dbt-core is pinned `>=1.8,<2.0`; the
   1.9 `hard_deletes` form is optional), keyed on `uniqueid`, and decide
   which workflow runs `dbt snapshot`. If refused, record why in the ADR and
   skip to step 6.
5. **Verify the snapshot captures a withdrawal.** Run it on two consecutive
   days on the bucket zone and confirm a withdrawn `uniqueid` gains a
   `dbt_valid_to`. Without this the snapshot is a table nobody has seen
   work.
6. **Exposures.** One `_exposures.yml` declaring the published Parquet
   export and the two context packs as consumers of the marts, with owner
   and URL. `make docs` shows them on the lineage graph.
7. **Materialization check, measured.** Time `mart_activity_by_h3` as a
   table (today) against an incremental build keyed on month, on the bucket
   zone. Write the numbers in the dev note. Expected outcome: stay a table,
   because the full rebuild is cheap at this scale and incremental adds a
   correctness surface. Only write an ADR if the measurement says otherwise.
8. **Docs.** CLAUDE.md current-state rows for staging, the guard, and the
   snapshot; README metrics (test count will move); `docs/README.md` ADR
   table; dev note.

## Out of scope

- The decision ADR-19 declines to take: whether the newest-partition rule
  lets `prune_raw.py` treat older partitions as unreachable. That needs its
  own acceptance test and its own ADR after step 5 has run for a while.
- Scheduling `make publish`, or any change to `retention.yml`.
- The Overture or OSM spatial layer (milestone 2) and the map viewer
  (milestone 3). Neither starts until this plan is `done`.

## Done when

- [ ] Step 1 baseline is in the dev note with wall time and per-model counts.
- [ ] `make build` refuses a SHORT snapshot partition, and `make ci-build`
      proves it on a fixture.
- [ ] Every snapshot staging model reads only the newest `ingest_date`, and
      the marts return zero records absent from the newest registry.
- [ ] ADR-20 is `active` or refused with reasons; if active, a real
      withdrawal has a `dbt_valid_to` in the snapshot table.
- [ ] Exposures render in `make docs`.
- [ ] The materialization measurement is written down with numbers.
- [ ] `make check` green; `make build-bigquery` green; both recorded.

## Open questions

- Which workflow runs `dbt snapshot`? `ingest.yml` runs no dbt today; the
  weekly `dbt.yml` runs BigQuery only. A daily snapshot needs a daily dbt
  run against some warehouse, which is a change to what CI does.
- Does the BigQuery target get the snapshot table? Today BigQuery stores no
  raw bytes at all (ADR-9). A snapshot table would be the first data stored
  in BigQuery rather than read from GCS. That is a cost to name in ADR-20.
- Is `_socrata_updated_at` reliable enough for a `timestamp` strategy, or
  does the `check` strategy over the columns that matter cost less in
  false changes? Measure on the bucket zone before choosing.

## Session kickoff (temporary, delete this section when the plan is done)

Added 2026-09-11 so a session can start without reading the whole repo.

### Before the first session, by hand

1. The working tree on branch `ra33` holds the 2026-09-07 session's work
   (ADR-19, `check_snapshots.py`, the watermark change) plus this plan and
   ADR-20. On 2026-09-11 `pytest tests -q` passed 240 and `ruff check` was
   clean over all of it. **Keep the watermark change.** It is ADR-19 step 2,
   the ADR is `active`, and dropping it would leave an accepted decision
   describing code that does not exist. Commit in two commits (the
   2026-09-07 work, then the PLAN-10 and ADR-20 files), push `ra33`, open a
   pull request so `ci.yml` runs the credential-free gate on it, merge to
   `main`.
2. Decide whether the scheduled jobs keep running while the plan is idle.
   See the next section.

### Pause and resume the scheduled jobs

Cost today is zero: Actions minutes are free on a public repository, the
zones sit near 1.3 GB against Google Cloud's 5 GB always-free storage
allowance, and BigQuery reads external tables (no stored raw bytes) with one
weekly build far inside the 1 TB free query tier. At 55 MB/day the raw zone
crosses the allowance around 2026-11-13, after which storage bills cents per
month. The reason to pause is not money. It is that every daily ingest adds
a partition the proof cannot clear until step 3 lands.

Pause (stops zone growth, the BigQuery load, and the weekly build):

    gh workflow disable ingest.yml
    gh workflow disable dbt.yml
    gh workflow disable retention.yml
    gh workflow list

Resume, the morning step 1 starts:

    gh workflow enable ingest.yml
    gh workflow enable dbt.yml
    gh workflow enable retention.yml
    gh workflow run ingest.yml

Same thing in the browser: repository, Actions, pick the workflow, the
"..." menu, Disable workflow; Enable workflow reverses it. Pausing loses
nothing: delta datasets fetch everything updated since the watermark on
resume, and snapshot datasets fetch in full every run (ADR-19 step 2).
Safety net either way: Google Cloud console, Billing, Budgets and alerts,
create a $1 budget with email alerts at 50, 90 and 100 percent.

### Prompt for a session

Paste into Claude Code (or Gemini CLI) opened at the repository root,
replacing N. Sonnet for implementation steps; Opus for step 4.

    Read CLAUDE.md (only the working agreement and the current-state
    table), docs/plans/plan-10-snapshot-semantics-and-exposures.md, and
    docs/decisions/adr-19-withdrawn-registrations.md. Read nothing else
    unless a step names it. Do step N of PLAN-10 and nothing after it.
    Write the tests before the change. Stop when `make check` is green,
    tick the step's box in the plan, and append to
    docs/dev-notes/<today>.md following docs/dev-notes/TEMPLATE.md. Never
    run git add, commit, push, merge, rebase or reset. Ask before touching
    the bucket or BigQuery.

For step 4 add: "Argue ADR-20 against its own Options section before
implementing anything. If the case against option C wins, set the ADR to
superseded with the reason in a dated note and skip to step 6."

Steps 1, 5 and 7 need the bucket zone. Run `set -a; source .env; set +a`
first and say so in the prompt.
