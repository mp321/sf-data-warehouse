"""What `check_snapshots.py` calls short, and what it deliberately lets pass.

`shortfalls` is a pure function of a list of (partition, key count) pairs,
which is what lets the decision be tested without a zone, and it is the part
worth testing: the guard's whole value is where it draws the line between a
day's ordinary movement and a run that died mid-fetch. Drawn too tight it
fires on the city withdrawing nineteen registrations and gets switched off;
drawn too loose it passes the partial partition it exists to catch.

The numbers here are the measured ones from the zone on 2026-09-07, recorded
in ADR-19: `business_locations` held 365,307 to 366,438 distinct grain_keys
across the partitions of 2026-08-14 to 2026-09-05, growing by tens of keys a
day and shrinking by 6 to 22 when the city withdrew records.
"""

import subprocess
from pathlib import Path

import check_snapshots
import raw_zone

REPO_ROOT = Path(__file__).resolve().parent.parent
TOLERANCE = check_snapshots.SHORTFALL_TOLERANCE


# ---------------------------------------------------------------------------
# The movement that is not a defect
# ---------------------------------------------------------------------------


def test_a_growing_snapshot_is_silent():
    counts = [("2026-09-03", 366307), ("2026-09-04", 366389), ("2026-09-05", 366438)]
    assert check_snapshots.shortfalls(counts, TOLERANCE) == []


def test_withdrawn_registrations_are_not_a_shortfall():
    """The measured case ADR-19 is about, and the one that must stay quiet.

    22 keys off 365,679 is 0.006%. A guard that fired here would be red on
    the zone as it actually is, every day, which is how a guard stops being
    read.
    """
    counts = [("2026-08-22", 365679), ("2026-08-23", 365657)]
    assert check_snapshots.shortfalls(counts, TOLERANCE) == []


def test_a_single_partition_has_nothing_to_compare_against():
    assert check_snapshots.shortfalls([("2026-07-31", 41)], TOLERANCE) == []


def test_an_empty_dataset_is_not_a_shortfall():
    assert check_snapshots.shortfalls([], TOLERANCE) == []


# ---------------------------------------------------------------------------
# The run that died mid-fetch
# ---------------------------------------------------------------------------


def test_a_partition_that_lost_half_the_dataset_is_short():
    counts = [("2026-09-05", 366438), ("2026-09-06", 180000)]
    found = check_snapshots.shortfalls(counts, TOLERANCE)
    assert len(found) == 1
    assert found[0]["partition"] == "2026-09-06"
    assert found[0]["previous"] == "2026-09-05"
    assert found[0]["keys"] == 180000
    assert found[0]["previous_keys"] == 366438
    assert 0.50 < found[0]["drop"] < 0.51


def test_a_partition_holding_one_flush_is_short():
    """A run killed after its first flush: 50,000 rows of a 366,000-key registry."""
    counts = [("2026-09-05", 366438), ("2026-09-06", 50000)]
    assert len(check_snapshots.shortfalls(counts, TOLERANCE)) == 1


# ---------------------------------------------------------------------------
# Comparing against the predecessor rather than the maximum
# ---------------------------------------------------------------------------


def test_the_partition_after_a_short_one_is_not_accused():
    """One mid-fetch death reports once.

    Compared against the largest partition seen, the complete partition that
    follows a short one would look fine and the short one would look short,
    which is the same answer; compared against the maximum, a *recovery* after
    a short partition is fine too. What this pins is the third case: the short
    partition names itself and the complete one after it does not inherit the
    accusation, so the report names the run that died.
    """
    counts = [("2026-09-05", 366438), ("2026-09-06", 50000), ("2026-09-07", 366500)]
    found = check_snapshots.shortfalls(counts, TOLERANCE)
    assert [entry["partition"] for entry in found] == ["2026-09-06"]


def test_two_short_partitions_both_report():
    counts = [("2026-09-05", 366438), ("2026-09-06", 50000), ("2026-09-07", 10000)]
    found = check_snapshots.shortfalls(counts, TOLERANCE)
    assert [entry["partition"] for entry in found] == ["2026-09-06", "2026-09-07"]


# ---------------------------------------------------------------------------
# The tolerance boundary
# ---------------------------------------------------------------------------


def test_a_drop_inside_the_tolerance_passes():
    counts = [("2026-09-05", 100000), ("2026-09-06", 98500)]
    assert check_snapshots.shortfalls(counts, TOLERANCE) == []


def test_a_drop_past_the_tolerance_fails():
    counts = [("2026-09-05", 100000), ("2026-09-06", 97000)]
    assert len(check_snapshots.shortfalls(counts, TOLERANCE)) == 1


def test_a_drop_of_exactly_the_tolerance_passes():
    """> and not >=, so the constant names the first value that is not allowed."""
    counts = [("2026-09-05", 100000), ("2026-09-06", 98000)]
    assert check_snapshots.shortfalls(counts, TOLERANCE) == []


def test_a_predecessor_of_zero_keys_is_skipped_rather_than_dividing():
    counts = [("2026-09-05", 0), ("2026-09-06", 0)]
    assert check_snapshots.shortfalls(counts, TOLERANCE) == []


# ---------------------------------------------------------------------------
# Only snapshot datasets are considered
# ---------------------------------------------------------------------------


def test_a_delta_dataset_is_refused_by_name():
    """`311_cases` is an event log: a partition is meant to be smaller."""
    try:
        check_snapshots.selected(["311_cases"])
    except SystemExit as exc:
        assert "delta" in str(exc)
    else:
        raise AssertionError("a delta dataset should be refused")


