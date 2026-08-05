---
status: active
date: 2026-08-04
related: [adr-3-dataset-scope, adr-5-h3-computation, adr-7-dataset-scope-2, plan-5-narrow-and-polish]
---

# ADR-10. Seven datasets, two H3 resolutions: the project is purely spatial

Supersedes ADR-7.

**ADR-5 is amended, not superseded, and stays `active`.** This ADR changes one
line of it, the resolution list. ADR-5's actual decision, that H3 cells are
computed in Python and stored as BIGINTs because BigQuery has no H3 function
to dispatch to, is untouched and is still a hard constraint in CLAUDE.md.
Marking it superseded would file a live rule under history, which is the one
outcome the superseding convention exists to prevent.

## Context

ADR-7 set scope at nine datasets and ADR-5 stored three H3 resolutions on
every point row. Both were the right size for a project that was still finding
out what it was. Neither is the right size for one that has found out.

**The forcing constraint is that this repo is read in twenty minutes or it is
not read at all.** Nine datasets, 22 models and seven marts is roughly twice
what the argument needs, and the excess is not neutral. It makes the two parts
worth reading, the raw zone design and the H3 precompute, harder to find. A
reviewer who runs out of patience in the staging directory never reaches
`spatial.py`.

Three specific things were carrying cost without carrying weight.

**`city_budget` was the only non-spatial dataset.** ADR-7 conceded it existed
mainly so that a non-spatial mart existed. The mart it fed could not answer
the question anyone actually wants from budget data, spending against 311
demand, because that needs a crosswalk between budget `department_code` and
the 311 `agency_responsible` field: two independently maintained taxonomies
with no reason to agree, and building it is a project in itself. ADR-3 blocked
a budget mart on exactly that crosswalk; ADR-7 unblocked it on the grounds
that a mart staying inside one taxonomy owes the crosswalk nothing. That is
true and it is also the problem. A mart that stays inside one taxonomy is a
budget rollup sitting beside a spatial warehouse, related to it by nothing.

**`street_trees` was justified as the H3 stress test, and no longer had to
be.** ADR-7's argument was that trees are dense, evenly spread and stable,
where 311 and permits cluster hard enough to hide a broken cell assignment.
That argument is sound and it now describes `business_locations` too, at 365k
rows against 198k, exercising the identical code path, and already load
bearing as the population-independent denominator. The stress test survives
the cut; a second copy of it does not earn 25 MB and a staging model.

**H3 resolution 9 was kept so a guess stayed checkable.** ADR-5 carried r9
explicitly so that ADR-2's original resolution guess "stays checkable". That
is a dev-note reason paying a permanent schema cost: a third of the widest
derived table, on every point, forever, to preserve the ability to re-derive a
number that could simply be written down.

## Decision

**Seven datasets. Every one of them is spatial.** `city_budget` and
`street_trees` are cut end to end: registry entry, source, staging model,
marts, fixtures, published exports and prose. The `kind` field in the dataset
registry now has two legal values, `point` and `polygon`, where it had three.

**Two H3 resolutions, 8 and 10.** r8 is the readable map, r10 is the
membership resolution. `RESOLUTIONS` in `ingestion/spatial.py` is `(8, 10)`
and `h3_r9` is gone from every model, macro and yml.

**`mart_activity_by_h3` moves to r8, not r10.** It was documented as the map
and r10 is a join key rather than a map.

**`film_locations` stays.** At 204 KB and 2,214 rows it ingests end to end in
seconds, which makes it the pipeline canary. Cutting the 25 MB dataset and
keeping the 204 KB one is the right way round.

**311 stays at `start_date` 2024-01-01.** Roughly 155 bytes per row as
Parquet, so 2024 to date is about 250 to 310 MB against 5 GB of always-free
GCS. Full history at 8.8 million rows would be about 1.4 GB and still fits, so
widening later is a scope decision and not a storage one. Do not trim below
2024: the marts are monthly and a window shorter than that cannot show a
seasonal pattern, which is most of what they are for.

## The measurements, so they stay checkable in prose

This is what r9 was being kept for. Recorded here, where it costs nothing.

Distinct cells occupied by 705,067 points, measured on the real zone before
the cut:

| Resolution | Approx. hexagon | Cells occupied |
|---|---|---|
| 8 | 460 m across, 0.737 sq km | 15,773 |
| 9 | 175 m across, 0.105 sq km | 29,040 |
| 10 | 65 m across, 0.015 sq km | 47,627 |

`mart_activity_by_h3` at each resolution, on the same zone with `street_trees`
already excluded, at the grain (cell, dataset, category, month):

| Resolution | Mart rows |
|---|---|
| 8 | 140,342 |
| 9 | 238,742 |
| 10 | 330,960 |

r8 is 41% smaller than r9 and 58% smaller than r10, and this mart is the
largest published artifact in the project at any of the three.

The membership agreement measured on 2026-07-31, which is the number that
justifies r10 for boundary assignment, is in the header of
`dbt/tests/assert_h3_membership_matches_exact_pip.sql` and keeps its r9
column for the same reason: three points show a trend that two do not.

## Consequences

**The project makes one claim now.** "Count events inside this boundary, with
no geometry at query time." Everything here serves that claim or is a
denominator for it.

**The stress test is `business_locations`, and that is now load bearing.** It
is the only dataset dense and wide enough to expose a broken cell assignment.
If it is ever cut or narrowed, the H3 machinery loses its stress test and
something else has to take the role. Note also that its `location_started_at`
reaches back to 1967, which is why `mart_activity_by_h3` publishes into 879
monthly partitions.

**The flat lat/lon code path is still covered, and by more than
`film_locations`.** PLAN-5 raised this as an open question on the premise that
`street_trees` and `film_locations` were the only two datasets with flat
`latitude`/`longitude` columns. That premise was wrong. `311_cases` is flat
too, and is the largest dataset here. The fixtures' adversarial coordinate
cases already sit on `311_cases` (unparseable) and `business_locations`
(out of bounds, and State Plane feet in a degree column); `street_trees`
carried a 9999 diameter sentinel and a missing plant date, neither of which is
a coordinate case. Nothing had to move.

**Any change to `RESOLUTIONS` invalidates every stored cell.** Unchanged from
ADR-5, and it is why this cut required `make clean-derived` then `make
spatial` rather than a dbt rebuild. A zone written before this ADR still holds
`h3_r9` and street tree rows; there is one zone at a time, so a bucket-backed
zone needs the same rebuild before it agrees with this code.

**What this does not reopen.** ADR-5's decision that cells are computed in
Python and stored as BIGINTs, ADR-6's no-geometry-at-query-time rule, and
ADR-1's both-engines constraint are all untouched. This ADR changes how many
of a thing there are, not what the thing is.

## Alternatives considered

**Keep `street_trees`, cut `business_locations`.** Rejected: businesses are
the denominator behind every "per 1000 businesses" rate, and trees are not a
denominator for anything.

**Move `mart_activity_by_h3` to r10 rather than r8.** Rejected on the
measurement above. It would have made the largest published artifact 2.4 times
larger while making the map unreadable, and r10 already has a job.

**Keep r9 and drop r8.** Rejected: r9 is between the two things anyone needs
here, which is what made it easy to cut. Membership wants the finest available
and a map wants the coarsest that still resolves a neighborhood into many
cells.

**Write two ADRs, one per cut.** Rejected. PLAN-5 exists partly to reduce the
document count, and answering it with two new records would work against that.
The two cuts also share one rationale, which is the twenty-minute read.
