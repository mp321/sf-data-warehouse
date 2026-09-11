"""Is every snapshot partition in the raw zone as complete as its predecessor?

`check_runs.py` asks whether the zone agrees with the manifests beside it.
This asks a different question about the same zone, and it is the one nothing
was asking: whether a `refresh: snapshot` partition holds the dataset, or only
the part of it that arrived before the run died.

**What a partial snapshot looks like from the outside, which is the problem.**
`refresh: snapshot` says upstream republishes the whole dataset, so a complete
run writes every row into one `ingest_date` partition. A run killed mid-fetch
writes a partition too: `_flush` lands each buffer of rows as it fills, so a
run that died after four flushes leaves 200,000 rows of a 366,000-row registry
in a partition that is indistinguishable, by shape, name, column set and
manifest, from a complete one. `check_runs.py` deliberately does not fire on
it, and is right not to: `_flush` counts as it writes and `_finish` writes the
manifest on the failure path, so the manifest claims exactly the 200,000 rows
that durably landed. The zone is internally consistent. It is just short.

The only thing that distinguishes the two is the row that is not there, and
the only reference for what should have been there is the partition before it.
Hence this check: **compare a snapshot partition's distinct `grain_key` count
against its predecessor's, and fail when it has fallen off a cliff.**

  SHORT   a snapshot partition holds materially fewer distinct grain_keys
          than the partition before it. Exits 3.

**Distinct `grain_key` and not rows**, for the same reason the prune proves
itself on `grain_key`: rows are versions and keys are records. A run that
refetched the same registry twice inside one day writes two files into one
partition and doubles the rows while the key count is unchanged, and the key
count is the one that says whether the dataset is all there.

**The predecessor and not a fixed floor.** A constant would have to be
maintained per dataset and would go stale in the direction that matters: it is
wrong on the day a dataset legitimately grows, and it is the day nobody edits
it. The partition before is the measurement this project already trusts for
the same job, and it moves with the data by construction. The first partition
of a dataset has no predecessor and is not checked, which is correct and is
also the reason this is a guard and not a proof: it cannot tell a zone whose
every partition is short from one that is fine.

**Why a tolerance and not equality.** A snapshot's key count moves a little
every day in both directions and neither direction is a defect. It grows as
the city registers businesses, about 0.03% a day on `business_locations`
measured over 2026-08-14 to 2026-09-05, and it shrinks as the city withdraws
records, 6 to 22 keys at a time over the same window, which is what ADR-19 is
about. `SHORTFALL_TOLERANCE` is set two orders of magnitude above that noise
and two orders below a mid-fetch death, which loses whole multiples of
`ROWS_PER_FILE`. The gap between the two is wide enough that the exact
constant is not load bearing; what is load bearing is that it sits inside it.

**It does not gate `make build`, and that is a decision rather than an
oversight (ADR-19 step 3).** `check_derived.py` gates the build because what
it catches makes a build wrong: rows reach staging with null geography.
`check_runs.py` gates nothing because what it catches makes a report wrong and
a build correct. This one is `check_runs.py`'s case *today* and
`check_derived.py`'s case *after ADR-19 step 4*, and it ships as the former
because step 4 has not landed:

  - Today staging unions every partition and deduplicates by `grain_key` to
    the newest `_socrata_updated_at`. A short partition contributes fewer keys
    to that union and the older complete partitions still supply the rest, so
    every model returns exactly what it returned before. The build is correct
    and the zone is the thing that is wrong. Gating on it would wedge the
    pipeline on a condition with no consequence yet, and a gate that fires
    without consequence is the gate someone switches off, which is the
    argument CLAUDE.md already makes about row counts in the context pack.
  - After step 4 the newest `ingest_date` decides which keys a snapshot
    staging model returns at all. A short newest partition would then silently
    truncate every snapshot model, and the row counts would move in exactly
    the shape step 4 predicts as its expected one-time movement, which is the
    one failure that would be indistinguishable from success. **Adding
    `check-snapshots` to `BUILD_PREREQS` is therefore step 4's first line and
    not an optional part of it.**

Until then it runs where `check_runs.py` runs: `make check-snapshots` by hand,
and inside `make ci-build` against the fixture zone, credential-free, so it is
a PR gate on the code even while it gates no build.

Reads the zone and not `raw_ingest_runs`, for `check_runs.py`'s reason: the
warehouse copy assumes `load.py` did its job, and a check of the zone that
trusts a derived copy of the zone is checking the wrong thing.

Usage:
    python ingestion/check_snapshots.py                 # report, exit 0
    python ingestion/check_snapshots.py --strict        # exit 3 on a shortfall
    python ingestion/check_snapshots.py business_locations
    python ingestion/check_snapshots.py --tolerance 0.05
Optional environment variables:
    RAW_ZONE_DIR      root of the raw zone, beating RAW_ZONE_URI
    RAW_ZONE_URI      gs:// prefix of the raw zone
"""

import argparse
import sys
from itertools import pairwise
from pathlib import Path

import dataset_registry
import raw_zone
import remote

# Exit code under --strict. Distinct from 1 and from its siblings' codes so a
# Makefile or a human can tell "a snapshot partition is short" apart from
# "this script broke". The numbering follows check_runs.py and
# check_derived.py, whose 3 is likewise the verdict about the data.
SHORT_EXIT = 3

# How far a partition's distinct grain_key count may fall below its
# predecessor's before it is called short. 2%: see the module docstring for
# why the constant is not load bearing and the order of magnitude is.
SHORTFALL_TOLERANCE = 0.02


