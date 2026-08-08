"""Delete raw partitions that a later one has provably superseded.

The raw zone is append-only (ADR-4). This is the second exception to that rule
and ADR-14 is where it is argued; `ingest.py --full-refresh` is the first. Read
ADR-14 before changing anything here, because every line below is either the
proof that a deletion is safe or a refusal to make one without it.

**Why the zone needs this at all.** Measured on the real bucket on 2026-08-07:
511.9 MB over 236 objects, of which `raw_business_locations` is 397.1 MB, seven
daily partitions of about 49.6 MB each. `ingest.py` is watermark-incremental
and does not re-fetch a dataset per run, so that is not a backfill. It is
upstream republish: the city bumps `:updated_at` across a current-state
registry and the next run writes the whole 415,006 rows again into a zone that
never deletes. Staging deduplicates by `grain_key` to the newest
`_socrata_updated_at`, so once a later complete copy lands, the rows it
supersedes cannot be returned by any model. The zone was paying storage every
month for rows no query can reach. See PLAN-9.

**The distinction that makes this safe, and it is the whole of it.**

    snapshot   the partition holds the dataset as of that run, so a later
               complete run can supersede it.        prunable, once proven
    delta      the partition holds only the rows that changed since the
               watermark.                            NEVER prunable

`refresh` in the registry says which, and `dataset_registry.snapshot_datasets()`
is the filter applied before anything else happens. `311_cases` and
`building_permits` are delta: deleting a partition of either deletes rows, and
no later partition can bring them back. They are not reachable from here by any
flag, which is why there is no `--force` and no per-dataset override that could
name one.

**A bucket lifecycle rule is the obvious answer and is the wrong one**, which
is worth stating in the code and not only in the ADR, because the next person
to look at the bill will reach for it. It deletes by object age and knows
nothing about which partitions are snapshots. Pointed at this zone it destroys
311 and permit history, and the loss surfaces months later as a hole in a
monthly series that nothing can refill.

**The proof, and why membership of `snapshot` is nowhere near enough.**
`refresh: snapshot` says a partition of this dataset *can* be complete, not
that any given one *is*. A run that fetched 200 changed rows writes a partition
that looks exactly like a complete one from the outside. So each candidate is
proven against a partition that will survive the prune, and both halves have to
hold:

  1. every `grain_key` in the candidate is present in the surviving partition,
     which is what makes the rows reachable after the delete; and
  2. for every one of those keys, the surviving partition's newest
     `_socrata_updated_at` is not older than the candidate's, which is what
     makes them reachable *at the same values*. Staging picks the newest per
     key, so a superset that is behind on a key would silently change what the
     model returns, and a row-count acceptance test would not see it.

A candidate that fails either is not deleted, is reported by name, and exits
UNPROVEN_EXIT. That is the point: this refuses rather than guesses, on the same
principle as `check_derived.py`. Deleting nothing is always a correct outcome
here and deleting the wrong partition never is.

**The run manifests go with the partitions.** `check_runs.py` compares each
manifest under `<table>/_runs/` against the rows carrying its run id and exits
3 MISCOUNTED when they disagree, so a partition deleted without its manifests
makes the zone fail its own consistency check on the next run. PLAN-9 step 4
offered that or a third state in `check_runs.py` for a run whose rows were
deliberately removed; ADR-14 records why this took the first. Manifests for
runs that wrote no rows are never touched, and that exclusion is load bearing
rather than an optimisation: a run that fetched nothing writes no Parquet, so
its manifest is the only record that it ran at all, and it is the one thing in
this zone that cannot be recomputed.

Usage:
    python ingestion/prune_raw.py                    # report only, deletes nothing
    python ingestion/prune_raw.py --apply            # delete what it can prove
    python ingestion/prune_raw.py --keep 3           # retain more partitions
    python ingestion/prune_raw.py business_locations # one dataset
Optional environment variables:
    RAW_ZONE_DIR      root of the raw zone, beating RAW_ZONE_URI
    RAW_ZONE_URI      gs:// prefix of the raw zone
"""

import argparse
import shutil
import sys
from pathlib import Path

import dataset_registry
import raw_zone
import remote

