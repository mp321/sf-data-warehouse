"""Boundaries: covering cells, exact membership, and the oracle that checks it.

Four of the six derived tables, and the whole of ADR-6's scheme:

  derived_boundary        one row per polygon: name, area, and its GeoJSON.
  derived_polygon_h3      one row per (boundary, resolution, covering cell),
                          flagged interior, primary and allocation. The bridge.
  derived_point_boundary  one row per (point, boundary set): the boundary the
                          point is exactly inside.
  derived_pip_sample      exact point-in-polygon answers for a deterministic
                          sample of points. The test oracle, not a mart input.

**The three containment modes** are the whole trick, and are worth
understanding before changing anything here. For one polygon at one
resolution, h3 gives three different covering sets:

  contain='overlap'   every cell touching the polygon. The covering set.
  contain='full'      every cell entirely inside it. Any point in one of
                      these is inside the polygon, with no test needed. This
                      is ADR-2's "fully interior" set, computed by the H3
                      library rather than by us, which is what retires the
                      "highest-risk code in the project" worry.
  contain='center'    every cell whose centre is inside it. Because polygons
                      in a boundary set do not overlap, these sets are
                      disjoint across boundaries, so this assigns each cell
                      to exactly one boundary. That is `is_primary`, and it
                      is what lets a query join on equality and get one row.

A point in a primary-but-not-interior cell can be in a different boundary
than its cell. That error is real, bounded by cell size, and measured rather
than assumed: `derived_pip_sample` plus the singular test in
dbt/tests/assert_h3_membership_matches_exact_pip.sql is how we know what it
actually is at each resolution.

The exact half of that, `build_point_boundary`, is the reason this project
owns a point-in-polygon implementation at all. It runs here, once, so that
nothing at query time knows what a polygon is (ADR-6). `geometry.py` is the
implementation and `tests/test_geometry.py` is what checks it directly.
"""

import hashlib
import json
from pathlib import Path

import h3
import numpy as np

import geometry as geo
import raw_zone
from dataset_registry import polygon_datasets
from h3_points import RESOLUTIONS, dedup_sql

# The resolution boundary membership is decided at. Finest wins: membership
# error scales with cell size, and at r10 the whole 41-neighbourhood bridge
# is still only tens of thousands of rows.
MEMBERSHIP_RESOLUTION = 10


# ---------------------------------------------------------------------------
# Reading the raw zone
# ---------------------------------------------------------------------------


def read_boundaries(con, name: str, cfg: dict, root: Path | str | None) -> list[dict]:
    """One dict per polygon row, with the GeoJSON already parsed."""
    spec = cfg["geometry"]
    key = cfg["grain_key"]
    columns = [
        f"{spec['boundary_id']} as boundary_id",
        f"{spec['boundary_name']} as boundary_name",
        f"{spec['geojson']} as geojson_text",
    ]
    for measure in ("population", "housing_units"):
        columns.append(f"{spec[measure]} as {measure}" if measure in spec else f"null as {measure}")

    inner = (
        f"select {key} as row_key, {', '.join(columns)}, _socrata_updated_at, _ingested_at "
        f"from {raw_zone.read_sql(cfg['table'], root)}"
    )
    rows = con.execute(
        "select boundary_id, boundary_name, geojson_text, population, housing_units "
        f"from ({dedup_sql(inner, 'row_key')}) where boundary_id is not null"
    ).fetchall()

    boundaries = []
    for boundary_id, boundary_name, geojson_text, population, housing_units in rows:
        try:
            parsed = json.loads(geojson_text) if geojson_text else None
        except (TypeError, ValueError):
            parsed = None
        boundaries.append(
            {
                "boundary_set": spec["boundary_set"],
                "boundary_id": str(boundary_id),
                "boundary_name": str(boundary_name) if boundary_name is not None else None,
                "geometry": parsed,
                "population": _as_int(population),
                "housing_units": _as_int(housing_units),
            }
        )
    return boundaries


def _as_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


