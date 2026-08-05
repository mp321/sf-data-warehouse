"""Precompute H3 cells for points and boundaries. The geometry step (ADR-5, ADR-6).

This is the step that makes "count 311 cases inside the Mission" an integer
join. It reads the Parquet raw zone, computes H3 cells in Python, and writes
the derived zone. It talks to no API, needs no credentials, and can be
re-run at any time: everything it produces is a pure function of the raw zone
and this code.

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

**What comes out.** Six tables, all replaced wholesale on every run, and the
module that builds each:

  derived_point_h3        one row per point-bearing raw row: its coordinates,
                          whether they are usable, and its cell at r8 and r10.
                          h3_points.py
  derived_boundary        one row per polygon: name, area, and its GeoJSON.
                          boundaries.py
  derived_polygon_h3      one row per (boundary, resolution, covering cell),
                          flagged interior and primary. The bridge table.
                          boundaries.py
  derived_point_boundary  one row per (point, boundary set): the boundary the
                          point is exactly inside. boundaries.py
  derived_pip_sample      exact point-in-polygon answers for a deterministic
                          sample of points. The test oracle, not a mart input.
                          boundaries.py
  derived_h3_population   one row per (resolution, cell): population and
                          housing units interpolated from block groups.
                          population.py

This file is what remains once those three are separate (PLAN-5 step 6): the
command line, the Arrow schemas, the run order, and the manifest recording what
the raw zone held when it ran. The three containment modes that make boundary
membership work are explained in `boundaries.py`, beside the code that uses
them, and `geometry.py` holds the point-in-polygon and area implementations
that `tests/test_geometry.py` covers directly.

Usage:
    python ingestion/spatial.py --all
    python ingestion/spatial.py --all --sample-size 5000
"""

import argparse
import sys
from pathlib import Path

import pyarrow as pa

import derived_zone
import raw_zone
import remote
from boundaries import (
    build_boundaries,
    build_pip_sample,
    build_point_boundary,
    check_oracle_agrees,
)
from dataset_registry import DATASETS, point_datasets, polygon_datasets
from h3_points import RESOLUTIONS, build_point_h3, dedup_sql
from population import build_h3_population

# ---------------------------------------------------------------------------
# What the raw zone held when this ran
# ---------------------------------------------------------------------------


def raw_input_state(con, root: Path | str | None) -> dict:
    """What the raw zone held for every dataset this step reads.

    Per raw table: the deduplicated row count and the newest watermark. The
    count goes through `dedup_sql`, so it is the number of rows a staging
    model has, not the number of files' worth of appends behind them, and a
    later comparison against it means "does the derived zone still cover every
    row that exists" rather than "has anything been appended".

    Recorded for polygon datasets too. A new neighbourhood boundary does not
    leave a point without geography, so nothing downstream fails loudly, but
    every assignment in the zone was computed against the old boundaries and
    is silently one version behind. That is worth reporting.

    `check_derived.py` imports this rather than reimplementing it, which is
    what keeps its comparison honest, so it stays in this file even though the
    work either side of it moved out.
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
        rows = con.execute(f"select count(*) from ({dedup_sql(inner, 'row_key')})").fetchone()[0]
        state[table] = {
            "rows": rows,
            "watermark": raw_zone.read_watermark(table, root),
        }
    return state


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
    check_oracle_agrees(sample_rows, assignment_rows)

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
