"""Points: read them from the raw zone, judge their coordinates, give them cells.

`derived_point_h3`, one row per point-bearing raw row, carrying the parsed
coordinates, a status saying whether they are usable, and the H3 cell at each
resolution in `RESOLUTIONS`. Everything spatial downstream keys on those cells,
because ADR-5 has both engines read precomputed BIGINTs rather than compute
anything: BigQuery has no H3 function to dispatch to.

Split out of `spatial.py` under PLAN-5 step 6, which is also why this module
holds two things that are not about points. `RESOLUTIONS` is read by
`boundaries.py` and `population.py`, and `dedup_sql` by `boundaries.py` and
`spatial.py`. They live here because this is the module the other two import
rather than the other way round, and one direction is what keeps the four files
free of import cycles.
"""

from pathlib import Path

import h3

import raw_zone
from dataset_registry import SF_BOUNDING_BOX

# The resolutions carried on every point row. 8 is roughly a 460 m hexagon,
# 10 roughly 65 m across. Two rather than one because they answer different
# questions: r10 is fine enough for boundary membership, r8 is coarse enough
# to aggregate into a readable map. Changing this list invalidates every
# stored cell (ADR-5), so a change here means `make clean-derived` and
# `make spatial`, not a dbt rebuild.
#
# r9 was carried as a third column until ADR-10 dropped it. It was kept only
# so that ADR-2's original resolution guess stayed checkable, which is a
# dev-note reason paying a permanent schema cost; the measurement is now
# recorded in ADR-10 in prose, where it costs nothing. For the record:
# 705,067 points occupied 15,773 cells at r8, 29,040 at r9 and 47,627 at r10.
RESOLUTIONS = (8, 10)

# Coordinates that are on Earth but not plausibly a San Francisco address.
# Kept separate from impossible ones because they mean different things: a
# business registered in San Francisco with its location in Atlanta is
# correct data that this warehouse cannot map, while a latitude of 5999163
# is a Web Mercator metre that leaked into a degree column.
COORDINATE_STATUSES = ("ok", "missing", "unparseable", "impossible", "out_of_bounds")


# ---------------------------------------------------------------------------
# Reading the raw zone
# ---------------------------------------------------------------------------


def dedup_sql(inner: str, key: str) -> str:
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


def read_points(
    con, cfg: dict, root: Path | str | None, partitions: list[str] | None = None
) -> list[tuple]:
    """(row_key, latitude_text, longitude_text) for one point dataset.

    `partitions` narrows the answer to rows whose key appears in those
    `ingest_date` partitions. **It does not narrow the deduplication**, and the
    difference is the whole correctness argument for the incremental path
    (PLAN-5 step 9). The dedup below still runs over the entire table, so each
    returned row is the same version a full run would have chosen; the
    partition list only decides which keys are asked about.

    Deduplicating inside the new partitions instead would be the obvious
    shortcut and would be wrong in a way nothing downstream could see. Ingestion
    is incremental on `:updated_at`, so a re-ingested row normally arrives with
    a newer one and the two agree, but a row whose `:updated_at` moved backwards
    upstream would win in the new partitions and lose over the whole zone, and
    the derived zone would then hold a version the staging models do not.

    Narrowing the keys is safe for the opposite reason: the zone is append-only
    (ADR-4), so a key absent from every changed partition has the same set of
    versions it had last run and therefore the same winner.
    """
    latitude, longitude = _point_expressions(cfg)
    key = cfg["grain_key"]
    inner = (
        f"select {key} as row_key, {latitude} as lat_text, {longitude} as lon_text, "
        f"_socrata_updated_at, _ingested_at from {raw_zone.read_sql(cfg['table'], root)}"
    )
    latest = f"select row_key, lat_text, lon_text from ({dedup_sql(inner, 'row_key')})"
    if partitions is None:
        return con.execute(latest).fetchall()
    touched = (
        f"select distinct {key} as row_key "
        f"from {raw_zone.read_sql(cfg['table'], root, partitions=partitions)}"
    )
    return con.execute(f"select * from ({latest}) where row_key in ({touched})").fetchall()


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


def point_row(table: str, row_key, lat_text, lon_text) -> dict:
    """One `derived_point_h3` row. The whole of what the H3 precompute costs.

    Pure in its arguments, which is what lets an incremental run compute it for
    the rows that changed and leave the rest alone: two points with the same
    coordinate text get the same row whichever run produced it.
    """
    status, latitude, longitude = classify_coordinate(lat_text, lon_text)
    cells = (
        {
            f"h3_r{resolution}": h3.str_to_int(h3.latlng_to_cell(latitude, longitude, resolution))
            for resolution in RESOLUTIONS
        }
        # Cells are computed for out_of_bounds points too: the coordinate is
        # real, so the cell is real, and it is only the San Francisco boundary
        # sets that will not match it. Not computing them would make "outside
        # the city" and "no location at all" look identical downstream.
        if status in ("ok", "out_of_bounds")
        else dict.fromkeys((f"h3_r{r}" for r in RESOLUTIONS), None)
    )
    return {
        "source_table": table,
        "row_key": str(row_key),
        "latitude": latitude,
        "longitude": longitude,
        "coordinate_status": status,
        "is_usable_coordinate": status == "ok",
        **cells,
    }


def build_points(
    con, cfg: dict, root: Path | str | None, partitions: list[str] | None = None
) -> list[dict]:
    """`derived_point_h3` rows for one point dataset, whole or partial.

    With `partitions`, only the rows those partitions touched come back, and
    the caller merges them over what the last run wrote. See `read_points` for
    why that is the same answer a full rebuild gives.
    """
    return [
        point_row(cfg["table"], row_key, lat_text, lon_text)
        for row_key, lat_text, lon_text in read_points(con, cfg, root, partitions)
    ]


def coordinate_stats(rows: list[dict]) -> dict:
    """The per-source coordinate quality summary, counted from the rows.

    Counted from the finished rows rather than tallied while building them, so
    that a table assembled from a cache plus a day of new points reports the
    quality of the whole table and not of the day.
    """
    counts = dict.fromkeys(COORDINATE_STATUSES, 0)
    for row in rows:
        counts[row["coordinate_status"]] += 1
    return {"total": len(rows), **counts}


def report_coordinates(name: str, stats: dict) -> None:
    """The one line per dataset that says how much of it is mappable."""
    total, usable = stats["total"], stats["ok"]
    rate = 0.0 if total == 0 else 100.0 * (total - usable) / total
    print(
        f"[{name}] {total} rows, {usable} usable ({rate:.2f}% dropped): "
        + ", ".join(f"{status}={stats[status]}" for status in COORDINATE_STATUSES[1:])
    )
