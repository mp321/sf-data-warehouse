---
status: active
date: 2026-07-30
related: [adr-1-warehouse-targets, adr-2-spatial-strategy]
---

# ADR-3. Dataset scope: two core sources, two demoted

## Context

`ingestion/datasets.py` registers four DataSF datasets and `README.md`
presents all four as equals behind the headline question, "does city spending
track 311 demand by department and district".

The four are not equally useful or equally hard, and two of them do not
support that headline. Only one staging model exists, so every hour on one
dataset is an hour not spent on another. Without explicit scope, priority gets
decided by whichever dataset is most fun, which here is `film_locations`,
described in its own registry entry as "the fun one".

Two earlier constraints apply: ADR-2 makes geography cheap only for datasets
carrying point coordinates, and ADR-1 makes every in-scope dataset a recurring
cost across two engines, freshness checks, tests, and a staging model that has
to survive upstream schema changes.

## Options considered

**A. Keep all four first-class.** Matches what README.md promises. Against:
quadruples the staging surface for a project with one staging model, and
implies the budget join works when we have not shown that it does.

**B. Cut to 311 only.** Sharpest focus, no joins to get wrong. Against: a
single-source warehouse never exercises the staging-to-marts split, and it
discards `building_permits`, the cheapest useful join available.

**C. Two core, two demoted, reversibly.** Against: the README headline depends
on `city_budget`, so demoting it means the headline is aspirational until the
README is rewritten.

## Decision

Option C.

**Core, full treatment.**

- `311_cases` (`vw6y-z8j6`). The anchor: high volume, daily updates, and the
  only dataset with a real record lifecycle (open through closed), which is
  what makes the append-only raw zone and deduplicating staging model worth
  having. Carries `lat` and `long`, so ADR-2 applies directly. Backfill from
  2024-01-01.
- `building_permits` (`i98e-djp9`). Kept for three reasons: point coordinates
  so it shares the H3 machinery, joins to 311 on district and time with no
  taxonomy crosswalk needed, and cost and valuation fields messy enough to be
  real cleaning practice. Backfill from 2024-01-01.

**Demoted, still ingested.** No marts, no freshness SLA, staging only if
something needs it.

- `city_budget` (`xdgd-c79v`). The spend-versus-demand join requires matching
  budget departments to the 311 `agency_responsible` field, two independently
  maintained taxonomies with no reason to agree. Building and maintaining that
  crosswalk is a project in itself and is the real work hiding behind one line
  of README. It also has no point geography and updates annually, so ADR-2 and
  incremental ingestion both buy it nothing. Cheap to keep ingesting; no marts
  until the crosswalk question has an answer.
- `film_locations` (`yitu-d5am`). Demo and smoke test: small enough that a
  full ingest proves the pipeline end to end in seconds, which is how
  `SETUP.md` Phase 3 uses it. Its locations are free text rather than
  coordinates, so it cannot participate in ADR-2, and it shares no join key
  with anything else. Pipeline canary is a real job.

**Out of scope.** No new datasets until both core sources have a staging
model, at least one mart, and passing tests. This is a sequencing rule, not a
judgement on the datasets.

## Consequences

**Buys.** Effort concentrates on the two sources that actually join, so the
marts layer gets built instead of the registry getting longer. Both core
datasets exercise the ADR-2 H3 machinery, which is the only way we find out
whether the interior-cell classifier is correct. Freshness SLAs mean something
because they exist only where staleness is a signal, matching what
`_datasf__sources.yml` already does.

**Costs.** README.md now overstates the project and has to be rewritten to
describe the crosswalk as an open question. We keep paying ingestion and
storage for two unmodelled datasets, close to free but not zero, and demoted
datasets rot quietly: nothing will notice if DataSF changes the budget schema,
because nothing reads it.

**Lock-in.** Promoting `city_budget` later means building the crosswalk
against however much history exists then, and crosswalks are far easier to
build while someone still remembers why two categories were considered
equivalent. We are trading institutional memory for effort knowingly. The
2024-01-01 backfill floor also hardens over time: widening it via `--since`
gets slower every month, and under ADR-1's Parquet raw zone it means rewriting
partitions rather than appending.

## Revisit if

- Both core sources reach staging plus one mart with green tests, which is the
  trigger to reconsider the whole scope.
- Someone finds or builds a crosswalk between budget departments and 311
  `agency_responsible`, which promotes `city_budget` immediately.
- A demoted dataset breaks ingestion, at which point drop it rather than fix
  it. Demoted datasets do not earn maintenance.
