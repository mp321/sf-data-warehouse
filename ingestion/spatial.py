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

**What comes out.** Six tables and the module that builds each. A table is
rewritten when something it is computed from has moved and left alone when
nothing has (PLAN-5 step 9, and the section below):

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

**What a re-run recomputes, and what it does not.** PLAN-5 step 9. A run reads
the manifest the last one left, compares the raw zone's `ingest_date`
partitions and the code stamp against what is recorded there, and rebuilds what
those say has moved. `derived_state.py` owns that comparison and the stamp; the
correctness argument for each reuse is in the function that does it.

    boundary, polygon_h3  a polygon dataset's partitions changed
    h3_population         the boundaries were rebuilt
    point_h3              that source's partitions changed, and then only for
                          the rows those partitions touched
    point_boundary        anything above changed. Always whole: it is about a
                          second for half a million points and depends on both
                          halves, so a partial rebuild would cost more to
                          reason about than to run.
    pip_sample            anything above changed, reusing the exact answers for
                          sampled points that did not move. The expensive one,
                          and the reason this is worth doing at all.

Nothing is reused when the code stamp moves, which is the point of the stamp
rather than a limitation of it: the zone claims to be a pure function of the raw
zone plus this code, and a cache of a function that no longer exists is the one
state that claim cannot survive. `make clean-derived && make spatial` and
`--full` are the two ways to force it by hand, and neither should be needed.

Usage:
    python ingestion/spatial.py --all
    python ingestion/spatial.py --all --full
    python ingestion/spatial.py --all --sample-size 5000
