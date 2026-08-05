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
from dataset_registry import SF_BOUNDING_BOX, point_datasets

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


def read_points(con, name: str, cfg: dict, root: Path | str | None) -> list[tuple]:
    """(row_key, latitude_text, longitude_text) for one point dataset."""
    latitude, longitude = _point_expressions(cfg)
    key = cfg["grain_key"]
    inner = (
        f"select {key} as row_key, {latitude} as lat_text, {longitude} as lon_text, "
        f"_socrata_updated_at, _ingested_at from {raw_zone.read_sql(cfg['table'], root)}"
    )
    return con.execute(
        f"select row_key, lat_text, lon_text from ({dedup_sql(inner, 'row_key')})"
    ).fetchall()


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
