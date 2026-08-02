---
status: active
date: 2026-07-31
related: [adr-1-warehouse-targets, adr-2-spatial-strategy, adr-6-polygon-membership]
---

# ADR-5. H3 cells are computed in Python, in a derived zone, as BIGINTs

## Context

ADR-2 chose H3 as the spatial strategy and said every point-bearing row
carries a precomputed cell id. It did not say what computes it, and the
obvious answer turns out not to be available.

**The forcing constraint: BigQuery has no H3 support of any kind.** DuckDB's
h3 community extension works well and was verified in this session
(`install h3 from community` then `h3_latlng_to_cell(37.7749, -122.4194, 9)`
returns `617700169957507071`). BigQuery has no built-in equivalent and no
extension mechanism; the usual answer there is Carto's public UDF dataset,
which is a third-party dependency in one region.

ADR-1 requires every model to compile on both targets. So an H3 function call
inside a dbt model cannot exist: it would compile on DuckDB and fail on
BigQuery, and no dispatch macro helps because there is nothing to dispatch to.

Two smaller constraints. The DuckDB extension downloads on first use, which
breaks the offline-clone property ADR-1 bought and ADR-2 explicitly protected.
And ADR-2 rejected computing cells during ingestion (its option B) because it
puts business logic in ingestion and makes a bug fix require re-ingesting.

## Options considered

**A. DuckDB h3 extension, called from dbt models.** Least new machinery, and
the cells would be derived where every other transformation lives. Against:
the models then only build on DuckDB, which ends the cross-engine guarantee
rather than bending it; the extension download breaks offline builds; and the
BigQuery target would have to be dropped or fed from somewhere else, which is
the same as this decision with extra steps.

**B. Python h3 during ingestion, cells written into the raw zone.** Simple and
one fewer step. Against: exactly what ADR-2 rejected. Raw stops being raw, the
all-STRING contract gains an exception, and re-resolving a cell means
re-fetching from Socrata rather than recomputing locally. The raw zone is
append-only, so a bad cell is permanent.

**C. Python h3 in a separate precompute step writing a derived zone.**
Against: a fourth pipeline step that is easy to forget, and one that fails
quietly when skipped, because the spatial models build empty rather than
erroring. A second Parquet zone to explain. And the H3 library moves into the
critical path of the build rather than of ingestion, so an h3 major version
bump breaks `make spatial` for everyone at once.

## Decision

Option C.

- `ingestion/spatial.py` reads the Parquet raw zone, computes H3 cells with
  the Python `h3` package, and writes `data/derived/`. `ingestion/load.py`
  materialises that into a `derived_spatial` schema on both engines.
- **Cells are stored as BIGINT, not as hex strings.** H3 indexes always have
  bit 63 clear, so every cell fits in a signed 64-bit integer on both engines,
  and an integer join is what the whole scheme exists to produce.
- **Resolutions 8, 9 and 10 on every point row.** They answer different
  questions: r10 (about 65 m across) is fine enough for boundary membership,
  r8 (about 460 m) is coarse enough to draw a readable city map, and r9 is
  what ADR-2 guessed at and is kept so that guess stays checkable.
- The derived zone is typed, unlike the raw zone. Nothing in it was received
  from an API, so the all-STRING contract buys nothing and would mean storing
  a computed number as text.
- `h3` is pinned to major version 4 in `requirements.txt`.

The pipeline becomes five steps: ingest, spatial, load, build, publish.

## Consequences

**Buys.** The two engines cannot disagree about which cell a point is in,
because neither of them computes it: they read the same integers from the same
Parquet. That is a stronger guarantee than ADR-1's usual "the same SQL
compiles on both", which only means the dialects match. Offline builds survive,
since no extension is downloaded. Raw stays raw, so fixing a cell bug is
`make spatial`, about a minute, with no network and no re-ingest. And the
derived zone is deletable at any time, which makes the highest-risk code in
the project also the cheapest to re-run.

**Costs.** A fourth step that fails quietly when skipped: the spatial models
build with zero rows and the marts come out empty rather than erroring, which
is why `make all` and `make rebuild` sequence it for you and why `load.py`
prints a warning naming the step. Cells are computed in Python at about 40
seconds for 700,000 points, which is fine now and grows linearly; a full 311
backfill is 8.8 million rows and would take minutes. The derived zone is a
second thing to keep in step with the raw zone, and nothing enforces that it
is current: a stale `data/derived` is silently wrong rather than obviously
missing.

**Lock-in.** Changing the resolution list invalidates every stored cell and
requires reprocessing every point-bearing row, so it behaves like a schema
change rather than a tuning knob. Storing cells as BIGINT means anything
reading them needs `h3.int_to_str` to get a recognisable `89283082803ffff`
back, which is a real ergonomic cost when debugging. And the `h3` Python
package is now a build dependency rather than an ingestion one: its v3 to v4
release renamed every function in the library, so a v5 would be a rewrite of
`spatial.py` rather than an upgrade.

## Revisit if

- BigQuery ships native H3 support, or Carto's UDFs become a first-party
  offering. That removes the forcing constraint entirely and option A becomes
  available, though it would still cost the offline-clone property.
- `make spatial` stops finishing in a time anyone will wait for, which is the
  point at which the precompute needs to become incremental rather than a
  full rebuild.
- Anyone needs a resolution finer than 10 for membership, at which point the
  bridge table size in ADR-6 has to be re-measured before agreeing.
