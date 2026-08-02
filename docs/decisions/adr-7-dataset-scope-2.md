---
status: active
date: 2026-07-31
related: [adr-3-dataset-scope, adr-5-h3-computation, adr-6-polygon-membership]
---

# ADR-7. Dataset scope, second pass: geography sources in, tiers redrawn

Supersedes ADR-3.

## Context

ADR-3 set scope at two core sources and two demoted ones, and closed with a
sequencing rule: "No new datasets until both core sources have a staging
model, at least one mart, and passing tests."

**The forcing constraint is that ADR-3's own revisit trigger has fired, and
its blocking rule now blocks the work it was protecting.** Both core sources
have staging models and green tests. Neither has a mart, because the marts
are spatial and there were no boundaries to be spatial against: ADR-2 assumed
a polygon source that was never in scope. So ADR-3 forbids adding the polygon
datasets until a mart exists, and the mart cannot exist until the polygons are
added. That is a deadlock, not a sequencing rule.

Three further findings, each of which ADR-3 got wrong or could not have known.

**Film locations does carry usable coordinates.** ADR-3 demoted it partly
because "its locations are free text rather than coordinates, so it cannot
participate in ADR-2". The free-text `locations` column is real, but the
dataset also publishes flat `latitude` and `longitude`, populated on 2,127 of
2,214 rows. The claim was simply false. This was the item the plan flagged as
possibly needing a geocoding decision; no such decision is needed, because
there is nothing to geocode.

**api.census.gov now requires an API key.** Every endpoint, including the
county-level ones that used to be keyless, returns "Missing Key". An ACS
5-year population fetch would therefore put a credential on the critical path
of `make ingest`, which ADR-1 spent a whole decision removing.

**Registered business locations are frequently not in San Francisco.** About
18 percent of rows carry coordinates elsewhere, including other states. This
is correct data: the registry records where a business is, and plenty of
businesses holding a San Francisco tax certificate are located elsewhere.

## Options considered

**A. Keep ADR-3 and work around it.** Treat boundaries as infrastructure
rather than datasets and add them without a scope change. Against: it is a
lie of categorisation. They are ingested from DataSF through the same
registry, the same raw zone and the same staging layer as everything else, and
pretending otherwise means the scope document stops describing the warehouse.

**B. Add the geography sources, leave the tiers alone.** Minimal change.
Against: it leaves `film_locations` demoted on a factually wrong premise, and
leaves boundary sets in whichever tier they land in, where "demoted" would
mean the neighborhoods that every mart depends on earn no maintenance. ADR-3's
own words: "demoted datasets do not earn maintenance."

**C. Rewrite scope with a third tier.** Against: three tiers is one more
concept than two, and the middle one has to justify itself or it becomes the
place things go to avoid a decision.

## Decision

Option C. Nine datasets in three tiers.

**Core.** Point-bearing event sources. Full treatment: staging model, marts,
freshness SLA, H3 cells.

- `311_cases` (`vw6y-z8j6`), `building_permits` (`i98e-djp9`), unchanged from
  ADR-3.
- `business_locations` (`g8m3-pdis`), 365k rows. Added because it is both a
  subject and a denominator: it is the only source that says where commercial
  activity is, which makes "per 1000 businesses" possible, and that rate
  ranks neighborhoods very differently from per-capita.
- `street_trees` (`tkzw-k3nq`), 198k rows. Added because it is dense, evenly
  spread and stable, which makes it the only dataset here that would expose a
  broken cell assignment. 311 and permits cluster hard enough to hide one.

**Reference.** Boundary sets. Staging models and full test coverage, no
freshness SLA, because they change every several years and staleness is not a
signal. Not demoted: every spatial mart depends on them.

- `analysis_neighborhoods` (`ajp5-b2md`), 41 polygons.
- `supervisor_districts` (`f2zs-jevy`), 11 polygons, 2022 boundaries. The 2012
  set (`keex-zmn4`) is deliberately not ingested: carrying two vintages means
  every question needs a date before it can be answered.
- `census_block_groups`, 681 polygons, from the Census TIGERweb service.

**Demoted.** Ingested and modelled, no SLA, no maintenance promise.

- `city_budget` (`xdgd-c79v`). **Promoted to the extent of one non-spatial
  mart.** ADR-3 blocked all budget marts until a crosswalk existed between
  budget `department_code` and the 311 `agency_responsible` field. That
  objection was to the crosswalk, not to the budget data: a mart that stays
  inside one taxonomy owes it nothing. `mart_budget_by_department_year`
  aggregates budget by department and year and does not touch 311. The
  spend-versus-demand question remains open and unattempted.
- `film_locations` (`yitu-d5am`). Still demoted, still the pipeline canary,
  but it now participates in ADR-2 and ADR-6 like any other point dataset,
  and it gets `mart_film_locations` as the demo mart.

**The population denominator is the 2020 Decennial count, not ACS 5-year.**
TIGERweb's Census 2020 block group layer carries `POP100` and `HU100`
alongside the geometry, needs no key, and returns both in one request. The
city total is 873,965, which matches the published 2020 figure exactly.

**Out of scope, and named so the gap is not mistaken for an oversight.**
Parcels and street centrelines. The plan asked for rates per parcel and per
street mile; both need a dataset that is not here, and adding one is a scope
decision rather than a modelling one. Rates are normalised by residents,
housing units, registered businesses and land area instead.

## Consequences

**Buys.** The deadlock is gone and the spatial marts exist. Every core source
now shares one geography implementation, so a bug in it is one bug rather than
four. The population denominator arrives with no credential, keeping
`make ingest` credential-free end to end. And the "reference" tier means the
boundary sets get tests without getting a freshness alarm that would fire
forever on data that is correct.

**Costs.** Nine datasets is more than twice ADR-3's four, and ADR-3's warning
holds: every one is a recurring cost across two engines, freshness checks,
tests and a staging model that has to survive upstream schema changes. The raw
zone roughly doubles. Business locations and street trees backfill fully
rather than from 2024, because they are current-state registries where a
partial backfill by `:updated_at` yields an arbitrary subset. The Decennial
substitution means every per-capita rate divides by an April 2020 denominator
and drifts further from the truth each year, which is invisible in the output.
And promoting `city_budget` at all weakens ADR-3's cleanest line; the mitigation
is that the crosswalk stays explicitly unbuilt.

**Lock-in.** The reference tier is now load-bearing: `dim_neighborhood` is the
denominator for every rate in the warehouse, so a change to the neighborhood
boundary set moves every published number. TIGERweb is a second upstream with
a WAF that rate-limits bursts, so `make ingest` now has a dependency that can
fail for reasons DataSF never fails for. And the 2022 supervisor districts are
baked in: rows from before 2022 carry an upstream district assigned under the
2012 lines, and the two will disagree forever without either being wrong.

## Revisit if

- A question needs rates per parcel or per street mile, which is the trigger to
  ingest one of those datasets rather than to reinterpret an existing
  denominator.
- Someone builds the budget-to-311 department crosswalk, which promotes
  `city_budget` properly and is still the project's headline question.
- The 2020 denominator drifts far enough to matter, most likely when the 2030
  Census lands or if ACS access stops needing a key.
- A demoted dataset breaks ingestion. ADR-3's rule stands: drop it rather than
  fix it.
