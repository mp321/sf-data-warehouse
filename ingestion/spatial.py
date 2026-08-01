"""Precompute H3 cells for points and boundaries. The geometry step (ADR-5, ADR-6).

This is the step that makes "count 311 cases inside the Mission" an integer
join. It reads the Parquet raw zone, computes H3 cells in Python, and writes
the derived zone. It talks to no API, needs no credentials, and can be
re-run at any time: everything it produces is a pure function of the raw zone
and this file.

    make ingest    Socrata, TIGERweb -> raw zone          network, no creds
    make spatial   raw zone          -> derived zone      no network, no creds  <- here
    make load      both zones        -> warehouse         no network, no creds
    make build     dbt run + test                         no network, no creds

Both zones are `data/raw` and `data/derived` by default and `gs://` prefixes
when `RAW_ZONE_URI` and `DERIVED_ZONE_URI` are set (ADR-9). This reads and
writes whichever is configured, and writes to exactly one of them: a remote run
leaves `data/derived` alone rather than keeping a second copy. The two roots are
resolved independently, so reading a remote raw zone and writing a local derived
zone is a legal arrangement; `check_derived.py` is what notices when the pair
has drifted apart, and it compares whatever two roots it is pointed at.

**Why Python and not SQL.** ADR-5 has the argument in full. Briefly: DuckDB's
h3 community extension works well, and BigQuery has no H3 support of any kind,
so an H3 expression in a dbt model cannot compile on both targets, which ADR-1
requires of every model. Computing the cells once here and letting both
engines read them as ordinary BIGINTs is the only arrangement where the two
warehouses are guaranteed to agree, because they are reading the same numbers
rather than each deriving them.

**What comes out.** Five tables, all replaced wholesale on every run:

  derived_point_h3        one row per point-bearing raw row: its coordinates,
                          whether they are usable, and its cell at r8/r9/r10.
  derived_boundary        one row per polygon: name, area, and its GeoJSON.
  derived_polygon_h3      one row per (boundary, resolution, covering cell),
                          flagged interior and primary. The bridge table.
  derived_h3_population   one row per (resolution, cell): population and
                          housing units interpolated from block groups.
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

Usage:
    python ingestion/spatial.py --all
    python ingestion/spatial.py --all --sample-size 5000
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import h3
import numpy as np
import pyarrow as pa

import derived_zone
import geometry as geo
import raw_zone
import remote
from datasets import DATASETS, SF_BOUNDING_BOX, point_datasets, polygon_datasets

# The resolutions carried on every point row. 8 is roughly a 460 m hexagon,
# 9 roughly 175 m, 10 roughly 65 m across. Three rather than one because
# they answer different questions: r10 is fine enough for boundary
# membership, r8 is coarse enough to aggregate into a readable map, and r9
# is what ADR-2 originally guessed at and is kept so that guess stays
# checkable. Changing this list invalidates every stored cell (ADR-5).
RESOLUTIONS = (8, 9, 10)

# The resolution boundary membership is decided at. Finest wins: membership
# error scales with cell size, and at r10 the whole 41-neighbourhood bridge
# is still only tens of thousands of rows.
MEMBERSHIP_RESOLUTION = 10

# Coordinates that are on Earth but not plausibly a San Francisco address.
# Kept separate from impossible ones because they mean different things: a
# business registered in San Francisco with its location in Atlanta is
# correct data that this warehouse cannot map, while a latitude of 5999163
# is a Web Mercator metre that leaked into a degree column.
COORDINATE_STATUSES = ("ok", "missing", "unparseable", "impossible", "out_of_bounds")


# ---------------------------------------------------------------------------
# Reading the raw zone
# ---------------------------------------------------------------------------


def _dedup_sql(inner: str, key: str) -> str:
    """Latest version of each row, matching what the staging models do.

    The raw zone is append-only, so a re-ingested row exists more than once.
    This has to agree with the staging models' QUALIFY exactly, or a point
    gets an H3 cell computed from one version of its coordinates and joins to
    a staging row carrying another.
    """
    return f"""
        select * from ({inner})
        qualify row_number() over (
            partition by {key}
            order by
                try_cast(_socrata_updated_at as timestamp) desc,
                try_cast(_ingested_at as timestamp) desc
        ) = 1
    """


def raw_input_state(con, root: Path | str | None) -> dict:
    """What the raw zone held for every dataset this step reads.

    Per raw table: the deduplicated row count and the newest watermark. The
    count goes through `_dedup_sql`, so it is the number of rows a staging
    model has, not the number of files' worth of appends behind them, and a
    later comparison against it means "does the derived zone still cover every
    row that exists" rather than "has anything been appended".

    Recorded for polygon datasets too. A new neighbourhood boundary does not
    leave a point without geography, so nothing downstream fails loudly, but
    every assignment in the zone was computed against the old boundaries and
    is silently one version behind. That is worth reporting.
    """
    state: dict = {}
    for cfg in {**point_datasets(), **polygon_datasets()}.values():
        table = cfg["table"]
        if not raw_zone.has_data(table, root):
            continue
        inner = (
            f"select {cfg['grain_key']} as row_key, _socrata_updated_at, _ingested_at "
            f"from {raw_zone.read_sql(table, root)}"
        )
        rows = con.execute(f"select count(*) from ({_dedup_sql(inner, 'row_key')})").fetchone()[0]
        state[table] = {
            "rows": rows,
            "watermark": raw_zone.read_watermark(table, root),
        }
    return state


def _point_expressions(cfg: dict) -> tuple[str, str]:
    """SQL for the latitude and longitude columns of one point dataset.

    Two shapes upstream. DataSF publishes flat `lat`/`long` columns on some
    datasets and a nested GeoJSON point on others; `normalize_record` stored
    the latter as JSON text. GeoJSON orders coordinates [longitude, latitude],
    so index 0 is the longitude, which is the reverse of how anyone says it.
    """
    spec = cfg["geometry"]
    if "geojson_point" in spec:
        column = spec["geojson_point"]
        return (
            f"json_extract_string({column}, '$.coordinates[1]')",
            f"json_extract_string({column}, '$.coordinates[0]')",
        )
    return (spec["latitude"], spec["longitude"])


def read_points(con, name: str, cfg: dict, root: Path | str | None) -> list[tuple]:
    """(row_key, latitude_text, longitude_text) for one point dataset."""
    latitude, longitude = _point_expressions(cfg)
    key = cfg["grain_key"]
    inner = (
        f"select {key} as row_key, {latitude} as lat_text, {longitude} as lon_text, "
        f"_socrata_updated_at, _ingested_at from {raw_zone.read_sql(cfg['table'], root)}"
    )
    return con.execute(
        f"select row_key, lat_text, lon_text from ({_dedup_sql(inner, 'row_key')})"
    ).fetchall()


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
        f"from ({_dedup_sql(inner, 'row_key')}) where boundary_id is not null"
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
# Points
# ---------------------------------------------------------------------------


def classify_coordinate(lat_text, lon_text) -> tuple[str, float | None, float | None]:
    """Parse and judge one coordinate pair. Returns (status, latitude, longitude).

    The four failure statuses are not decoration. `missing` and
    `out_of_bounds` are properties of the world (a case with no location, a
    business whose registered address is in another state) and are expected
    to be nonzero forever. `unparseable` and `impossible` are properties of
    the pipeline and should be zero; if either starts moving, something
    upstream changed shape. Collapsing them into one "bad" flag would hide
    that distinction, which is the only reason the drop rate is worth
    reporting at all.
    """
    if lat_text is None or lon_text is None or lat_text == "" or lon_text == "":
        return ("missing", None, None)
    try:
        latitude = float(lat_text)
        longitude = float(lon_text)
    except (TypeError, ValueError):
        return ("unparseable", None, None)

    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return ("impossible", None, None)
    if latitude == 0.0 and longitude == 0.0:
        # Null island. A real coordinate, never a San Francisco one, and the
        # standard shape of "the geocoder returned nothing and something
        # coalesced it to zero".
        return ("impossible", None, None)

    box = SF_BOUNDING_BOX
    if not (
        box["min_latitude"] <= latitude <= box["max_latitude"]
        and box["min_longitude"] <= longitude <= box["max_longitude"]
    ):
        return ("out_of_bounds", latitude, longitude)
    return ("ok", latitude, longitude)


def build_point_h3(con, root: Path | str | None) -> tuple[list[dict], dict]:
    """derived_point_h3, plus a per-source coordinate quality summary."""
    rows: list[dict] = []
    stats: dict = {}

    for name, cfg in point_datasets().items():
        if not raw_zone.has_data(cfg["table"], root):
            print(f"[{name}] no Parquet in the raw zone; skipped")
            continue

        counts = dict.fromkeys(COORDINATE_STATUSES, 0)
        for row_key, lat_text, lon_text in read_points(con, name, cfg, root):
            status, latitude, longitude = classify_coordinate(lat_text, lon_text)
            counts[status] += 1
            cells = (
                {
                    f"h3_r{resolution}": h3.str_to_int(
                        h3.latlng_to_cell(latitude, longitude, resolution)
                    )
                    for resolution in RESOLUTIONS
                }
                # Cells are computed for out_of_bounds points too: the
                # coordinate is real, so the cell is real, and it is only the
                # San Francisco boundary sets that will not match it. Not
                # computing them would make "outside the city" and "no
                # location at all" look identical downstream.
                if status in ("ok", "out_of_bounds")
                else dict.fromkeys((f"h3_r{r}" for r in RESOLUTIONS), None)
            )
            rows.append(
                {
                    "source_table": cfg["table"],
                    "row_key": str(row_key),
                    "latitude": latitude,
                    "longitude": longitude,
                    "coordinate_status": status,
                    "is_usable_coordinate": status == "ok",
                    **cells,
                }
            )

        total = sum(counts.values())
        stats[cfg["table"]] = {"total": total, **counts}
        usable = counts["ok"]
        rate = 0.0 if total == 0 else 100.0 * (total - usable) / total
        print(
            f"[{name}] {total} rows, {usable} usable ({rate:.2f}% dropped): "
            + ", ".join(f"{status}={counts[status]}" for status in COORDINATE_STATUSES[1:])
        )
    return rows, stats


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
                            # population. See build_h3_population.
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
# Population, interpolated onto cells
# ---------------------------------------------------------------------------


def build_h3_population(boundary_rows: list[dict], bridge_rows: list[dict]) -> list[dict]:
    """derived_h3_population: block group population spread over H3 cells.

    Areal interpolation under the standard assumption that population is
    uniform within a block group. Each block group's centre-contained cells
    partition its area, so its population is divided equally among them. That
    is exactly as wrong as the uniformity assumption and no more: a block
    group covering a park and a tower block will smear residents across the
    park.

    Centre-contained rather than covering cells, because centre-contained
    cells tile without overlapping. Interpolating over covering cells would
    count the population of every edge block group once per neighbour it
    touches, and the city total would come out well above 873,965.

    It reads `is_allocation_cell` rather than `is_primary`, and the difference
    is the whole reason both columns exist. `is_primary` is stripped down to
    one boundary per cell so that membership joins cannot fan out. At r8 a
    single cell covers dozens of block groups, so stripping it discards all
    but one of them, and with them their population: measured at 221,088 of
    873,965 residents surviving to r8 before this was split out. Conservation
    is checked below rather than trusted.
    """
    measures = {
        row["boundary_id"]: (row["population"], row["housing_units"])
        for row in boundary_rows
        if row["boundary_set"] == "census_block_group"
    }
    if not measures:
        return []

    cells_by_boundary: dict[tuple, list[int]] = {}
    for row in bridge_rows:
        if row["boundary_set"] != "census_block_group" or not row["is_allocation_cell"]:
            continue
        cells_by_boundary.setdefault((row["boundary_id"], row["resolution"]), []).append(
            row["h3_cell"]
        )

    totals: dict[tuple, list[float]] = {}
    for (boundary_id, resolution), cells in cells_by_boundary.items():
        population, housing_units = measures.get(boundary_id, (None, None))
        share_population = (population or 0) / len(cells)
        share_housing = (housing_units or 0) / len(cells)
        for cell in cells:
            bucket = totals.setdefault((resolution, cell), [0.0, 0.0])
            bucket[0] += share_population
            bucket[1] += share_housing

    expected = sum(population or 0 for population, _ in measures.values())
    for resolution in RESOLUTIONS:
        allocated = sum(
            values[0]
            for (cell_resolution, _), values in totals.items()
            if cell_resolution == resolution
        )
        if abs(allocated - expected) > 1.0:
            # Loud rather than logged. Every rate in every mart divides by
            # this, so losing residents here understates nothing and
            # overstates every per-capita number in the warehouse, silently
            # and plausibly.
            raise RuntimeError(
                f"population not conserved at r{resolution}: allocated {allocated:.0f} "
                f"of {expected} residents. A block group is contributing no cells."
            )

    return [
        {
            "resolution": resolution,
            "h3_cell": cell,
            "population": values[0],
            "housing_units": values[1],
        }
        for (resolution, cell), values in sorted(totals.items())
    ]


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
    point_rows: list[dict], boundary_rows: list[dict], sample_size: int
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
    """
    geometries = [
        (row["boundary_set"], row["boundary_id"], json.loads(row["geojson"]))
        for row in boundary_rows
        if row["geojson"]
    ]
    boundary_sets = sorted({boundary_set for boundary_set, _, _ in geometries})

    usable_by_source: dict[str, list[dict]] = {}
    for row in point_rows:
        if row["coordinate_status"] == "ok":
            usable_by_source.setdefault(row["source_table"], []).append(row)

    sample: list[dict] = []
    for source_table, rows in sorted(usable_by_source.items()):
        chosen = sorted(rows, key=lambda row: _stable_hash(row["row_key"]))[:sample_size]
        for row in chosen:
            for boundary_set in boundary_sets:
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
    return sample


