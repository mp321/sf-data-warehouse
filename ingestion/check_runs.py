"""Does the raw zone hold the rows its run manifests claim it holds?

`ingest.py` writes one manifest per run under `<table>/_runs/<run_id>.json`,
and `load.py` materialises all of them as `raw_ingest_runs`, which
`mart_pipeline_freshness` reads. Nothing asserted that a manifest describes
the Parquet beside it. This does (PLAN-7 step 1).

The manifests are the only record of a run that fetched nothing. A run that
finds no new rows writes no Parquet file, so the data alone cannot tell
"ingestion ran and found nothing" from "ingestion has not run in three days",
and those mean opposite things to whoever is reading freshness. That is what
makes the manifests worth checking rather than deriving: everything else in
this pipeline can be recomputed from the zone, and this cannot.

Two distinct answers, because they have different causes and different
consequences:

  MISCOUNTED  a manifest and the rows carrying its run id disagree in number,
              or the rows are in a partition the manifest does not name. A
              number `mart_pipeline_freshness` reports is wrong now. Exits 3.
  UNRECORDED  rows carry a run id no manifest describes. The run died between
              its last write and its manifest, or a manifest was deleted.
              Freshness undercounts, and the run that advanced the zone's
              watermark left nothing saying it ran. Exits 4.

Both print in full before either exits. The exit code is MISCOUNTED's when
both are present, because a wrong number is a stronger statement about the
zone than a missing one.

**Mismatches are errors and not warnings, and the plan expected the opposite.**
PLAN-7 step 1 argued that a run interrupted by a network failure is a
legitimate state that should not wedge the pipeline. It is legitimate and it
does not fire this check: `_flush` increments `rows_written` and
`files_written` as it writes each file, and `_finish` writes the manifest on
the failure path too, so a run that died mid-fetch claims exactly what it
durably wrote. A `status: failed` manifest whose numbers reconcile is a
correctly recorded incident, not a defect, and is not reported here; whether
the newest run failed is `mart_pipeline_freshness`'s question. What is left
when the honest partial run is excluded is a zone that has been edited, which
the append-only rule (ADR-4) says cannot happen. So: error.

Two states are legitimate and deliberately silent:

  - A manifest claiming zero rows with no Parquet behind it. That is the
    "ran, found nothing new" case above, and it is the commonest manifest in
    a healthy zone.
  - A watermark in a manifest ahead of the newest `_socrata_updated_at` in
    the data. `watermark_out` advances per page and rows land per flush, so a
    run killed between the two claims a watermark it did not durably write.
    It costs nothing, because `resolve_watermark` resumes from the data
    (`raw_zone.read_watermark`) and never from a manifest. Warned about,
    never fatal.

**A separate script from `check_derived.py`, and that was the open question.**
PLAN-5 step 9 put the code stamp into `check_derived.py` rather than beside
it, and the precedent does not transfer. That was a third record in the same
manifest, in the same zone, read by the same reader, answering the same
question: is the derived zone still what its inputs say it should be. These
manifests are `ingest.py`'s, they are in the other zone, and the question is
whether one zone agrees with itself. Folding them in would make
`check_derived.py` about two zones' manifests, and its exit codes would stop
meaning "the derived zone is not usable". They also run at different moments:
`check-derived` gates `make build`, because what it catches makes a build
wrong, and this one does not gate anything, because a miscounted manifest
makes a report wrong and a build correct.

It lives in `ingestion/` rather than `scripts/`, which is where PLAN-7 step 1
said to put it, written before `check_derived.py` grew a third verdict. Every
property that matters here is that file's: it reads zones and not a
warehouse, it needs no credentials, it imports its siblings directly, and it
runs inside `make ci-build`. `scripts/` is the credentialed, run-by-hand half
(`parity-check.py`) or shell (`leak-check.sh`). The dev note for 2026-08-05
records the departure.

Deliberately reads the zone and not `raw_ingest_runs`. PLAN-7's own
constraint is that these checks read the zone rather than a copy of it, and
the warehouse copy assumes `load.py` did its job, which is part of what is
being checked. It costs no duplicated parsing: `raw_zone.runs_read_sql` is
the one reader of the manifests and `load.py` builds its table from the same
call.

Usage:
    python ingestion/check_runs.py                # report, exit 0
    python ingestion/check_runs.py --strict       # exit nonzero on a mismatch
Optional environment variables:
    RAW_ZONE_DIR      root of the raw zone (default: data/raw)
"""

import argparse
import sys
from pathlib import Path

import duckdb

import dataset_registry
import raw_zone
import remote

# Exit codes under --strict. Distinct from 1, and from each other, so a
# Makefile or a CI step can tell "a manifest is wrong about the zone" and
# "rows nothing claims to have written" apart from "this script broke". The
# numbering mirrors check_derived.py's for the same reason it has one.
MISCOUNTED_EXIT = 3
UNRECORDED_EXIT = 4

NO_RUN_ID = "(no run id)"


