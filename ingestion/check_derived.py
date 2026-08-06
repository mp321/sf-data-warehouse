"""Is the derived zone still what the raw zone and the code say it should be?

The derived zone is a pure function of the raw zone plus `spatial.py`, and
nothing recomputes it automatically. So `make ingest` followed by `make load`
and `make build`, with `make spatial` skipped, leaves the warehouse in a state
that is wrong in a specific and quiet way: every raw row ingested since the
last `make spatial` reaches staging with null geography, because
`join_point_geography` is a LEFT join (deliberately, so a staging model's row
count never depends on whether the spatial step has run).

What that looked like before this check existed: four `not_null` failures on
`coordinate_status` and `is_usable_coordinate`, four models downstream of the
actual mistake, plus fifty-one skipped nodes hiding behind them. Every number
in the failure was correct and none of them said "run make spatial".

This says it directly, by comparing what `spatial.py` recorded in the derived
manifest against the raw zone and the code as they are now. Three distinct
answers, because they have different causes and different consequences:

  STALE    rows exist in the raw zone that the derived zone has never seen.
           Those rows have no geography at all. Exits nonzero.
  DRIFT    the row set is unchanged but the watermark has moved, so existing
           rows were re-ingested with new values. Coordinates in the derived
           zone may be a version behind, which no test downstream can detect,
           since the row is present and its geography is non-null. Warns.
  RECODED  the zone was built by code that no longer exists. Nothing about
           the raw zone has to have moved for this, so the two comparisons
           above both pass. Exits nonzero.

**RECODED is here because it happened.** On 2026-08-05 the bucket's derived
zone was found carrying H3 r9 cells that ADR-10 had removed from the code the
day before, and this file compared row counts, which agreed: the raw zone had
not moved and only the code had. It surfaced as an `accepted_values` failure in
`make build-bigquery`, several steps downstream of the cause, and `make check`
could not have caught it at all, being DuckDB-only and local-zone-only by
design. A schema change in the derived zone is invisible to a row count.

It is a hard failure and not a warning, unlike DRIFT, and the difference is
what a build does with the answer. A drifted zone holds slightly old
coordinates for rows that are all present; a recoded zone holds columns or
values the code cannot produce, which is a build that fails somewhere else or,
worse, does not.

Deliberately reads the zones and not the warehouse. This has to be runnable
before `make load`, which is the step that would carry the staleness in. It
also reads `spatial.py` as bytes rather than importing it, so it still answers
on code that does not currently parse.

Usage:
    python ingestion/check_derived.py              # warn only, exit 0
    python ingestion/check_derived.py --strict     # exit nonzero if not current
Optional environment variables:
    RAW_ZONE_DIR      root of the raw zone (default: data/raw)
    DERIVED_ZONE_DIR  root of the derived zone (default: data/derived)
"""

import argparse
import sys
from pathlib import Path

# `derived_state` is imported rather than reimplemented, and that is the whole
# reason it is a module. The row counts have to be computed exactly the way
# `spatial.py` computed the ones in the manifest, dedup and all, or the
# comparison invents a difference, and the same goes for the code stamp. It is
# `derived_state` and not `spatial` because a checker should not have to import
# the code it is checking; the stamp is read off the source as bytes.
import derived_state
import derived_zone
import raw_zone
import remote

# Exit codes under --strict. Distinct from 1, and from each other, so a
# Makefile or a CI step can tell "the derived zone is behind", "it was built by
# code that no longer exists" and "this script broke" apart.
STALE_EXIT = 3
RECODED_EXIT = 4

REBUILD_HINT = "Run `make spatial && make load` before building."


