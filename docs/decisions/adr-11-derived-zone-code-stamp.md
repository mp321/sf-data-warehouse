---
status: active
date: 2026-08-05
related: [adr-5-h3-computation, adr-9-cloud-raw-zone, adr-10-narrowed-scope, plan-5-narrow-and-polish, plan-7-pipeline-assurance]
---

# ADR-11. The derived zone records the code that built it, and is rebuilt incrementally

Amends ADR-5 rather than superseding it, the same way ADR-10 did. ADR-5's live
decision is that H3 cells are computed in Python and stored as BIGINTs in a
separate derived zone, and that is unchanged. This decides two things ADR-5 left
open: what a re-run of `make spatial` recomputes, and how anything can tell what
computed the zone in the first place.

## Context

ADR-5 says the derived zone is a pure function of the raw zone plus
`spatial.py`. The zone recorded half of that. `_manifest.json` held the raw row
count per dataset, which `check_derived.py` compares against the raw zone, and
nothing at all about the code.

**That gap is not theoretical.** On 2026-08-05 the bucket's derived zone was
found carrying H3 r9 cells that ADR-10 had removed from the code the day
before. Nothing in the project detected it. `make check` is DuckDB-only and
local-zone-only by design (ADR-1), so it never reads the bucket;
`check_derived.py` compared row counts, which agreed, because the raw zone had
not moved and only the code had. A schema change in the derived zone is
invisible to a row count. It surfaced as an `accepted_values` failure in
`make build-bigquery`, four models downstream of the cause.

**And the same day produced the second half of the argument.** The session that
went to fix that zone found it already correct, and could not establish from the
zone whether it had been rebuilt or had never been wrong. Answering that took
GCS object mtimes and a cell-count comparison against the local zone, which is
forensics rather than a check. "This zone is correct now" and "this zone was
never wrong" were the same observation from inside the zone, and so were
"someone rebuilt it" and "someone widened the test".

Separately, ADR-5's own revisit clause anticipated this decision's other half:
"`make spatial` stops finishing in a time anyone will wait for, which is the
point at which the precompute needs to become incremental". It has not; the
measurement below is the reason that half came out smaller than expected.

## What the step was written against, and what was measured

PLAN-5 step 9 was written against ADR-5's figure of about 40 seconds per
700,000 points, linear, on every scheduled build. **That figure is stale.**
Timed by phase on the 506,632-point local zone on 2026-08-05:

| phase | seconds | share |
|---|---|---|
| `build_pip_sample`, the exact point-in-polygon oracle | 18.86 | 78% |
| `build_point_boundary`, boundary assignment | 1.58 | 7% |
| `build_boundaries`, covering cells for 733 polygons | 1.06 | 4% |
| `build_point_h3`, the H3 cells the step names | 0.95 | 4% |
| everything else, reads, writes, the population spread | ~1.6 | 7% |
| **total** | **~24** | |

So the cost is not the H3 precompute and does not grow with the raw zone: the
oracle sample is a fixed 2,000 rows per source tested against every polygon in
a set. Reading last run's `derived_point_h3` back costs 0.85 seconds against
0.95 to recompute it, near enough a wash locally, which is why the point cache
is used only when the changed partitions hold under half the table.

The stamp's value does not rest on any of those numbers. It is a correctness
guard first and an incrementality trigger second.

## Options considered

**A. An explicit version constant someone bumps.** Precise: it fires when the
author decides the output changed and stays quiet through a comment edit, so it
never forces a rebuild that would change nothing. Rejected because it fails
open. Someone will change `RESOLUTIONS` and forget to bump it, and the result is
exactly the failure above: a zone that is wrong, agrees on every row count, and
says it is current.

**B. A hash of the source of every module that decides the zone.** Automatic,
and fires on a comment change, which costs one unnecessary full rebuild. On the
local zone that is 24 seconds.

**C. No stamp; keep comparing row counts and rebuild wholesale every run.**
What was already running. Rejected on the incident: it is the state that
produced the failure, and rebuilding wholesale does not fix it either, because
nothing makes the rebuild happen when only the code moved.

## Decision

**The stamp is option B, a hash of the source.** The costs are asymmetric rather
than close: B's failure mode is a rebuild nobody needed, C's and A's is a zone
nobody can tell is wrong. `ingestion/derived_state.py` owns it and its header
carries this argument, since that is where someone hits it.