def claimed_runs(con, root: Path | str | None) -> dict[tuple[str, str], dict]:
    """Every run manifest in the zone, keyed by (table, run_id).

    Keyed by both because a run id is unique per table and not globally: it is
    a UTC timestamp at second resolution (`raw_zone.new_run_id`), and one
    `ingest.py --all` can start two datasets inside the same second.

    Returns an empty dict for a zone with no manifests at all rather than
    raising. That is a zone `ingest.py` has never written, which is a fresh
    clone rather than a defect, and the caller says so.
    """
    try:
        rows = con.execute(
            "select table_name, run_id, ingest_date, rows_written, files_written, "
            f"status, watermark_out from {raw_zone.runs_read_sql(root)}"
        ).fetchall()
    except duckdb.IOException:
        # read_json raises on a glob that matches nothing, which is the empty
        # zone. Narrow on purpose: a malformed manifest raises something else
        # and should still be an error rather than an empty result.
        return {}

    return {
        (table, run_id): {
            "ingest_date": ingest_date,
            "rows": rows_written,
            "files": files_written,
            "status": status,
            "watermark": watermark_out,
        }
        for table, run_id, ingest_date, rows_written, files_written, status, watermark_out in rows
    }


def present_runs(con, table: str, root: Path | str | None) -> dict[tuple[str, str], dict]:
    """What the Parquet holds per run for one table, keyed the same way.

    Grouped by `_ingest_run_id`, which `normalize_record` stamps on every row,
    rather than by `ingest_date` as PLAN-7 step 1 proposed. Two runs of one
    dataset on one day share a partition, and comparing partition totals lets
    their errors cancel; the run id is the grain the manifest is written at.
    The partition is carried alongside so the manifest's `ingest_date` can be
    checked too, which is the weaker comparison for free.

    Counts come out of the Parquet footers, like `raw_zone.partition_state`,
    so this is a listing rather than a scan.
    """
    if not raw_zone.has_data(table, root):
        return {}

    rows = con.execute(
        f"select {raw_zone.RUN_ID_COLUMN}, {raw_zone.PARTITION_KEY}, "
        "count(*), count(distinct filename) "
        f"from {raw_zone.read_sql(table, root, filename=True)} group by 1, 2"
    ).fetchall()

    state: dict[tuple[str, str], dict] = {}
    for run_id, partition, row_count, file_count in rows:
        # A null run id means a file written by something that is not
        # ingest.py. It cannot match a manifest, so it reports as unrecorded
        # under a name that reads as one rather than as "None".
        entry = state.setdefault(
            (table, run_id or NO_RUN_ID), {"rows": 0, "files": 0, "partitions": set()}
        )
        entry["rows"] += row_count
        entry["files"] += file_count
        entry["partitions"].add(str(partition))
    return state


def reconcile(claimed: dict, present: dict) -> tuple[list[str], list[str]]:
    """(miscounted, unrecorded) descriptions, comparing manifests against rows.

    Pure, so the decisions can be tested without a zone. Every line it
    produces names the table, the run and both numbers, because a check that
    says "mismatch" and stops is one nobody trusts at 2am.
    """
    miscounted: list[str] = []
    unrecorded: list[str] = []

    for (table, run_id), says in sorted(claimed.items()):
        # A failed run that reconciles is not reported at all, so `status` only
        # ever appears next to a defect. It is worth carrying that far: it is
        # the difference between "a run wrote the wrong number" and "a run died
        # and this is what it left", which is the first thing the reader would
        # otherwise go and look up.
        died = "" if says["status"] == "success" else f", and recorded status: {says['status']}"
        held = present.get((table, run_id))
        if held is None:
            if says["rows"] == 0:
                # Ran, found nothing new, wrote no file. The case the
                # manifests exist to record.
                continue
            miscounted.append(
                f"{table} run {run_id}: manifest claims {says['rows']} row(s) in "
                f"{says['files']} file(s), the zone holds none{died}"
            )
            continue

        if held["rows"] != says["rows"] or held["files"] != says["files"]:
            miscounted.append(
                f"{table} run {run_id}: manifest claims {says['rows']} row(s) in "
                f"{says['files']} file(s), the zone holds {held['rows']} row(s) in "
                f"{held['files']} file(s){died}"
            )
            continue

        partitions = sorted(held["partitions"])
        if partitions != [says["ingest_date"]]:
            miscounted.append(
                f"{table} run {run_id}: manifest names ingest_date={says['ingest_date']}, "
                f"its {held['rows']} row(s) are under {', '.join(partitions)}{died}"
            )

    for (table, run_id), held in sorted(present.items()):
        if (table, run_id) in claimed:
            continue
        unrecorded.append(
            f"{table} run {run_id}: {held['rows']} row(s) in {held['files']} file(s) under "
            f"ingest_date={', '.join(sorted(held['partitions']))}, described by no manifest"
        )

    return miscounted, unrecorded


