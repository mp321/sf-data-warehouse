"""What a run of `spatial.py` decides to rebuild, and when it refuses to skip.

`derived_state.plan_rebuild` is a pure function of three dicts, which makes it
the one part of PLAN-5 step 9 that can be tested without a zone. It is also the
part most worth testing: every branch here is a decision to *not* recompute
something, and a wrong one produces a derived zone that disagrees with the raw
zone and looks fine, which is the failure mode the whole step exists to avoid.

The end-to-end proof is a different thing and lives in the dev note for
2026-08-05: an incremental run and a full rebuild over the same raw zone,
compared row for row. This covers the decisions that lead into it.
"""

import pytest

import derived_state
import raw_zone
from derived_zone import CODE_VERSION_KEY, PARTITIONS_KEY, RAW_INPUTS_KEY

POINT = "raw_311_cases"
POLYGON = "raw_analysis_neighborhoods"

CODE = {
    "stamp": "aaaaaaaaaaaaaaaa",
    "resolutions": [8, 10],
    "membership_resolution": 10,
    "point_tables": [POINT],
    "polygon_tables": [POLYGON],
    "modules": {"spatial.py": "111111111111", "population.py": "222222222222"},
}


def partitions(point_rows: dict, polygon_rows: dict | None = None) -> dict:
    state = {POINT: {date: {"rows": rows, "files": 1} for date, rows in point_rows.items()}}
    state[POLYGON] = {
        date: {"rows": rows, "files": 1}
        for date, rows in (polygon_rows or {"2026-07-31": 41}).items()
    }
    return state


UNSET = object()


def manifest(state: dict, code=UNSET) -> dict:
    """A manifest of the shape spatial.py writes. `code=None` is a zone with no stamp."""
    return {
        "tables": [],
        CODE_VERSION_KEY: CODE if code is UNSET else code,
        RAW_INPUTS_KEY: {},
        PARTITIONS_KEY: state,
    }


# ---------------------------------------------------------------------------
# Nothing to do
# ---------------------------------------------------------------------------


def test_an_unchanged_zone_rebuilds_nothing():
    state = partitions({"2026-07-31": 100_000})
    plan = derived_state.plan_rebuild(manifest(state), state, CODE)
    assert plan.is_current
    assert not plan.is_full
    assert plan.points == {POINT: []}


def test_reuse_and_full_rebuild_are_not_the_same_empty():
    """`[]` means read no partitions, `None` means read all of them."""
    assert derived_state.Plan(rebuild_boundaries=False, points={POINT: []}).is_current
    assert not derived_state.Plan(rebuild_boundaries=False, points={POINT: None}).is_current


# ---------------------------------------------------------------------------
# Reasons to rebuild everything
# ---------------------------------------------------------------------------


def test_no_manifest_is_a_full_rebuild():
    plan = derived_state.plan_rebuild(None, partitions({"2026-07-31": 10}), CODE)
    assert plan.is_full
    assert plan.reasons == ["there is no derived zone to build on"]


def test_forced_reason_is_reported_verbatim():
    state = partitions({"2026-07-31": 10})
    plan = derived_state.plan_rebuild(manifest(state), state, CODE, "--full was passed")
    assert plan.is_full
    assert plan.reasons == ["--full was passed"]
    assert plan.points == {POINT: None}


def test_a_changed_code_stamp_rebuilds_everything():
    state = partitions({"2026-07-31": 10})
    old = {**CODE, "stamp": "bbbbbbbbbbbbbbbb", "resolutions": [8, 9, 10]}
    plan = derived_state.plan_rebuild(manifest(state, old), state, CODE)
    assert plan.is_full
    assert "resolutions: zone built for [8, 9, 10], code computes [8, 10]" in plan.reasons


def test_a_zone_with_no_recorded_code_version_rebuilds_everything():
    """The state every zone built before PLAN-5 step 9 is in."""
    state = partitions({"2026-07-31": 10})
    plan = derived_state.plan_rebuild(manifest(state, code=None), state, CODE)
    assert plan.is_full
    assert plan.reasons == [
        "the zone records no code version, so it was built before the stamp existed"
    ]


def test_a_zone_with_no_recorded_partitions_rebuilds_everything():
    state = partitions({"2026-07-31": 10})
    without = {key: value for key, value in manifest(state).items() if key != PARTITIONS_KEY}
    plan = derived_state.plan_rebuild(without, state, CODE)
    assert plan.is_full


def test_a_raw_table_that_disappeared_rebuilds_everything():
    """Otherwise its rows would sit in the derived zone with nothing to drop them."""
    recorded = partitions({"2026-07-31": 10})
    current = {POLYGON: recorded[POLYGON]}
    plan = derived_state.plan_rebuild(manifest(recorded), current, CODE)
    assert plan.is_full
    assert plan.reasons == [f"no raw data left for {POINT}"]


# ---------------------------------------------------------------------------
# Partial rebuilds
# ---------------------------------------------------------------------------