"""

import argparse
import sys
from datetime import datetime, timezone

import pyarrow as pa

import derived_state
import derived_zone
import raw_zone
import remote
from boundaries import (
    build_boundaries,
    build_pip_sample,
    build_point_boundary,
    check_oracle_agrees,
)
from dataset_registry import DATASETS, point_datasets
from h3_points import RESOLUTIONS, build_points, coordinate_stats, report_coordinates
from population import build_h3_population

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


# ---------------------------------------------------------------------------
# Reading what the last run left
# ---------------------------------------------------------------------------


def cached_entries(manifest: dict | None) -> dict[str, dict]:
    """The per-table entries the last run wrote, keyed by table."""
    return {
        entry["table"]: entry for entry in (manifest or {}).get("tables", []) if "table" in entry
    }


def cached_stats(manifest: dict | None) -> tuple[dict, dict]:
    """(coordinate_quality, boundary_coverage) out of the last manifest.

    They ride in the `tables` list as a trailing entry with no `table` key,
    which is how the manifest has always carried them.
    """
    for entry in (manifest or {}).get("tables", []):
        if "coordinate_quality" in entry:
            return entry["coordinate_quality"], entry.get("boundary_coverage", {})
    return {}, {}


def cached_points(root) -> dict[str, list[dict]]:
    """Last run's `derived_point_h3`, grouped by source table.

    One read for all four point datasets, because they share one Parquet file.
    """
    grouped: dict[str, list[dict]] = {}
    for row in derived_zone.read_table("derived_point_h3", root) or ():
        grouped.setdefault(row["source_table"], []).append(row)
    return grouped


def report_plan(plan: derived_state.Plan) -> None:
    """Say what this run is about to do before it spends any time doing it."""
    if plan.is_full:
        print("Full rebuild:")
        for reason in plan.reasons:
            print(f"  {reason}")
        return
    if plan.is_current:
        return
    print("Incremental rebuild:")
    if plan.rebuild_boundaries:
        print("  boundaries: a polygon dataset's partitions changed")
    for table, partitions in sorted(plan.points.items()):
        if partitions is None:
            print(f"  {table}: rebuilding whole")
        elif partitions:
            print(f"  {table}: partition(s) {', '.join(partitions)}")


# ---------------------------------------------------------------------------
# Building, or not
# ---------------------------------------------------------------------------


def boundary_half(con, args, plan, previous_coverage: dict) -> tuple[list, list, dict]:
    """(boundary_rows, bridge_rows, coverage stats), rebuilt or read back.

    All or nothing, and there is nothing to gain from finer: 733 polygons take
    about a second, and every other table in the zone is computed against them.
    """
    print("\nBoundaries:")
    if plan.rebuild_boundaries:
        return build_boundaries(con, args.raw_root)
    boundary_rows = derived_zone.read_table("derived_boundary", args.derived_root)
    bridge_rows = derived_zone.read_table("derived_polygon_h3", args.derived_root)
    print(f"  reused {len(boundary_rows)} boundaries and {len(bridge_rows)} bridge rows")
    return boundary_rows, bridge_rows, previous_coverage


def point_half(con, args, plan) -> tuple[list[dict], dict, bool]:
    """(point rows, per-source quality stats, whether anything was recomputed).

    The merge is by `row_key` with the fresh row winning, which is the same
    answer a full rebuild gives because `read_points` deduplicates over the
    whole table rather than over the partitions it was asked about.
    """
    print("\nPoints:")
    cache = {} if plan.is_full else cached_points(args.derived_root)
    rows_out: list[dict] = []
    stats: dict = {}
    rebuilt = False
    for name, cfg in point_datasets().items():
        table = cfg["table"]
        if table not in plan.points:
            print(f"[{name}] no Parquet in the raw zone; skipped")
            continue
        partitions = plan.points[table]
        if partitions is not None and not cache.get(table):
            # A table with raw data always has derived rows, so an empty cache
            # here is a zone that lost them rather than a dataset that has none.
            print(f"[{name}] nothing cached; rebuilding whole")
            partitions = None
        if partitions is None:
            rows = build_points(con, cfg, args.raw_root)
            rebuilt = True
        elif not partitions:
            # No partition of this table has moved, so the raw zone is not read
            # for it at all. This is the case a scheduled build hits for every
            # dataset but the one that was ingested.
            rows = cache[table]
        else:
            merged = {row["row_key"]: row for row in cache[table]}
            for row in build_points(con, cfg, args.raw_root, partitions):
                merged[row["row_key"]] = row
            rows = list(merged.values())
            rebuilt = True
        rows_out.extend(rows)
        stats[table] = coordinate_stats(rows)
        report_coordinates(name, stats[table])
    return rows_out, stats, rebuilt


def write_zone(
    tables: dict, root, previous: dict[str, dict], fallback_built_at: str, extra: dict
) -> list[dict]:
    """Write the tables that changed, keep the entries for those that did not.

    `built_at` is per table and is what makes a rebuild attributable to a run.
    Establishing that on 2026-08-05 took GCS object mtimes and a cell-count
    comparison against another zone, because nothing in the zone recorded it.

    `extra` is per-table fields to record beside the counts, for the arguments a
    table was built with. Only `derived_pip_sample` has one, `--sample-size`,
    and it is recorded because it is an input to the zone that the code stamp
    cannot cover: it arrives on the command line rather than in the source.
    """
    now = datetime.now(timezone.utc).isoformat()
    entries = []
    for table, rows in tables.items():
        if rows is None:
            entry = {**previous[table], "rebuilt": False}
            entry.setdefault("built_at", fallback_built_at)
            entries.append(entry)
            print(f"  kept {entry['rows']} rows in {entry['path']}")
            continue
        path = derived_zone.write_table(table, rows, SCHEMAS[table], root)
        entries.append(
            {
                "table": table,
                "rows": len(rows),
                "path": str(path),
                "built_at": now,
                "rebuilt": True,
                **extra.get(table, {}),
            }
        )
        print(f"  wrote {len(rows)} rows to {path}")
    return entries


def forced_reason(args, manifest: dict | None) -> str | None:
    """This run's own reason to rebuild everything, as a sentence, or None.

    The reasons `derived_state` cannot reach: what the command line asked for,
    and what is true of the zone on disk rather than of the manifest describing
    it.
    """
    if args.full:
        return "--full was passed"
    if manifest is None:
        return None
    absent = [table for table in SCHEMAS if not derived_zone.has_data(table, args.derived_root)]
    if absent:
        # The manifest is not the zone. A table deleted from under it would
        # otherwise be reported as reused and never written.
        return f"missing from the derived zone: {', '.join(absent)}"
    sampled_at = cached_entries(manifest).get("derived_pip_sample", {}).get("sample_size")
    if sampled_at is not None and sampled_at != args.sample_size:
        # The one input to the zone the code stamp cannot see, because it
        # arrives on the command line rather than in the source. Without this,
        # `--sample-size 5000` on a current zone prints "nothing to do" and
        # leaves the old sample in place.
        return f"--sample-size changed from {sampled_at} to {args.sample_size}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Precompute H3 cells from the raw zone into the derived zone (ADR-5, ADR-6)."
    )
    parser.add_argument(
        "--all", action="store_true", help="build every derived table (the only mode today)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="rebuild every table, ignoring what the last run left (default: reuse what "
        "the raw zone and the code have not moved past)",
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

    manifest = derived_zone.read_manifest(args.derived_root)
    code = derived_state.code_version()
    forced = forced_reason(args, manifest)

    # The root goes to connect() as well as to every read: connect() is what
    # registers the gs:// filesystem, and it registers it for the zone it is
    # given, so a --raw-root that disagrees with the environment would otherwise
    # open a connection that cannot see the zone it is about to be asked for.
    with raw_zone.connect(args.raw_root) as con:
        # Read before building, not after. Taking the counts afterwards would
        # record a raw zone that an `ingest` running alongside this could have
        # already moved past, and the manifest would then claim coverage the
        # zone does not have. Reading first can only understate.
        raw_inputs = derived_state.raw_input_state(con, args.raw_root)
        partitions = derived_state.partition_state(con, args.raw_root)
        plan = derived_state.plan_rebuild(manifest, partitions, code, forced)
        report_plan(plan)

        if plan.is_current:
            # Nothing to write, and deliberately not even the Parquet: rewriting
            # six identical files is the work this step exists to avoid. The
            # manifest is rewritten because it now records a later run having
            # checked the zone, and each table's `built_at` carries forward
            # untouched, which is what keeps a rebuild attributable to the run
            # that actually did one.
            derived_zone.write_manifest(
                [
                    {**entry, "rebuilt": False} if "table" in entry else entry
                    for entry in manifest["tables"]
                ],
                args.derived_root,
                raw_inputs=raw_inputs,
                partitions=partitions,
                code_version=code,
            )
            print("Derived zone is current: the raw zone and the code are where it left them.")
            print("  Nothing rebuilt. Force one with `--full`, or `make clean-derived`.")
            return

        previous = cached_entries(manifest)
        # Only the boundary half is carried forward. Coordinate quality is
        # recounted from the finished rows every run, so a table assembled from
        # a cache plus a day of new points reports the whole table.
        _, previous_coverage = cached_stats(manifest)
        boundary_rows, bridge_rows, boundary_stats = boundary_half(
            con, args, plan, previous_coverage
        )
        point_rows, point_stats, rebuilt_points = point_half(con, args, plan)

    print("\nBoundary assignment:")
    assignment_rows = build_point_boundary(point_rows, boundary_rows, bridge_rows)

    print("\nDerived measures:")
    if plan.rebuild_boundaries:
        population_rows = build_h3_population(boundary_rows, bridge_rows)
        print(f"  derived_h3_population: {len(population_rows)} cells")
    else:
        # Reused rather than recomputed from the cached bridge, which would
        # give the same cells and a last-bit different float: the per-cell
        # population is a sum whose order comes off a Python set, so two runs
        # of identical code over identical input differ by about 5e-13
        # residents on a couple of cells. Recomputing it would churn the file
        # and the manifest for no change anybody can measure.
        population_rows = None
        print("  derived_h3_population: unchanged, boundaries not rebuilt")
    sample_rows = build_pip_sample(
        point_rows,
        boundary_rows,
        args.sample_size,
        # Only safe while the boundaries stand still. A polygon that moved
        # changes the exact answer for a point that did not.
        cached=None
        if plan.rebuild_boundaries
        else derived_zone.read_table("derived_pip_sample", args.derived_root),
    )
    print(f"  derived_pip_sample: {len(sample_rows)} exact answers")
    # Runs on an incremental build as well as a full one, and it is the check
    # that matters most there: the oracle is built by the scalar
    # point-in-polygon and the assignments by the vectorised one, over row sets
    # that this run assembled from a cache and a recompute. If the merge lost or
    # mismatched a row, these two stop agreeing.
    check_oracle_agrees(sample_rows, assignment_rows)

    tables: dict[str, list[dict] | None] = {
        # `or plan.is_full` covers the one case where nothing was recomputed and
        # there is also nothing to carry forward: a full rebuild of a raw zone
        # with no point data in it at all.
        "derived_point_h3": point_rows if (rebuilt_points or plan.is_full) else None,
        "derived_boundary": boundary_rows if plan.rebuild_boundaries else None,
        "derived_polygon_h3": bridge_rows if plan.rebuild_boundaries else None,
        "derived_point_boundary": assignment_rows,
        "derived_h3_population": population_rows,
        "derived_pip_sample": sample_rows,
    }
    entries = write_zone(
        tables,
        args.derived_root,
        previous,
        (manifest or {}).get("generated_at", "unknown"),
        {"derived_pip_sample": {"sample_size": args.sample_size}},
    )
    derived_zone.write_manifest(
        [*entries, {"coordinate_quality": point_stats, "boundary_coverage": boundary_stats}],
        args.derived_root,
        raw_inputs=raw_inputs,
        partitions=partitions,
        code_version=code,
    )
    print("\nDerived zone updated. Load it into a warehouse with:")
    print("  python ingestion/load.py --all --target duckdb")


if __name__ == "__main__":
    sys.exit(main())