# Exit codes. Distinct from 1 and from each other so a Makefile or a human can
# tell "this zone holds an old partition nothing supersedes" apart from "this
# script broke". The numbering follows check_derived.py and check_runs.py.
UNPROVEN_EXIT = 3

# Partitions retained per dataset regardless of what can be proven, newest
# first. Two rather than one because a rollback is worth about 50 MB: the
# newest complete snapshot, and one to fall back to if the newest turns out to
# have been written during an upstream incident. PLAN-9 step 3.
DEFAULT_KEEP = 2


def partitions_of(con, table: str, root: Path | str | None) -> list[str]:
    """Every `ingest_date` in one dataset's tree, oldest first.

    Read from the data rather than by listing directories, because that is the
    same path every other reader takes (`raw_zone.read_sql`) and because a
    partition prefix with no Parquet under it is not a partition. On GCS there
    are no directories at all, so a listing would be answering a different
    question.
    """
    if not raw_zone.has_data(table, root):
        return []
    rows = con.execute(
        f"select distinct {raw_zone.PARTITION_KEY} from {raw_zone.read_sql(table, root)} order by 1"
    ).fetchall()
    return [str(row[0]) for row in rows]


def supersession(con, table: str, grain_key: str, candidate: str, keeper: str, *, root) -> dict:
    """Does `keeper` hold every `grain_key` in `candidate`, at values no older?

    One query and two counts, both of which must be zero. Reads two columns of
    two partitions, so Parquet column pruning keeps it far below the size of
    the partitions themselves even against a bucket.

    `unreachable` counts keys the keeper does not have. A NULL `grain_key` in
    the candidate lands here too, because the join cannot match it: a row
    staging cannot deduplicate by is a row this cannot prove anything about,
    and the refusal is the correct answer rather than an awkward edge case.

    `regressed` counts keys the keeper has at an older `_socrata_updated_at`
    than the candidate. It should never be anything but zero against an API
    that serves current state, and it is checked because the acceptance test
    for this tool compares row counts, which would not notice a value moving
    backwards.
    """
    watermark = raw_zone.WATERMARK_COLUMN
    row = con.execute(
        f"""
        with older as (
            select {grain_key} as grain_key, max({watermark}) as watermark
            from {raw_zone.read_sql(table, root, partitions=[candidate])}
            group by 1
        ),
        newer as (
            select {grain_key} as grain_key, max({watermark}) as watermark
            from {raw_zone.read_sql(table, root, partitions=[keeper])}
            group by 1
        )
        select
            (select count(*) from older),
            (select count(*) from newer),
            (select count(*) from older
                left join newer on older.grain_key = newer.grain_key
                where newer.grain_key is null),
            (select count(*) from older
                join newer on older.grain_key = newer.grain_key
                where newer.watermark < older.watermark)
        """
    ).fetchone()
    candidate_keys, keeper_keys, unreachable, regressed = row
    return {
        "candidate_keys": candidate_keys,
        "keeper_keys": keeper_keys,
        "unreachable": unreachable,
        "regressed": regressed,
        "proven": unreachable == 0 and regressed == 0,
    }


def runs_wholly_within(con, table: str, partitions: list[str], root) -> list[str]:
    """Run ids every row of which sits in `partitions`, so their manifests go too.

    Computed over the whole table and not over the partitions being deleted,
    which is the difference between "this run's rows are here" and "this run's
    rows are only here". A run spanning two partitions is already a defect
    `check_runs.py` reports, and this leaves its manifest alone rather than
    compounding it.

    A run that wrote no rows appears nowhere in the data and so is never in
    this list. That is the intended exclusion: its manifest is the only record
    that it ran, and it is what lets `mart_pipeline_freshness` tell "ingestion
    ran and found nothing" from "ingestion has not run in three days".
    """
    rows = con.execute(
        f"select {raw_zone.RUN_ID_COLUMN}, {raw_zone.PARTITION_KEY} "
        f"from {raw_zone.read_sql(table, root)} group by 1, 2"
    ).fetchall()
    seen: dict[str, set[str]] = {}
    for run_id, partition in rows:
        if run_id is None:
            continue
        seen.setdefault(str(run_id), set()).add(str(partition))
    inside = set(partitions)
    return sorted(run_id for run_id, where in seen.items() if where <= inside)