def _stable_hash(value: str) -> str:
    """Deterministic across processes, unlike hash(), which is salted per run."""
    return hashlib.blake2b(value.encode("utf-8"), digest_size=8).hexdigest()


def _check_oracle_agrees(sample_rows: list[dict], assignment_rows: list[dict]) -> None:
    """Assert the scalar oracle and the vectorised assignment agree.

    `derived_pip_sample` is built by the scalar `point_in_geometry`, and
    `derived_point_boundary` by the vectorised `PreparedGeometry.contains`.
    They implement the same crossing-number test twice, so this catches a
    divergence between them and not a shared misunderstanding of geometry. It
    is cheap and it runs on every build, which is the only reason the two
    implementations are allowed to coexist.

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


# ---------------------------------------------------------------------------
# Schemas and writing
# ---------------------------------------------------------------------------

CELL_FIELDS = [(f"h3_r{resolution}", pa.int64()) for resolution in RESOLUTIONS]

SCHEMAS = {
    "derived_point_h3": pa.schema(
        [
            ("source_table", pa.string()),
            ("row_key", pa.string()),
            ("latitude", pa.float64()),
            ("longitude", pa.float64()),
            ("coordinate_status", pa.string()),
            ("is_usable_coordinate", pa.bool_()),
            *CELL_FIELDS,
        ]
    ),
    "derived_boundary": pa.schema(
        [
            ("boundary_set", pa.string()),
            ("boundary_id", pa.string()),
            ("boundary_name", pa.string()),
            ("area_sq_km", pa.float64()),
            ("population", pa.int64()),
            ("housing_units", pa.int64()),
            ("geojson", pa.string()),
        ]
    ),
    "derived_polygon_h3": pa.schema(
        [
            ("boundary_set", pa.string()),
            ("boundary_id", pa.string()),
            ("resolution", pa.int32()),
            ("h3_cell", pa.int64()),
            ("is_interior", pa.bool_()),
            ("is_primary", pa.bool_()),
            ("is_allocation_cell", pa.bool_()),
        ]
    ),
    "derived_point_boundary": pa.schema(
        [
            ("source_table", pa.string()),
            ("row_key", pa.string()),
            ("boundary_set", pa.string()),
            ("boundary_id", pa.string()),
            ("assignment_method", pa.string()),
        ]
    ),
    "derived_h3_population": pa.schema(
        [
            ("resolution", pa.int32()),
            ("h3_cell", pa.int64()),
            ("population", pa.float64()),
            ("housing_units", pa.float64()),
        ]
    ),
    "derived_pip_sample": pa.schema(
        [
            ("source_table", pa.string()),
            ("row_key", pa.string()),
            ("boundary_set", pa.string()),
            ("latitude", pa.float64()),
            ("longitude", pa.float64()),
            ("exact_boundary_id", pa.string()),
            *CELL_FIELDS,
        ]
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Precompute H3 cells from the raw zone into the derived zone (ADR-5, ADR-6)."
    )
    parser.add_argument(
        "--all", action="store_true", help="build every derived table (the only mode today)"
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=2000,
        help="points per source in derived_pip_sample (default: 2000)",
    )
    parser.add_argument(
        "--raw-root",
        type=remote.zone_root,
        default=None,
        help="root of the raw zone: a directory or a gs:// prefix "
        "(default: $RAW_ZONE_DIR, else $RAW_ZONE_URI, else data/raw)",
    )
    parser.add_argument(
        "--derived-root",
        type=remote.zone_root,
        default=None,
        help="root of the derived zone: a directory or a gs:// prefix "
        "(default: $DERIVED_ZONE_DIR, else $DERIVED_ZONE_URI, else data/derived)",
    )
    args = parser.parse_args()
    if not args.all:
        parser.error("pass --all; there is nothing else to select yet")

    missing = [
        name
        for name, cfg in DATASETS.items()
        if cfg["kind"] == "polygon" and not raw_zone.has_data(cfg["table"], args.raw_root)
    ]
    if missing:
        # Points without boundaries still produce a useful derived zone, but
        # every mart that assigns a neighbourhood would come out empty, so
        # this is worth saying loudly rather than discovering in a mart.
        print(f"WARNING: no raw data for boundary dataset(s): {', '.join(missing)}")

    # The root goes to connect() as well as to every read: connect() is what
    # registers the gs:// filesystem, and it registers it for the zone it is
    # given, so a --raw-root that disagrees with the environment would otherwise
    # open a connection that cannot see the zone it is about to be asked for.
    with raw_zone.connect(args.raw_root) as con:
        # Read before building, not after. Taking the counts afterwards would
        # record a raw zone that an `ingest` running alongside this could have
        # already moved past, and the manifest would then claim coverage the
        # zone does not have. Reading first can only understate.
        raw_inputs = raw_input_state(con, args.raw_root)

        print("Boundaries:")
        boundary_rows, bridge_rows, boundary_stats = build_boundaries(con, args.raw_root)
        print("\nPoints:")
        point_rows, point_stats = build_point_h3(con, args.raw_root)

    print("\nBoundary assignment:")
    assignment_rows = build_point_boundary(point_rows, boundary_rows, bridge_rows)

    print("\nDerived measures:")
    population_rows = build_h3_population(boundary_rows, bridge_rows)
    print(f"  derived_h3_population: {len(population_rows)} cells")
    sample_rows = build_pip_sample(point_rows, boundary_rows, args.sample_size)
    print(f"  derived_pip_sample: {len(sample_rows)} exact answers")
    _check_oracle_agrees(sample_rows, assignment_rows)

    tables = {
        "derived_point_h3": point_rows,
        "derived_boundary": boundary_rows,
        "derived_polygon_h3": bridge_rows,
        "derived_point_boundary": assignment_rows,
        "derived_h3_population": population_rows,
        "derived_pip_sample": sample_rows,
    }
    entries = []
    for table, rows in tables.items():
        path = derived_zone.write_table(table, rows, SCHEMAS[table], args.derived_root)
        entries.append({"table": table, "rows": len(rows), "path": str(path)})
        print(f"  wrote {len(rows)} rows to {path}")

    derived_zone.write_manifest(
        [*entries, {"coordinate_quality": point_stats, "boundary_coverage": boundary_stats}],
        args.derived_root,
        raw_inputs=raw_inputs,
    )
    print("\nDerived zone updated. Load it into a warehouse with:")
    print("  python ingestion/load.py --all --target duckdb")


if __name__ == "__main__":
    sys.exit(main())