def build_boundaries(con, root: Path | str | None) -> tuple[list[dict], list[dict], dict]:
    """derived_boundary and derived_polygon_h3, for every polygon dataset."""
    boundary_rows: list[dict] = []
    bridge_rows: list[dict] = []
    stats: dict = {}

    for name, cfg in polygon_datasets().items():
        if not raw_zone.has_data(cfg["table"], root):
            print(f"[{name}] no Parquet in the raw zone; skipped")
            continue

        boundaries = read_boundaries(con, name, cfg, root)
        boundary_set = cfg["geometry"]["boundary_set"]
        per_resolution = {resolution: {"cells": 0, "interior": 0} for resolution in RESOLUTIONS}

        for boundary in boundaries:
            shape = _to_h3shape(boundary["geometry"])
            boundary_rows.append(
                {
                    "boundary_set": boundary_set,
                    "boundary_id": boundary["boundary_id"],
                    "boundary_name": boundary["boundary_name"],
                    "area_sq_km": geo.geometry_area_sq_km(boundary["geometry"]),
                    "population": boundary["population"],
                    "housing_units": boundary["housing_units"],
                    "geojson": json.dumps(boundary["geometry"]) if boundary["geometry"] else None,
                }
            )
            if shape is None:
                continue

            for resolution in RESOLUTIONS:
                # set(), not the list h3 returns. For a MultiPolygon whose
                # parts share a cell, the same cell comes back once per part,
                # and duplicate bridge rows make every downstream join fan out
                # and over-count. Found by a uniqueness check on the bridge,
                # not by anything failing.
                covering = set(
                    h3.h3shape_to_cells_experimental(shape, resolution, contain="overlap")
                )
                interior = set(h3.h3shape_to_cells_experimental(shape, resolution, contain="full"))
                primary = set(h3.h3shape_to_cells_experimental(shape, resolution, contain="center"))

                if not primary:
                    # A polygon too small to contain any cell centre owns no
                    # cell, so points in it would fall through to whichever
                    # neighbour does. Rare at r10 and common at r8. Give it
                    # the cell containing a point known to be inside it; that
                    # cell may already be another boundary's primary, which
                    # is resolved deterministically below.
                    point = geo.geometry_representative_point(boundary["geometry"])
                    if point is not None:
                        primary = {h3.latlng_to_cell(point[1], point[0], resolution)}
                        covering = covering | primary

                per_resolution[resolution]["cells"] += len(covering)
                per_resolution[resolution]["interior"] += len(interior)
                for cell in covering:
                    bridge_rows.append(
                        {
                            "boundary_set": boundary_set,
                            "boundary_id": boundary["boundary_id"],
                            "resolution": resolution,
                            "h3_cell": h3.str_to_int(cell),
                            "is_interior": cell in interior,
                            "is_primary": cell in primary,
                            # is_primary gets stripped from all but one
                            # boundary per cell below; this keeps the
                            # unstripped answer, and it is what conserves
                            # population. See population.build_h3_population.
                            "is_allocation_cell": cell in primary,
                        }
                    )

        stats[boundary_set] = {
            "boundaries": len(boundaries),
            "by_resolution": {
                str(resolution): {
                    "covering_cells": values["cells"],
                    "interior_cells": values["interior"],
                    "boundary_cell_pct": (
                        0.0
                        if values["cells"] == 0
                        else 100.0 * (values["cells"] - values["interior"]) / values["cells"]
                    ),
                }
                for resolution, values in per_resolution.items()
            },
        }
        summary = ", ".join(
            f"r{resolution}: {values['cells']} cells "
            f"({stats[boundary_set]['by_resolution'][str(resolution)]['boundary_cell_pct']:.1f}% "
            "on a boundary)"
            for resolution, values in per_resolution.items()
        )
        print(f"[{name}] {len(boundaries)} boundaries. {summary}")

    _resolve_primary_collisions(bridge_rows)
    return boundary_rows, bridge_rows, stats


def _to_h3shape(geometry: dict | None):
    """GeoJSON to an h3 shape, or None if there is nothing to cover."""
    if not geometry or not geometry.get("coordinates"):
        return None
    try:
        return h3.geo_to_h3shape(geometry)
    except (ValueError, TypeError, KeyError):
        return None


def _resolve_primary_collisions(bridge_rows: list[dict]) -> None:
    """Guarantee at most one primary boundary per (set, resolution, cell).

    Cell centres fall in at most one polygon, so `contain='center'` is
    naturally disjoint and this is normally a no-op. It is not guaranteed
    though: the fallback above hands a sub-cell-sized polygon a cell that may
    already belong to a neighbour, and a boundary set whose polygons genuinely
    overlap would collide everywhere. Left unresolved, a query joining on the
    bridge would silently fan out and double-count, which is the worst failure
    mode available here. Losing ties by boundary_id keeps it deterministic and
    therefore comparable across engines and runs.
    """
    winners: dict[tuple, str] = {}
    for row in bridge_rows:
        if not row["is_primary"]:
            continue
        cell_key = (row["boundary_set"], row["resolution"], row["h3_cell"])
        current = winners.get(cell_key)
        if current is None or row["boundary_id"] < current:
            winners[cell_key] = row["boundary_id"]
    for row in bridge_rows:
        if row["is_primary"]:
            cell_key = (row["boundary_set"], row["resolution"], row["h3_cell"])
            row["is_primary"] = winners[cell_key] == row["boundary_id"]


# ---------------------------------------------------------------------------
# Assigning points to boundaries
# ---------------------------------------------------------------------------