def objects_under(location: Path | str) -> dict[str, int]:
    """Every file under a partition prefix or directory, as {name: bytes}."""
    if remote.is_remote(location):
        return remote.list_objects(location)
    directory = Path(location)
    if not directory.exists():
        return {}
    return {str(item): item.stat().st_size for item in directory.rglob("*") if item.is_file()}


def delete(paths: list[str], location_is_remote: bool) -> None:
    """Remove the objects or files named. Nothing here decides what they are."""
    if location_is_remote:
        remote.remove(paths)
        return
    for path in paths:
        Path(path).unlink(missing_ok=True)


def plan_for(con, name: str, cfg: dict, root, keep: int) -> dict:
    """What can be deleted for one snapshot dataset, and what could not be proven."""
    table = cfg["table"]
    found = partitions_of(con, table, root)
    retained = found[-keep:]
    candidates = found[: max(len(found) - keep, 0)]

    prunable: list[dict] = []
    unproven: list[dict] = []
    for candidate in candidates:
        # Proven against a partition that survives, and against the newest of
        # them. Proving it against another candidate would be proving it
        # against something about to be deleted, which proves nothing.
        keeper = retained[-1]
        result = supersession(con, table, cfg["grain_key"], candidate, keeper, root=root)
        entry = {"dataset": name, "table": table, "partition": candidate, "keeper": keeper}
        entry.update(result)
        (prunable if result["proven"] else unproven).append(entry)

    return {
        "dataset": name,
        "table": table,
        "partitions": found,
        "retained": retained,
        "prunable": prunable,
        "unproven": unproven,
    }


def selected(only: list[str]) -> dict:
    """The datasets to consider, refusing a delta one by name rather than skipping it.

    A skip would read as "considered and found nothing to do", which is the
    wrong sentence about a source no partition of which may ever be deleted.
    There is no flag that gets past this, which is the point.
    """
    snapshots = dataset_registry.snapshot_datasets()
    if not only:
        return snapshots
    unknown = [name for name in only if name not in dataset_registry.DATASETS]
    if unknown:
        sys.exit(f"unknown dataset(s): {', '.join(unknown)}")
    deltas = [name for name in only if name not in snapshots]
    if deltas:
        sys.exit(
            f"{', '.join(deltas)} is refresh: delta. A partition of a delta source holds "
            "only the rows that changed since the watermark, so deleting one deletes rows "
            "and no later partition can bring them back. This is refused rather than "
            "flagged; see ADR-14."
        )
    return {name: cfg for name, cfg in snapshots.items() if name in only}


def report(plan: dict) -> None:
    """One dataset's findings, every line naming the partition and the numbers."""
    if not plan["partitions"]:
        print(f"{plan['dataset']:26s} SKIP nothing in the zone")
        return
    print(
        f"{plan['dataset']:26s} {len(plan['partitions'])} partition(s), "
        f"{len(plan['prunable'])} prunable, {len(plan['unproven'])} unproven, "
        f"retaining {', '.join(plan['retained'])}"
    )
    for entry in plan["prunable"]:
        print(
            f"    {entry['partition']}  superseded by {entry['keeper']}: "
            f"all {entry['candidate_keys']} grain_key(s) present, none behind"
        )
    for entry in plan["unproven"]:
        print(
            f"    {entry['partition']}  NOT superseded by {entry['keeper']}: "
            f"{entry['unreachable']} of {entry['candidate_keys']} grain_key(s) absent, "
            f"{entry['regressed']} present at an older _socrata_updated_at"
        )


def paths_to_remove(plan: dict, root) -> dict[str, int]:
    """Every object this plan would delete for one dataset, as {path: bytes}.

    Partitions and the manifests of the runs wholly inside them, in one
    mapping, because they are one deletion: a partition removed without its
    manifests leaves the zone failing `check_runs.py` with exit 3 MISCOUNTED,
    which is PLAN-9 step 4 and the reason this function does not have a
    partitions-only sibling.
    """
    found: dict[str, int] = {}
    for entry in plan["prunable"]:
        prefix = remote.child(
            raw_zone.dataset_dir(plan["table"], root),
            f"{raw_zone.PARTITION_KEY}={entry['partition']}",
        )
        found.update(objects_under(prefix))
    for run_id in plan["runs"]:
        manifest = remote.child(raw_zone.runs_dir(plan["table"], root), f"{run_id}.json")
        found.update(objects_under(manifest) if remote.is_remote(root) else _local_size(manifest))
    return found