- The stamp covers `spatial.py`, `h3_points.py`, `boundaries.py`,
  `population.py`, `geometry.py` and `derived_state.py`, plus the part of the
  dataset registry that decides what the zone contains: each dataset's table,
  kind, grain key and geometry spec. Deliberately not `tier`,
  `stale_after_hours` or `description`, which dbt reads and this does not.
- It is written into `_manifest.json` beside readable fields: the resolutions,
  the membership resolution, the point and polygon table lists, and a digest per
  module. The stamp alone can only say "different"; the fields are what let a
  checker say "this zone was built for resolutions 8, 9 and 10 and the code
  computes 8 and 10", which is the r9 failure named at its cause.
- **The checker is `check_derived.py`, not a new script**, and the verdict is
  `RECODED`, joining `STALE` and `DRIFT` with exit code 4. That file already
  reads the zone rather than the warehouse, already parses the manifest, and
  already grades two verdicts. It is a hard failure like `STALE` and not a
  warning like `DRIFT`, because a drifted zone holds slightly old coordinates
  for rows that are all present, while a recoded zone holds columns or values
  the code cannot produce.
- The stamp is read off the source as bytes and never imported, so the checker
  still answers on a `spatial.py` that does not parse.
- **Each table records `built_at`, and the manifest records `generated_at`.**
  Those are different questions: when this table's bytes were written, and when
  `spatial.py` last ran. Both are needed to make a fix attributable to a run.

**Incrementality is keyed on `ingest_date` partitions.** `_manifest.json`
records, per raw table, the row and file count of every partition, both read out
of the Parquet footers in about 3 ms per table. A run rebuilds what those say
has moved, and a changed stamp rebuilds everything.

- Points are recomputed only for the rows the changed partitions touched, and
  merged over the cache. The deduplication still runs over the whole table, so a
  merged row is the version a full rebuild would have chosen; only the set of
  keys asked about is narrowed. The zone is append-only (ADR-4), so a key absent
  from every changed partition cannot have gained a version.
- Boundaries are all or nothing, and everything computed from them is rebuilt
  when they are.
- The oracle sample reuses the exact answer for any sampled point whose
  coordinates are unchanged, and only when the boundaries did not move.
- A partition that shrank or vanished rebuilds that table whole, since only a
  local `--full-refresh` can produce that and nothing before it survives.

## Consequences

**Buys.** A zone built by code that no longer exists is now a named failure with
its own exit code, ahead of `make build`, instead of an `accepted_values`
failure on another engine a day later. A rebuild is attributable to a run from
the manifest alone. A second `make spatial` on an unchanged zone is 0.3 seconds
against 23 and writes no Parquet at all, which is what makes the step cheap
enough to put in front of a build rather than remember to run. And an
incremental run is *more* reproducible than a full one for
`derived_h3_population`, because reuse is exact where recomputation is not: see
below.

**Costs.** Editing a comment in any stamped module invalidates the whole derived
zone. That is the price of the stamp being automatic and it will surprise
someone. `spatial.py` gained a decision it did not have, and every reuse is a
place a future change can be wrong in a way that looks fine; the mitigations are
`tests/test_derived_state.py` on the decision table, and `check_oracle_agrees`,
which runs on every incremental build and compares two independent
point-in-polygon implementations over the merged row sets. The manifest is
larger and now carries three records rather than one.

**A measurement worth keeping.** `derived_h3_population` is not reproducible bit
for bit. Two full builds over an identical zone differ by 4.55e-13 residents on
a few of the 39,301 cells, because each block group's share is summed into a
per-cell float in an order that comes off a Python set of H3 cell strings, whose
iteration order is salted per process. Totals are exact and every other derived
table is byte-stable. So incrementality is verified by comparing rows, not
Parquet bytes: making that sum order-independent is a separate change with its
own verification and is not folded in here.

## Revisit if

- `make spatial` grows a phase that dominates the oracle sample, at which point
  the table above is out of date and the thing to make incremental is whatever
  replaced it.
- Someone wants a byte comparison as the incrementality check, which needs the
  population sum made order-independent first.
- The stamp starts firing often enough that people reach for `DERIVED_CHECK=0`.
  That is the signal that the module list is too wide, not that the stamp is
  wrong; narrowing it to the modules that write rows is the cheaper fix than
  removing it.