def test_an_unknown_dataset_is_refused():
    try:
        check_snapshots.selected(["street_trees"])
    except SystemExit as exc:
        assert "registry" in str(exc)
    else:
        raise AssertionError("an unregistered dataset should be refused")


def test_the_default_is_every_snapshot_dataset():
    chosen = check_snapshots.selected([])
    assert "business_locations" in chosen
    assert "311_cases" not in chosen
    assert all(cfg["refresh"] == "snapshot" for cfg in chosen.values())


# ---------------------------------------------------------------------------
# The exit code, over a real zone
#
# Everything above tests `shortfalls`, which is the decision. These test the
# exit code, which is the contract `make build` now rests on (PLAN-10 step 2,
# ADR-19 step 4's first line). The two can disagree: a guard that reaches the
# right verdict and returns 0 anyway leaves the build unrefused, and no test
# above would notice. They build a real Parquet zone in `tmp_path` through
# `raw_zone.write_batch` for test_prune_raw.py's reason, that the thing under
# test is a query over the zone layout and a mock of that layout would be a
# second copy of the assumption being checked.
# ---------------------------------------------------------------------------

TABLE = "raw_business_locations"
DATASET = "business_locations"
GRAIN = "uniqueid"


def partition_keys(count: int, start: int = 0) -> list[str]:
    return [f"key-{n:06d}" for n in range(start, start + count)]


def write_partition(root, partition: str, keys: list[str], *, seq: int = 0, run: str = "") -> None:
    run_id = f"{partition.replace('-', '')}T0900{seq:02d}Z{run}"
    rows = [
        {
            GRAIN: key,
            raw_zone.WATERMARK_COLUMN: f"{partition}T00:00:00.000Z",
            raw_zone.RUN_ID_COLUMN: run_id,
        }
        for key in keys
    ]
    raw_zone.write_batch(TABLE, rows, run_id, seq, ingest_date=partition, root=root)


def test_a_zone_holding_a_short_partition_exits_short(tmp_path):
    write_partition(tmp_path, "2026-09-05", partition_keys(200))
    write_partition(tmp_path, "2026-09-06", partition_keys(40))
    assert check_snapshots.check(tmp_path, [DATASET], TOLERANCE) == check_snapshots.SHORT_EXIT


def test_a_zone_whose_partitions_each_hold_the_dataset_exits_zero(tmp_path):
    write_partition(tmp_path, "2026-09-05", partition_keys(200))
    write_partition(tmp_path, "2026-09-06", partition_keys(201))
    assert check_snapshots.check(tmp_path, [DATASET], TOLERANCE) == 0


def test_a_dataset_with_nothing_in_the_zone_is_skipped_rather_than_short(tmp_path):
    """An empty zone must not refuse a build. SKIP is a verdict, not a shortfall."""
    assert check_snapshots.check(tmp_path, [DATASET], TOLERANCE) == 0


def test_two_runs_on_one_day_are_one_partition_and_not_a_collapse(tmp_path):
    """ADR-19's 2026-08-15, end to end rather than as a count.

    Two runs on one day write two files into one partition and double its
    rows while its distinct key count is unchanged. A row-based guard would
    read 200, then 400, then 201 and call the third partition a 50% collapse,
    refusing every build until someone raised the tolerance. The pure-function
    tests above cannot show this, because they are handed key counts.
    """
    write_partition(tmp_path, "2026-09-05", partition_keys(200))
    write_partition(tmp_path, "2026-09-06", partition_keys(200), seq=0)
    write_partition(tmp_path, "2026-09-06", partition_keys(200), seq=1)
    write_partition(tmp_path, "2026-09-07", partition_keys(201))
    assert check_snapshots.check(tmp_path, [DATASET], TOLERANCE) == 0


# ---------------------------------------------------------------------------
# What the guard now gates
#
# PLAN-10 step 2. `check_derived.py` and this one are the two checks that can
# refuse a build, and the difference between a check that gates and one that
# reports is a single entry in a Makefile variable. Nothing else in the suite
# can see that entry, so removing it would be silent: every test above would
# still pass while a short partition built clean. `make --dry-run` asks make
# what it would run without running any of it, which is the cheapest honest
# question available here.
# ---------------------------------------------------------------------------


def dry_run(target: str, *overrides: str) -> str:
    """What `make <target>` would run, without running it."""
    done = subprocess.run(
        ["make", "--dry-run", "--no-print-directory", target, *overrides],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def test_make_build_runs_the_completeness_guard_before_dbt():
    recipe = dry_run("build")
    assert "check_snapshots.py --strict" in recipe
    assert recipe.index("check_snapshots.py") < recipe.index("dbt build")


def test_make_build_still_runs_the_derived_check():
    """The guard is an addition to BUILD_PREREQS and not a replacement of it."""
    recipe = dry_run("build")
    assert "check_derived.py --strict" in recipe


def test_each_gate_has_its_own_escape_hatch():
    """Turning one off must not turn the other off.

    One variable for both would mean `DERIVED_CHECK=0`, which CLAUDE.md
    documents for building against a knowingly stale derived zone, silently
    dropped the snapshot guard as well.
    """
    without_snapshots = dry_run("build", "SNAPSHOT_CHECK=0")
    assert "check_snapshots.py" not in without_snapshots
    assert "check_derived.py --strict" in without_snapshots

    without_derived = dry_run("build", "DERIVED_CHECK=0")
    assert "check_derived.py" not in without_derived
    assert "check_snapshots.py --strict" in without_derived