def key_counts(con, table: str, grain_key: str, root: Path | str | None) -> list[tuple[str, int]]:
    """Distinct grain_keys per `ingest_date` for one dataset, oldest first.

    One grouped pass over the tree rather than a query per partition, because
    on a remote zone every query is a listing plus a fetch and the partitions
    of one dataset are one glob.
    """
    if not raw_zone.has_data(table, root):
        return []
    rows = con.execute(
        f"select {raw_zone.PARTITION_KEY}, count(distinct {grain_key}) "
        f"from {raw_zone.read_sql(table, root)} group by 1 order by 1"
    ).fetchall()
    return [(str(partition), int(keys)) for partition, keys in rows]


def shortfalls(counts: list[tuple[str, int]], tolerance: float) -> list[dict]:
    """Partitions whose key count fell more than `tolerance` below the previous.

    Compared against the immediately preceding partition rather than against
    the largest seen, so one short partition reports once rather than making
    every partition after it look short too. The consequence is deliberate: a
    run that died mid-fetch names itself, and the complete partition that
    follows it is not accused of anything.
    """
    found = []
    for (before, before_keys), (partition, keys) in pairwise(counts):
        if before_keys == 0:
            continue
        drop = (before_keys - keys) / before_keys
        if drop > tolerance:
            found.append(
                {
                    "partition": partition,
                    "keys": keys,
                    "previous": before,
                    "previous_keys": before_keys,
                    "drop": drop,
                }
            )
    return found


def selected(only: list[str]) -> dict:
    """The snapshot datasets to check, refusing a delta one by name.

    A delta partition holds only what changed since the watermark, so its key
    count against the partition before it is a number about upstream activity
    and not about completeness. Reporting on it would be a confident wrong
    sentence, which is `prune_raw.py`'s refusal for the same reason.
    """
    snapshots = dataset_registry.snapshot_datasets()
    if not only:
        return snapshots
    chosen = {}
    for name in only:
        if name in snapshots:
            chosen[name] = snapshots[name]
        elif name in dataset_registry.DATASETS:
            raise SystemExit(
                f"{name} is refresh: {dataset_registry.DATASETS[name]['refresh']}. A delta "
                "partition holds only the rows that changed since the watermark, so it is "
                "meant to be smaller than the one before it and there is nothing here to "
                "check. Only snapshot datasets are considered."
            )
        else:
            raise SystemExit(f"{name} is not in the dataset registry.")
    return chosen


def check(raw_root: Path | str | None, only: list[str], tolerance: float) -> int:
    """Report on snapshot completeness. Returns a nonzero exit code on a shortfall."""
    root = raw_root if raw_root is not None else raw_zone.raw_root()
    print("snapshot partitions against the partition before them")
    print(f"  raw zone   {root}")
    print(f"  tolerance  a partition may hold {tolerance:.1%} fewer grain_key(s) than the last\n")

    short: list[dict] = []
    with raw_zone.connect(root) as con:
        for _name, cfg in sorted(selected(only).items()):
            table = cfg["table"]
            counts = key_counts(con, table, cfg["grain_key"], root)
            if not counts:
                print(f"{table:36s} SKIP nothing in the zone")
                continue

            found = shortfalls(counts, tolerance)
            for entry in found:
                entry["table"] = table
            short.extend(found)

            verdict = f"FAIL {len(found)} short" if found else "PASS"
            compared = len(counts) - 1
            print(
                f"{table:36s} {verdict:16s} {len(counts)} partition(s), "
                f"{compared} compared, newest {counts[-1][1]} key(s)"
            )
            if compared == 0:
                print("    only one partition, so there is nothing to compare it against")

    deltas = sorted(set(dataset_registry.DATASETS) - set(dataset_registry.snapshot_datasets()))
    print(f"\nsummary\n  {len(short)} short partition(s)")
    print(f"  delta sources are not considered: {', '.join(deltas)}")

    if short:
        print("\nERROR: snapshot partitions holding materially fewer grain_key(s) than the last:")
        for entry in short:
            print(
                f"  {entry['table']} ingest_date={entry['partition']}: {entry['keys']} "
                f"distinct grain_key(s) against {entry['previous_keys']} in "
                f"{entry['previous']}, down {entry['drop']:.1%}"
            )
        print(
            "\nA snapshot partition is meant to hold the whole dataset, so this is most "
            "likely a run that died mid-fetch and wrote what it had: the manifest beside it "
            "will reconcile, because it claims exactly what durably landed, and "
            "`check_runs.py` will pass. Re-run `make ingest` for the dataset to write a "
            "complete partition for today. The short one is then superseded and "
            "`make prune-raw` can prove it away. If upstream genuinely shrank by this much, "
            "that is a fact about the source worth writing down rather than a threshold "
            "worth raising."
        )
        return SHORT_EXIT

    print("  PASS no snapshot partition is materially shorter than the one before it")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check that each snapshot partition is as complete as its predecessor."
    )
    parser.add_argument(
        "datasets",
        nargs="*",
        help="dataset names to check (default: every refresh: snapshot dataset)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"exit {SHORT_EXIT} when a snapshot partition is short (default: report and exit 0)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=SHORTFALL_TOLERANCE,
        help=f"fractional drop in distinct grain_key(s) that counts as short "
        f"(default: {SHORTFALL_TOLERANCE})",
    )
    parser.add_argument(
        "--raw-root",
        type=remote.zone_root,
        default=None,
        help="root of the raw zone: a directory or a gs:// prefix "
        "(default: $RAW_ZONE_DIR, else $RAW_ZONE_URI, else data/raw)",
    )
    args = parser.parse_args()

    status = check(args.raw_root, args.datasets, args.tolerance)
    sys.exit(status if args.strict else 0)


if __name__ == "__main__":
    main()
