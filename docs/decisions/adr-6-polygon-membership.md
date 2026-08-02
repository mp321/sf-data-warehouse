---
status: active
date: 2026-07-31
related: [adr-2-spatial-strategy, adr-5-h3-computation]
---

# ADR-6. Polygon membership: covering cells plus exact refinement, both at precompute time

Supersedes ADR-2.

## Context

ADR-2 designed the scheme but predates any of it existing. Implementing it
surfaced three things it could not have known, and one of them changes the
decision rather than filling it in.

**ADR-2 put exact refinement at query time, behind an engine-specific macro.**
Its words: "Only points in boundary cells get an exact point-in-polygon test",
and "Boundary refinement is the one place engine-specific spatial code is
permitted". That means a query counting cases in a neighborhood invokes
`ST_Contains` on DuckDB and `ST_CONTAINS` on BigQuery, with planar and
spherical predicates that genuinely differ near boundaries. It also means the
DuckDB spatial extension is back in the build, which ADR-1 and ADR-5 both
worked to keep out.

**ADR-2's resolution estimate was wrong by two orders of magnitude.** It
guessed a resolution 9 seed table "in the low hundreds of thousands of rows".
Measured: the entire bridge across all three boundary sets and all three
resolutions is 98,655 rows, and neighborhoods at r9 alone are 1,762. The table
is small enough that the finest resolution is affordable, which ADR-2 assumed
it would not be.

**ADR-2's revisit threshold fires immediately.** It said to reconsider if more
than roughly 20 percent of populated cells are boundary cells. Measured for
analysis neighborhoods: 95.8 percent at r8, 66.3 at r9, 35.7 at r10. Even at
the finest resolution the threshold is passed nearly twice over, because San
Francisco's neighborhoods are small and intricate relative to any hexagon big
enough to be worth aggregating on.

Meanwhile ADR-2's worry about the interior classifier, "the highest-risk code
in the project ... it fails silently and at scale", turns out to be answerable
by the H3 library rather than by us: `h3shape_to_cells_experimental` takes a
`contain` argument and will return exactly the fully-interior set.

## Options considered

**A. Implement ADR-2 as written.** Query-time refinement behind a dispatch
macro. Against: puts a geometry engine back in every query, reintroduces the
DuckDB extension download, and produces answers that differ between targets
near boundaries, which is the specific failure ADR-1 exists to prevent. Also
slow: it re-runs the geometry on every query rather than once.

**B. Cell-based membership only, no exact test anywhere.** Assign each cell to
one boundary and accept the error. Simplest possible, pure integer join, no
geometry code at all. Against: measured agreement with exact
point-in-polygon is 94.7 percent for neighborhoods at r10 and 88.2 at r9.
A mart that is five percent wrong at the boundary is wrong in a way that
correlates with geography, so every neighborhood comparison inherits a bias
rather than noise.

**C. Covering cells as the coarse filter, exact refinement at precompute
time.** Against: the assignment becomes a stored column rather than something
a query derives, so a boundary change means re-running `make spatial` and not
just rebuilding dbt. More code in the precompute step, including a
point-in-polygon implementation this project now owns and has to be right.

## Decision

Option C. ADR-2's scheme, with the refinement moved from query time to
precompute time, which is the change that supersedes it.

`ingestion/spatial.py` covers each boundary at each resolution three ways,
using the H3 library rather than a hand-written classifier:

- `contain='overlap'` gives every cell touching the polygon. The covering set,
  and the coarse filter.
- `contain='full'` gives every cell entirely inside it. A point in one of
  these is inside the boundary with no test needed. This is ADR-2's "fully
  interior" set, and having the library compute it retires ADR-2's
  highest-risk-code concern.
- `contain='center'` gives every cell whose centre is inside it. Because
  polygons within a set do not overlap, these sets are disjoint, so this
  assigns each cell to exactly one boundary.

Then, for every point:

1. If its r10 cell is fully interior to a boundary, it inherits that boundary.
   About four points in five. Exact by construction.
2. Otherwise, run an exact point-in-polygon test against only the boundaries
   whose covering set includes that cell, which is two or three rather than
   all 41. This is ADR-2's coarse filter doing exactly the job it was chosen
   for, and it is what makes the exact step affordable.

The result is `derived_point_boundary`, one row per (point, boundary set), and
it is exact. **Nothing at query time knows what a polygon is.**

**Three flags on the bridge, not one.** `is_interior` and `is_primary` as
above, plus `is_allocation_cell`, which is the pre-collision centre set and is
the only one that may be used to spread a measure across cells. They are
separated because using `is_primary` for population interpolation loses
residents: at r8 one cell covers dozens of block groups, `is_primary` keeps
one, and 653,000 of 874,000 San Franciscans disappeared before this was split
out.

**Geometry is pure Python, in `ingestion/geometry.py`.** Crossing-number
point-in-polygon and spherical-excess area, about a hundred lines, no shapely
and no GEOS. Two implementations exist: a scalar one that builds the test
oracle and a numpy-vectorised one that does the work, and `spatial.py` asserts
they agree on the sample every run.

## Consequences

**Buys.** "Count 311 cases inside this neighborhood" is a `group by` on a
string that was decided by an integer cell lookup. Verified: the query plan
contains no geometry operator, and the answer is exact rather than 94.7
percent right. Both engines get identical assignments by construction, because
neither computes them. The DuckDB spatial extension stays out of the build, so
ADR-1's offline-clone property survives, and ADR-2's permission for
engine-specific spatial code is withdrawn rather than used.

**Costs.** Boundary assignment is now a stored column, so changing a boundary
means re-running `make spatial`; a dbt-only rebuild will faithfully reproduce
the old answer. Exact refinement costs about three minutes on 700,000 points
across three boundary sets, most of it in the test oracle. This project now
owns a point-in-polygon implementation, which is a thing that can be wrong;
the mitigation is two implementations plus a 30,000-row cross-check, which is
weaker evidence than using a library and stronger than nothing. And the bridge
carries three boolean flags whose differences are subtle, which is a real trap
documented in three places precisely because it is one.

**Lock-in.** The membership resolution is fixed at 10 by the stored
assignments; changing it means recomputing every point. Boundary sets are now
a closed list in `datasets.py` rather than anything a query can extend, so
asking "which police district is this in" means ingesting the polygons and
re-running the precompute, not writing a join. And the exactness claim is now
load-bearing: `assert_point_boundary_is_exact.sql` fails on a single
disagreement, so a legitimate future change to sampling or tolerance has to
argue with a test that has no threshold to relax.

## Measurements

On the full raw zone, 2026-07-31. Cell-based membership against exact
point-in-polygon, 10,000 sampled points per boundary set:

| Boundary set | r8 | r9 | r10 |
|---|---|---|---|
| analysis_neighborhood | 72.6% | 88.2% | 94.7% |
| supervisor_district | 83.6% | 92.7% | 96.8% |

Interior cells alone agree 100 percent, which is what the design predicts and
is the reason the split is worth having. Assignment method mix at r10:
neighborhoods 79.2 percent interior, supervisor districts 87.0, block groups
24.9. Block groups are small enough that most of their cells straddle a
boundary, which is the clearest signal available that resolution has to be
chosen per boundary set rather than once.

## Revisit if

- Interior-cell share at r10 falls below about half for neighborhoods, meaning
  refinement is doing most of the work and the coarse filter has stopped
  earning its keep.
- `make spatial` outgrows the time anyone will wait for, which is where the
  refinement needs to become incremental.
- A question arrives needing real geometry throughout, such as distance,
  buffers or routing. This scheme does not help with any of them, and ADR-2
  said so first.