def compare(current: dict, recorded: dict) -> tuple[list[str], list[str]]:
    """(stale, drifted) descriptions, comparing raw zone now against then.

    A table missing from `recorded` is stale rather than ignored: it means the
    derived zone was built when that dataset had no raw data, so none of its
    rows have geography. A table missing from `current` is not reported at all,
    because a raw table cannot lose rows (the zone is append-only) and the only
    way to see this is a raw zone pointed somewhere else.
    """
    stale: list[str] = []
    drifted: list[str] = []

    for table, now in sorted(current.items()):
        then = recorded.get(table)
        if then is None:
            stale.append(f"{table}: {now['rows']} rows in the raw zone, none in the derived zone")
            continue

        gap = now["rows"] - then["rows"]
        if gap > 0:
            stale.append(
                f"{table}: {gap} row(s) with no geography "
                f"({now['rows']} in the raw zone, {then['rows']} when spatial last ran)"
            )
        elif now["watermark"] != then["watermark"]:
            drifted.append(
                f"{table}: {now['rows']} row(s) unchanged in number but re-ingested "
                f"(watermark {then['watermark']} -> {now['watermark']})"
            )
    return stale, drifted


def check(raw_root: Path | str | None, derived_root: Path | str | None) -> int:
    """Report on the derived zone. Returns a nonzero exit code if it is not current."""
    manifest = derived_zone.read_manifest(derived_root)
    if manifest is None:
        # Not this check's failure to report. load.py already names the step
        # for a missing zone, and every spatial model building empty is loud
        # in a way a stale zone is not.
        print("derived zone: not built. `make spatial` has not run, or data/derived was deleted.")
        return 0

    built_at = manifest.get("generated_at", "unknown")

    # The code question first, because it is the one that cannot be answered by
    # looking at the data and the one whose answer changes what the others
    # mean. A zone built by code that no longer exists is not "behind"; it is a
    # cache of a function this repo does not contain, and its row counts
    # agreeing with the raw zone says nothing about its columns or its values.
    recoded = derived_state.describe_code_change(
        manifest.get(derived_zone.CODE_VERSION_KEY), derived_state.code_version()
    )
    if recoded:
        print(f"ERROR: the derived zone was built by code that no longer exists ({built_at}):")
        for line in recoded:
            print(f"  {line}")
        print(
            "\nEvery row in it may be correct and every row count may agree; what it holds "
            "is whatever the old code computed. Nothing downstream can tell, which is how "
            "an r9 cell survived ADR-10 in the bucket for a day on 2026-08-05.\n"
            f"{REBUILD_HINT}"
        )
        return RECODED_EXIT

    recorded = manifest.get(derived_zone.RAW_INPUTS_KEY)
    if recorded is None:
        print(
            "derived zone: built before input counts were recorded, so staleness "
            f"cannot be checked. Re-run `make spatial` to make it checkable. {REBUILD_HINT}"
        )
        return 0

    with raw_zone.connect(raw_root) as con:
        current = derived_state.raw_input_state(con, raw_root)

    stale, drifted = compare(current, recorded)

    for line in drifted:
        print(f"WARNING: {line}")
    if drifted and not stale:
        print(
            f"\nderived zone built {built_at} covers every row, but the values behind "
            f"some of them have changed. {REBUILD_HINT}"
        )

    if not stale:
        if not drifted:
            print(f"derived zone is current with the raw zone and the code (built {built_at}).")
        return 0

    print(f"\nERROR: the derived zone is stale. It was built {built_at}, and since then:")
    for line in stale:
        print(f"  {line}")
    print(
        "\nThose rows will reach staging with null coordinates, null H3 cells and no "
        "neighbourhood, and the first thing to notice will be a not_null test several "
        f"models downstream.\n{REBUILD_HINT}"
    )
    return STALE_EXIT


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check the derived zone against the raw zone it was built from."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"exit {STALE_EXIT} when the derived zone is stale and {RECODED_EXIT} when it "
        "was built by code that no longer exists (default: report and exit 0)",
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

    status = check(args.raw_root, args.derived_root)
    sys.exit(status if args.strict else 0)


if __name__ == "__main__":
    main()