def _local_size(path) -> dict[str, int]:
    item = Path(path)
    return {str(item): item.stat().st_size} if item.exists() else {}


def run(raw_root, keep: int, only: list[str], apply: bool) -> int:
    root = raw_root if raw_root is not None else raw_zone.raw_root()
    is_remote = remote.is_remote(root)
    snapshots = selected(only)

    print(f"prune superseded raw partitions\n  raw zone  {root}")
    print(f"  keeping   the newest {keep} partition(s) per dataset, proven or not")
    print(f"  mode      {'APPLY, deletes objects' if apply else 'report only, deletes nothing'}\n")

    plans = []
    with raw_zone.connect(root) as con:
        for name, cfg in sorted(snapshots.items()):
            plan = plan_for(con, name, cfg, root, keep)
            plans.append(plan)
            report(plan)

        # Resolved while the rows are still there, because the only way to know
        # which runs a partition holds is to read it.
        for plan in plans:
            partitions = [entry["partition"] for entry in plan["prunable"]]
            plan["runs"] = (
                runs_wholly_within(con, plan["table"], partitions, root) if partitions else []
            )

    removed_bytes = 0
    removed_objects = 0
    for plan in plans:
        found = paths_to_remove(plan, root)
        removed_objects += len(found)
        removed_bytes += sum(found.values())
        if apply:
            for path in sorted(found):
                print(f"  removing {path}")
            delete(sorted(found), is_remote)
            if not is_remote:
                for entry in plan["prunable"]:
                    shutil.rmtree(
                        Path(raw_zone.dataset_dir(plan["table"], root))
                        / f"{raw_zone.PARTITION_KEY}={entry['partition']}",
                        ignore_errors=True,
                    )

    unproven = [entry for plan in plans for entry in plan["unproven"]]
    deltas = sorted(set(dataset_registry.DATASETS) - set(dataset_registry.snapshot_datasets()))
    verb = "removed" if apply else "would remove"
    print(
        f"\nsummary\n  {verb} {removed_objects} object(s), {removed_bytes / 1e6:.1f} MB\n"
        f"  {sum(len(plan['prunable']) for plan in plans)} partition(s) and "
        f"{sum(len(plan['runs']) for plan in plans)} run manifest(s)"
    )
    print(f"  delta sources are not considered: {', '.join(deltas)}")

    if unproven:
        print("\nERROR: partitions older than the keep window that nothing supersedes:")
        for entry in unproven:
            print(
                f"  {entry['table']} ingest_date={entry['partition']}: "
                f"{entry['unreachable']} grain_key(s) are in it and not in {entry['keeper']}, "
                f"{entry['regressed']} are in both but newer here"
            )
        print(
            "\nNothing about them was deleted. Either the run that wrote one was not a "
            "complete snapshot, in which case the zone is correct and this partition is "
            "simply not prunable, or the dataset is not the current-state registry the "
            "registry says it is, in which case `refresh` is wrong. Deleting it on the "
            "strength of the registry alone is what this refuses to do."
        )
        return UNPROVEN_EXIT

    if not apply:
        print("\n  Nothing was deleted. Re-run with --apply.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete raw partitions a later one provably supersedes (ADR-14, PLAN-9)."
    )
    parser.add_argument(
        "datasets",
        nargs="*",
        help="dataset names to consider (default: every refresh: snapshot dataset)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete. Default is to report and delete nothing, because the default "
        "of a tool that removes the record cannot be to remove the record.",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=f"partitions to retain per dataset regardless of proof (default: {DEFAULT_KEEP})",
    )
    parser.add_argument(
        "--raw-root",
        type=remote.zone_root,
        default=None,
        help="root of the raw zone: a directory or a gs:// prefix "
        "(default: $RAW_ZONE_DIR, else $RAW_ZONE_URI, else data/raw)",
    )
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least 1: the newest partition is never a candidate")

    sys.exit(run(args.raw_root, args.keep, args.datasets, args.apply))


if __name__ == "__main__":
    main()
