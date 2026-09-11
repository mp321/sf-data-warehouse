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

import check_snapshots

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