def build_point_boundary(
    point_rows: list[dict], boundary_rows: list[dict], bridge_rows: list[dict]
) -> list[dict]:
    """derived_point_boundary: the exact boundary each point falls in.

    This is ADR-2's decision, implemented where ADR-2 did not put it. Two
    cases, and the split is what makes it both exact and affordable:

      interior_cell      the point's r10 cell lies entirely inside one
                         boundary, so the point does too. No test needed, and
                         this is roughly four points in five.
      exact_refinement   the cell straddles a boundary. Run a real
                         point-in-polygon test, but only against the handful
                         of boundaries whose covering set includes that cell,
                         which is two or three rather than all 41. That is
                         ADR-2's "coarse filter" doing the job it was chosen
                         for.

    ADR-2 put the refinement in a dbt macro dispatched per engine, at query
    time. Running it here instead is what removes the last piece of
    engine-specific spatial SQL from the project, and it is why ADR-6
    supersedes ADR-2 rather than merely implementing it. A query joins on an
    integer and gets an exact answer; nothing at query time knows what a
    polygon is.

    The cost is that a boundary change means re-running `make spatial` rather
    than just rebuilding dbt. That is a local, credential-free, one-minute
    step, and unlike ADR-2's option B it does not mean re-ingesting.
    """
    geometries = {
        (row["boundary_set"], row["boundary_id"]): geo.PreparedGeometry(json.loads(row["geojson"]))
        for row in boundary_rows
        if row["geojson"]
    }
    boundary_sets = sorted({boundary_set for boundary_set, _ in geometries})

    interior: dict[tuple, str] = {}
    candidates: dict[tuple, list[str]] = {}
    for row in bridge_rows:
        if row["resolution"] != MEMBERSHIP_RESOLUTION:
            continue
        cell_key = (row["boundary_set"], row["h3_cell"])
        candidates.setdefault(cell_key, []).append(row["boundary_id"])
        if row["is_interior"]:
            # A cell entirely inside two boundaries would mean the boundaries
            # overlap. Last one wins rather than erroring, because the bridge
            # is rebuilt from upstream shapes we do not control.
            interior[cell_key] = row["boundary_id"]

    cell_column = f"h3_r{MEMBERSHIP_RESOLUTION}"
    assignments: list[dict] = []
    for boundary_set in boundary_sets:
        pending: dict[str, list[dict]] = {}
        for row in point_rows:
            cell = row[cell_column]
            if cell is None:
                continue
            cell_key = (boundary_set, cell)
            resolved = interior.get(cell_key)
            if resolved is not None:
                assignments.append(_assignment(row, boundary_set, resolved, "interior_cell"))
                continue
            for boundary_id in candidates.get(cell_key, ()):
                pending.setdefault(boundary_id, []).append(row)

        # Refine one candidate boundary at a time so the whole batch of points
        # for that boundary goes through numpy in a single pass. Point by
        # point this same work took minutes; batched it takes seconds.
        decided: set[tuple] = set()
        for boundary_id, rows in pending.items():
            prepared = geometries.get((boundary_set, boundary_id))
            if prepared is None:
                continue
            longitudes = np.fromiter(
                (row["longitude"] for row in rows), dtype=np.float64, count=len(rows)
            )
            latitudes = np.fromiter(
                (row["latitude"] for row in rows), dtype=np.float64, count=len(rows)
            )
            for row, is_inside in zip(rows, prepared.contains(longitudes, latitudes), strict=True):
                identity = (row["source_table"], row["row_key"])
                if is_inside and identity not in decided:
                    decided.add(identity)
                    assignments.append(
                        _assignment(row, boundary_set, boundary_id, "exact_refinement")
                    )

        assigned = sum(1 for row in assignments if row["boundary_set"] == boundary_set)
        print(f"  {boundary_set}: {assigned} points assigned")
    return assignments


def _assignment(row: dict, boundary_set: str, boundary_id: str, method: str) -> dict:
    return {
        "source_table": row["source_table"],
        "row_key": row["row_key"],
        "boundary_set": boundary_set,
        "boundary_id": boundary_id,
        "assignment_method": method,
    }


# ---------------------------------------------------------------------------
# The test oracle
# ---------------------------------------------------------------------------


