---
status: superseded
date: 2026-07-30
related: [adr-1-warehouse-targets, adr-6-polygon-membership]
---

# ADR-2. Spatial strategy: H3 coarse filter, exact geometry only on boundaries

## Context

Most interesting questions here are spatial: cases per supervisor district,
permits by neighborhood, spend against demand by district. All we carry today
is `latitude`, `longitude`, and whatever `supervisor_district` string DataSF
stamped on the row.

That upstream column is not usable: null on a meaningful share of 311 rows,
assigned at report time rather than recomputed when boundaries change, and
absent from some datasets entirely. We have to assign points to polygons
ourselves.

The obvious `ST_Contains` join collides with ADR-1. BigQuery has built-in
`GEOGRAPHY` with spherical geometry; DuckDB has a planar GEOS-backed `spatial`
extension that must be installed and loaded per connection and moves fast
across versions. Writing against both means either two dialects of every
spatial model or accepting that targets disagree near boundaries, where
spherical and planar predicates genuinely differ.

There is also a cost problem: full point-in-polygon over millions of 311 rows
against detailed boundaries is expensive, and it re-runs on every build.

## Options considered

**A. Native spatial types per engine, dispatched by macro.** Most expressive
and exact. Against: makes the targets genuinely different rather than
differently spelled, produces boundary disagreements that are miserable to
debug, and makes DuckDB builds depend on an extension download, breaking the
offline-clone property ADR-1 just bought.

**B. Precompute the district label in Python at ingestion time.** Simple,
exact, engine independent. Against: puts business logic in ingestion, so raw
stops being raw, and fixing a boundary or a bug means re-ingesting rather than
rebuilding.

**C. H3 cells as a coarse filter, exact geometry only where it matters.**
Against: more machinery than the alternatives, boundary cells still need an
exact test somewhere, and H3 resolution becomes a tuning parameter someone has
to understand.

## Decision

Option C.

Every point-bearing row carries a precomputed H3 cell id, stored as a string
like every other raw column. A seed table maps cells to districts and
neighborhoods, flagging cells that straddle a boundary. Then:

1. Points in a fully-interior cell inherit that cell's district by an equality
   join. No geometry, no extension, no engine difference.
2. Only points in boundary cells get an exact point-in-polygon test. That set
   scales with district perimeter, not area.

Boundary refinement is the one place engine-specific spatial code is
permitted, isolated behind a single dispatch macro. If neither engine had
spatial support, everything except refinement would still work.

## Consequences

**Buys.** Identical district assignments across both engines for the
overwhelming majority of rows, by construction rather than luck. The join
stays cheap as row counts grow. `data/` builds need no extension download, so
ADR-1's offline-clone property survives. H3 cells also give free
aggregation-by-hexagon for any future map.

**Costs.** A precompute step and a seed table that are now build
dependencies. Resolution is a real tradeoff: too coarse and almost every cell
is a boundary cell, collapsing the optimisation; too fine and the seed table
gets awkward to keep in git. Starting at resolution 9, roughly 0.1 square km
per cell, which should keep the seed table in the low hundreds of thousands of
rows. That figure is a guess and should be measured.

We also accept a bounded correctness gap: interior-cell points are assigned by
cell, not by their own coordinates. That is exact only if "fully inside" is
computed conservatively, so the seed generation script is the highest-risk
code in the project despite running least often, and it fails silently and at
scale.

**Lock-in.** Changing resolution invalidates every stored cell id and requires
reprocessing every point-bearing row, so it behaves like a schema decision
rather than a tuning knob. Committing to H3 also rules out S2 and geohash
without the same reprocessing, and puts the `h3` library in the ingestion path
permanently.

## Revisit if

- More than roughly 20 percent of populated cells are boundary cells, meaning
  the resolution is too coarse to earn its keep.
- Boundary refinement grows past a single macro, meaning engine-specific
  spatial code has leaked into the common path.
- A question arrives needing real geometry throughout, such as distance,
  buffers, or routing, which this scheme does not help with at all.