def test_a_new_daily_partition_is_read_and_nothing_else_is():
    recorded = partitions({"2026-07-31": 100_000})
    current = partitions({"2026-07-31": 100_000, "2026-08-05": 800})
    plan = derived_state.plan_rebuild(manifest(recorded), current, CODE)
    assert not plan.is_full
    assert not plan.rebuild_boundaries
    assert plan.points == {POINT: ["2026-08-05"]}


def test_a_partition_that_gained_a_file_is_read_again():
    """A second ingest on the same day appends to the partition it already has."""
    recorded = partitions({"2026-07-31": 100_000, "2026-08-04": 500})
    current = {
        POINT: {
            "2026-07-31": {"rows": 100_000, "files": 1},
            "2026-08-04": {"rows": 900, "files": 2},
        },
        POLYGON: recorded[POLYGON],
    }
    plan = derived_state.plan_rebuild(manifest(recorded), current, CODE)
    assert plan.points == {POINT: ["2026-08-04"]}


def test_a_single_partition_holding_the_whole_table_is_never_incremental():
    """The shape of a zone that has only ever been ingested once.

    Any change to it is a change to most of it, so the share rule sends it down
    the full path. Nothing is lost: reading the cache would have cost more than
    the recompute it was meant to save.
    """
    recorded = partitions({"2026-07-31": 100_000})
    current = partitions({"2026-07-31": 100_500})
    plan = derived_state.plan_rebuild(manifest(recorded), current, CODE)
    assert plan.points == {POINT: None}


def test_a_backfill_bigger_than_the_cache_rebuilds_the_table_instead():
    """Reading the cache costs what recomputing costs; see INCREMENTAL_SHARE."""
    recorded = partitions({"2026-07-31": 100_000})
    current = partitions({"2026-07-31": 100_000, "2026-08-05": 400_000})
    plan = derived_state.plan_rebuild(manifest(recorded), current, CODE)
    assert plan.points == {POINT: None}
    assert not plan.is_full  # the table, not the zone
    assert not plan.rebuild_boundaries


def test_a_shrunken_partition_rebuilds_the_table():
    """Only a local --full-refresh can do this, and nothing before it survives."""
    recorded = partitions({"2026-07-31": 100_000})
    current = partitions({"2026-07-31": 90_000})
    plan = derived_state.plan_rebuild(manifest(recorded), current, CODE)
    assert plan.points == {POINT: None}


def test_a_vanished_partition_rebuilds_the_table():
    recorded = partitions({"2026-07-31": 100_000, "2026-08-01": 500})
    current = partitions({"2026-07-31": 100_000})
    plan = derived_state.plan_rebuild(manifest(recorded), current, CODE)
    assert plan.points == {POINT: None}


def test_a_table_never_seen_before_is_read_whole():
    recorded = {POLYGON: partitions({})[POLYGON]}
    current = partitions({"2026-07-31": 100_000})
    plan = derived_state.plan_rebuild(manifest(recorded), current, CODE)
    assert plan.points == {POINT: None}


def test_a_moved_boundary_set_rebuilds_boundaries_and_not_points():
    recorded = partitions({"2026-07-31": 100_000})
    current = partitions({"2026-07-31": 100_000}, {"2026-07-31": 41, "2026-08-05": 41})
    plan = derived_state.plan_rebuild(manifest(recorded), current, CODE)
    assert plan.rebuild_boundaries
    assert plan.points == {POINT: []}
    assert not plan.is_full


# ---------------------------------------------------------------------------
# The stamp itself
# ---------------------------------------------------------------------------


def test_the_stamp_covers_the_modules_that_decide_the_zone():
    version = derived_state.code_version()
    assert set(version["modules"]) == set(derived_state.STAMPED_MODULES)
    assert version == derived_state.code_version()  # stable within a process


def test_the_stamp_is_read_from_source_and_not_from_the_imported_module():
    """A checker has to answer on code it cannot import, so it reads bytes."""
    for name in derived_state.STAMPED_MODULES:
        assert (derived_state.MODULE_DIR / name).is_file()


def test_a_changed_module_is_named_rather_than_only_detected():
    current = derived_state.code_version()
    stale = {
        **current,
        "stamp": "not-the-current-stamp",
        "modules": {**current["modules"], "population.py": "0000deadbeef"},
    }
    assert derived_state.describe_code_change(stale, current) == [
        "changed since the zone was built: population.py"
    ]


def test_an_agreeing_stamp_reports_nothing():
    current = derived_state.code_version()
    assert derived_state.describe_code_change(current, current) == []


# ---------------------------------------------------------------------------
# The narrowed read
# ---------------------------------------------------------------------------


def test_a_narrowed_read_globs_only_the_partitions_it_was_given():
    sql = raw_zone.read_sql(POINT, "data/raw", partitions=["2026-08-05"])
    assert "ingest_date=2026-08-05/*.parquet" in sql
    assert "**" not in sql
    assert "hive_partitioning = true" in sql


def test_an_empty_partition_list_is_refused_rather_than_read_as_everything():
    with pytest.raises(ValueError, match="empty partition list"):
        raw_zone.read_sql(POINT, "data/raw", partitions=[])