def build_pip_sample(
    point_rows: list[dict],
    boundary_rows: list[dict],
    sample_size: int,
    cached: list[dict] | None = None,
) -> list[dict]:
    """derived_pip_sample: exact point-in-polygon answers for sampled points.

    The H3 bridge assigns a point to whichever boundary owns its cell, which
    is not always the boundary the point is actually in. This table is how
    that error gets measured instead of assumed: for a deterministic sample of
    points, it records the answer an exact geometry test gives, and a dbt test
    compares the two and fails if they disagree too often.

    The sample is chosen by hashing the row key rather than by `order by
    random()`, so it is the same sample on every run and on both engines. A
    sample that moved would turn a real regression into noise and a flaky test
    into a shrug.

    **This is the expensive half of `make spatial`, and the reason it is worth
    caching.** Measured on the 506,632-point local zone on 2026-08-05: 18.86
    seconds of a 24 second run, against 0.95 for the H3 cells that PLAN-5 step 9
    was written to make incremental. It does not scale with the raw zone at all,
    because the sample is a fixed 2,000 rows per source; it scales with the
    number of polygons each sampled point is tested against, one scalar
    crossing-number test per polygon until one contains it.

    `cached` is the previous run's rows. An entry is reused when the sampled
    point's coordinates are unchanged, which makes the answer identical rather
    than merely close: `exact_boundary_id` is a pure function of the coordinate
    and the boundary geometries. **The caller must pass `cached` only when the
    boundaries were not rebuilt**, since a moved polygon changes the answer for
    a point that did not move, and nothing here can see that.
    """
    geometries = [
        (row["boundary_set"], row["boundary_id"], json.loads(row["geojson"]))
        for row in boundary_rows
        if row["geojson"]
    ]
    boundary_sets = sorted({boundary_set for boundary_set, _, _ in geometries})
    previous = {
        (row["source_table"], row["row_key"], row["boundary_set"]): row for row in cached or ()
    }

    usable_by_source: dict[str, list[dict]] = {}
    for row in point_rows:
        if row["coordinate_status"] == "ok":
            usable_by_source.setdefault(row["source_table"], []).append(row)

    sample: list[dict] = []
    reused = 0
    for source_table, rows in sorted(usable_by_source.items()):
        chosen = sorted(rows, key=lambda row: _stable_hash(row["row_key"]))[:sample_size]
        for row in chosen:
            for boundary_set in boundary_sets:
                before = previous.get((source_table, row["row_key"], boundary_set))
                if (
                    before is not None
                    and before["latitude"] == row["latitude"]
                    and before["longitude"] == row["longitude"]
                ):
                    exact = before["exact_boundary_id"]
                    reused += 1
                else:
                    exact = next(
                        (
                            boundary_id
                            for candidate_set, boundary_id, geometry in geometries
                            if candidate_set == boundary_set
                            and geo.point_in_geometry(row["longitude"], row["latitude"], geometry)
                        ),
                        None,
                    )
                sample.append(
                    {
                        "source_table": source_table,
                        "row_key": row["row_key"],
                        "boundary_set": boundary_set,
                        "latitude": row["latitude"],
                        "longitude": row["longitude"],
                        "exact_boundary_id": exact,
                        **{
                            f"h3_r{resolution}": row[f"h3_r{resolution}"]
                            for resolution in RESOLUTIONS
                        },
                    }
                )
    if cached is not None:
        print(f"  derived_pip_sample: {reused} of {len(sample)} exact answers reused")
    return sample


def _stable_hash(value: str) -> str:
    """Deterministic across processes, unlike hash(), which is salted per run."""
    return hashlib.blake2b(value.encode("utf-8"), digest_size=8).hexdigest()


def check_oracle_agrees(sample_rows: list[dict], assignment_rows: list[dict]) -> None:
    """Assert the scalar oracle and the vectorised assignment agree.

    `derived_pip_sample` is built by the scalar `point_in_geometry`, and
    `derived_point_boundary` by the vectorised `PreparedGeometry.contains`.
    They implement the same crossing-number test twice, so this catches a
    divergence between them and not a shared misunderstanding of geometry. It
    is cheap and it runs on every build, which is the only reason the two
    implementations are allowed to coexist.

    `tests/test_geometry.py` is the other half and answers the question this
    one cannot: whether either implementation is right, rather than whether
    they match.

    Disagreement is a hard failure, not a warning: if these two disagree, one
    of them is also disagreeing with the H3 test that is about to be run
    against them both, and that test would report a resolution problem that
    is really a code problem.
    """
    assigned = {
        (row["source_table"], row["row_key"], row["boundary_set"]): row["boundary_id"]
        for row in assignment_rows
    }
    mismatches = [
        row
        for row in sample_rows
        if assigned.get((row["source_table"], row["row_key"], row["boundary_set"]))
        != row["exact_boundary_id"]
    ]
    if mismatches:
        example = mismatches[0]
        identity = (example["source_table"], example["row_key"], example["boundary_set"])
        raise RuntimeError(
            f"{len(mismatches)} of {len(sample_rows)} sampled points disagree between the "
            f"scalar and vectorised point-in-polygon implementations. First: "
            f"{example['source_table']}/{example['row_key']} in {example['boundary_set']}, "
            f"oracle says {example['exact_boundary_id']!r}, assignment says "
            f"{assigned.get(identity)!r}"
        )
    print(f"  oracle check: {len(sample_rows)} sampled points agree with the assignment")