def watermark_drift(claimed: dict, table: str, in_zone: str | None) -> str | None:
    """A description when the newest claimed watermark is not the newest in the data.

    Lexical max, which is `raw_zone.read_watermark`'s assumption and correct
    for the same reason: Socrata renders `:updated_at` as a fixed-width UTC
    instant, so string order is time order.
    """
    claims = [
        says["watermark"]
        for (claimed_table, _), says in claimed.items()
        if claimed_table == table and says.get("watermark")
    ]
    if not claims or in_zone is None:
        return None
    newest = max(claims)
    if newest == in_zone:
        return None
    if newest > in_zone:
        return (
            f"{table}: the newest manifest claims a watermark of {newest}, the newest row in "
            f"the zone is {in_zone}. A run advanced its watermark past rows it did not write."
        )
    return (
        f"{table}: the zone holds rows updated to {in_zone}, no manifest claims past {newest}. "
        "Rows arrived that no recorded run fetched."
    )


def check(raw_root: Path | str | None) -> int:
    """Report on the run manifests. Returns a nonzero exit code on a mismatch."""
    root = raw_root if raw_root is not None else raw_zone.raw_root()
    print(f"run manifests against the rows they claim\n  raw zone  {root}\n")

    with raw_zone.connect(root) as con:
        claimed = claimed_runs(con, root)
        if not claimed:
            print("no run manifests in the zone. `make ingest` has not run against it.")
            return 0

        registered = {cfg["table"] for cfg in dataset_registry.DATASETS.values()}
        found = {table for table, _ in claimed}
        unregistered = sorted(found - registered)

        miscounted: list[str] = []
        unrecorded: list[str] = []
        drifted: list[str] = []

        for table in sorted(registered | found):
            for_table = {key: says for key, says in claimed.items() if key[0] == table}
            present = present_runs(con, table, root)
            if not for_table and not present:
                print(f"{table:36s} SKIP nothing in the zone")
                continue

            table_miscounted, table_unrecorded = reconcile(for_table, present)
            drift = watermark_drift(for_table, table, raw_zone.read_watermark(table, root))

            miscounted.extend(table_miscounted)
            unrecorded.extend(table_unrecorded)
            if drift:
                drifted.append(drift)

            rows = sum(held["rows"] for held in present.values())
            problems = len(table_miscounted) + len(table_unrecorded)
            verdict = f"FAIL {problems} run(s) disagree" if problems else "PASS"
            note = " (not in the registry)" if table in unregistered else ""
            print(f"{table:36s} {verdict:24s} {len(for_table)} run(s), {rows} row(s){note}")

    failing = len(miscounted) + len(unrecorded)
    print(f"\nsummary\n  {len(claimed)} manifest(s) checked, {failing} failing")

    for line in drifted:
        print(f"  WARNING: {line}")
    # This warning is load bearing and must not be softened into silence
    # (ADR-16). It is the only thing in the project that notices a dataset an
    # ADR has cut whose Parquet is still being paid for: `city_budget` and
    # `street_trees` sat in the bucket for five days after ADR-10, 55.5 MB, with
    # this line printing on every run. The answer is a scope deletion by hand,
    # not a prune, because `prune_raw.py` has no `refresh` or `grain_key` for a
    # dataset the registry does not describe and exits on the name.
    for table in unregistered:
        print(
            f"  WARNING: {table} is in the zone and not in the registry, so nothing reads it. "
            "Reconciled anyway. Delete the prefix by hand if the cut is final; see ADR-16."
        )

    if miscounted:
        print("\nERROR: a run manifest disagrees with the rows in the zone:")
        for line in miscounted:
            print(f"  {line}")
        print(
            "\nThe raw zone is append-only (ADR-4), so this is not drift: either a write did "
            "not land or something edited the zone. mart_pipeline_freshness is reporting the "
            "manifest's number, which is the wrong one."
        )

    if unrecorded:
        print("\nERROR: rows in the zone that no run manifest describes:")
        for line in unrecorded:
            print(f"  {line}")
        print(
            "\nA run wrote them and did not record itself, so freshness undercounts and the "
            "watermark those rows advanced is written down nowhere. Re-running ingestion is "
            "safe and does not fix it: the manifest is gone, not stale."
        )

    if not miscounted and not unrecorded:
        print("  PASS every manifest agrees with the rows carrying its run id")
        return 0
    return MISCOUNTED_EXIT if miscounted else UNRECORDED_EXIT


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check the raw zone's run manifests against the Parquet they describe."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"exit {MISCOUNTED_EXIT} when a manifest disagrees with the zone and "
        f"{UNRECORDED_EXIT} when rows carry a run id no manifest describes "
        "(default: report and exit 0)",
    )
    parser.add_argument(
        "--raw-root",
        type=remote.zone_root,
        default=None,
        help="root of the raw zone: a directory or a gs:// prefix "
        "(default: $RAW_ZONE_DIR, else $RAW_ZONE_URI, else data/raw)",
    )
    args = parser.parse_args()

    status = check(args.raw_root)
    sys.exit(status if args.strict else 0)


if __name__ == "__main__":
    main()
