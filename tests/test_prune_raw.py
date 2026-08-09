"""What the prune will delete, and the four things it refuses to delete.

This is the only tool in the project that removes part of the record, so the
tests worth having are the ones that assert it does not. Every test below that
ends in an assertion about `proven` being False is a case where deleting would
have lost rows, and a wrong answer there is silent: the partition is gone, the
zone still looks well formed, and the loss surfaces as a number that quietly
moved.

These build a real Parquet zone in `tmp_path` through `raw_zone.write_batch`
rather than mocking one, because the thing under test is a query over the zone
layout, and a mock of that layout would be a second copy of the assumption
being checked. It costs about a tenth of a second.

The last block is the headroom check (ADR-17), and it is the opposite kind of
test: it asserts that the half of this tool which runs unattended in
`.github/workflows/retention.yml` reports and never deletes, whatever verdict it
reaches. The one to read is the precedence test. Over budget and unproven can
hold at once and they ask for opposite things, so the exit code has to be the
more serious of the two.

The end-to-end proof against the real bucket is in the dev notes for 2026-08-07
and 2026-08-09. This covers the decisions behind it.
"""

import json

import check_runs
import prune_raw
import pytest

import dataset_registry
import raw_zone

TABLE = "raw_business_locations"
DATASET = "business_locations"
GRAIN = "uniqueid"

OLD = "2026-08-01"
MID = "2026-08-02"
NEW = "2026-08-03"


def row(key: str, updated: str, run_id: str) -> dict:
    return {
        GRAIN: key,
        raw_zone.WATERMARK_COLUMN: updated,
        raw_zone.RUN_ID_COLUMN: run_id,
        "dba_name": f"business {key}",
    }


def write(root, partition: str, run_id: str, rows: list[dict], seq: int = 0) -> None:
    raw_zone.write_batch(TABLE, rows, run_id, seq, ingest_date=partition, root=root)


def snapshot(root, partition: str, keys: list[str], updated: str | None = None) -> str:
    """One run writing `keys` into `partition`, each row updated that day.

    Named for what it is meant to be rather than for what it does, because
    every test below is about whether a given partition really is one.
    """
    run_id = f"{partition.replace('-', '')}T090000Z"
    stamp = updated or f"{partition}T00:00:00.000Z"
    write(root, partition, run_id, [row(key, stamp, run_id) for key in keys])
    return run_id


def manifest(root, run_id: str, partition: str, rows: int, files: int = 1) -> None:
    raw_zone.write_run_manifest(
        TABLE,
        {
            "run_id": run_id,
            "dataset": DATASET,
            "table_name": TABLE,
            "ingest_date": partition,
            "started_at": f"{partition}T09:00:00",
            "finished_at": f"{partition}T09:01:00",
            "watermark_in": None,
            "watermark_out": "2026-08-03T00:00:00.000Z",
            "rows_written": rows,
            "files_written": files,
            "mode": "incremental",
            "status": "success",
            "error": None,
        },
        root=root,
    )


@pytest.fixture
def zone(tmp_path):
    """Two complete snapshots and one before them, the healthy shape."""
    snapshot(tmp_path, OLD, ["a", "b"])
    snapshot(tmp_path, MID, ["a", "b"])
    snapshot(tmp_path, NEW, ["a", "b", "c"])
    manifest(tmp_path, "20260801T090000Z", OLD, 2)
    manifest(tmp_path, "20260802T090000Z", MID, 2)
    manifest(tmp_path, "20260803T090000Z", NEW, 3)
    return tmp_path


def prove(root, candidate: str, keeper: str) -> dict:
    with raw_zone.connect(root) as con:
        return prune_raw.supersession(con, TABLE, GRAIN, candidate, keeper, root=root)


# ---------------------------------------------------------------------------
# The proof, and the three ways it fails
# ---------------------------------------------------------------------------


def test_a_later_complete_snapshot_supersedes_an_earlier_one(zone):
    result = prove(zone, OLD, NEW)
    assert result["proven"]
    assert result["unreachable"] == 0
    assert result["regressed"] == 0
    assert result["candidate_keys"] == 2


def test_a_key_the_later_partition_lacks_refuses(tmp_path):
    """The case the whole tool exists for: an incomplete later run.

    `refresh: snapshot` says a partition of this dataset CAN be complete, not
    that this one is. A run that fetched two changed rows writes a partition
    that looks exactly like a complete one from the outside, and deleting the
    earlier one on the strength of the registry would delete `b`.
    """
    snapshot(tmp_path, OLD, ["a", "b"])
    snapshot(tmp_path, NEW, ["a"])
    result = prove(tmp_path, OLD, NEW)
    assert not result["proven"]
    assert result["unreachable"] == 1


def test_a_key_present_later_at_an_older_value_refuses(tmp_path):
    """A superset that is behind on a key would change what staging returns.

    Staging deduplicates to the newest `_socrata_updated_at` per grain_key, so
    if the surviving partition holds an older value the model's answer moves
    when the earlier partition goes. The row count would not move, so the
    acceptance test for this tool cannot see it and this has to.
    """
    snapshot(tmp_path, OLD, ["a"], updated="2026-08-02T12:00:00.000Z")
    snapshot(tmp_path, NEW, ["a"], updated="2026-08-01T00:00:00.000Z")
    result = prove(tmp_path, OLD, NEW)
    assert not result["proven"]
    assert result["unreachable"] == 0
    assert result["regressed"] == 1


def test_a_null_grain_key_refuses(tmp_path):
    """A row staging cannot deduplicate by is a row this cannot reason about.

    It arrives here as an unmatched key rather than as a special case, which
    is the right outcome by accident and is asserted so it stays the outcome.
    """
    run_id = "20260801T090000Z"
    keyed = row("a", "2026-08-01T00:00:00.000Z", run_id)
    write(tmp_path, OLD, run_id, [keyed, keyed | {GRAIN: None}])
    snapshot(tmp_path, NEW, ["a"])
    result = prove(tmp_path, OLD, NEW)
    assert not result["proven"]
    assert result["unreachable"] == 1


# ---------------------------------------------------------------------------
# Which manifests go with the partitions
# ---------------------------------------------------------------------------


def test_a_run_wholly_inside_the_deleted_partitions_loses_its_manifest(zone):
    with raw_zone.connect(zone) as con:
        assert prune_raw.runs_wholly_within(con, TABLE, [OLD], zone) == ["20260801T090000Z"]


def test_a_run_spanning_two_partitions_keeps_its_manifest(tmp_path):
    """Its rows are not all being deleted, so its manifest would still be true.

    A run in two partitions is already something `check_runs.py` reports;
    deleting its manifest on the way past would turn one defect into two.
    """
    straddling = "20260801T235900Z"
    write(tmp_path, OLD, straddling, [row("a", "2026-08-01T00:00:00.000Z", straddling)])
    write(tmp_path, MID, straddling, [row("b", "2026-08-02T00:00:00.000Z", straddling)], seq=1)
    with raw_zone.connect(tmp_path) as con:
        assert prune_raw.runs_wholly_within(con, TABLE, [OLD], tmp_path) == []


def test_a_run_that_wrote_nothing_keeps_its_manifest(zone):
    """The one record in this zone that cannot be recomputed.

    A run that found nothing new writes no Parquet, so it appears in no
    partition and cannot be inside one. That is what lets
    mart_pipeline_freshness tell "ingestion ran and found nothing" from
    "ingestion has not run in three days".
    """
    manifest(zone, "20260801T210000Z", OLD, rows=0, files=0)
    with raw_zone.connect(zone) as con:
        assert "20260801T210000Z" not in prune_raw.runs_wholly_within(con, TABLE, [OLD], zone)


# ---------------------------------------------------------------------------
# End to end, including the reason step 4 exists
# ---------------------------------------------------------------------------


def test_apply_removes_the_partition_and_leaves_check_runs_clean(zone, capsys):
    """The acceptance criterion for PLAN-9 step 4, at fixture scale.

    Delete a partition and leave its manifest behind and `check_runs.py` exits
    3 MISCOUNTED on the next run, because the manifest claims rows the zone no
    longer holds. Pruning both is what keeps the zone agreeing with itself.
    """
    manifest(zone, "20260801T210000Z", OLD, rows=0, files=0)
    assert prune_raw.run(zone, keep=2, only=[DATASET], apply=True) == 0
    capsys.readouterr()

    assert not (zone / TABLE / f"{raw_zone.PARTITION_KEY}={OLD}").exists()
    assert not (zone / TABLE / raw_zone.RUNS_DIRNAME / "20260801T090000Z.json").exists()
    assert (zone / TABLE / raw_zone.RUNS_DIRNAME / "20260801T210000Z.json").exists()
    assert check_runs.check(zone) == 0


def test_a_dry_run_deletes_nothing(zone):
    assert prune_raw.run(zone, keep=2, only=[DATASET], apply=False) == 0
    assert (zone / TABLE / f"{raw_zone.PARTITION_KEY}={OLD}").exists()
    assert (zone / TABLE / raw_zone.RUNS_DIRNAME / "20260801T090000Z.json").exists()


def test_an_unprovable_partition_exits_nonzero_and_deletes_nothing(tmp_path, capsys):
    snapshot(tmp_path, OLD, ["a", "b"])
    snapshot(tmp_path, MID, ["a"])
    snapshot(tmp_path, NEW, ["a"])
    manifest(tmp_path, "20260801T090000Z", OLD, 2)

    assert prune_raw.run(tmp_path, keep=2, only=[DATASET], apply=True) == prune_raw.UNPROVEN_EXIT
    assert (tmp_path / TABLE / f"{raw_zone.PARTITION_KEY}={OLD}").exists()
    assert (tmp_path / TABLE / raw_zone.RUNS_DIRNAME / "20260801T090000Z.json").exists()
    assert "NOT superseded" in capsys.readouterr().out


def test_the_newest_partitions_are_never_candidates(zone, capsys):
    """`--keep` is applied before any proof, so a proof cannot override it.

    One rollback is worth about 50 MB on the real zone, and the keep window is
    what buys it. A partition inside it is not proven and rejected, it is not
    looked at.
    """
    assert prune_raw.run(zone, keep=3, only=[DATASET], apply=True) == 0
    for partition in (OLD, MID, NEW):
        assert (zone / TABLE / f"{raw_zone.PARTITION_KEY}={partition}").exists()


def test_a_delta_dataset_cannot_be_named(zone):
    """Not a warning and not a skip. Naming one is refused outright."""
    with pytest.raises(SystemExit, match="delta"):
        prune_raw.run(zone, keep=1, only=["311_cases"], apply=True)


def test_delta_datasets_are_absent_from_a_whole_zone_run():
    """The default run considers snapshot sources and no others."""
    considered = set(dataset_registry.snapshot_datasets())
    assert "311_cases" not in considered
    assert "building_permits" not in considered


def test_the_manifest_of_a_pruned_run_is_gone_from_the_zone_json(zone):
    """`load.py` builds raw_ingest_runs from the manifests, so this is the
    shape mart_pipeline_freshness sees afterwards: two runs, not three."""
    prune_raw.run(zone, keep=2, only=[DATASET], apply=True)
    remaining = sorted(
        json.loads(path.read_text())[0]["run_id"]
        for path in (zone / TABLE / raw_zone.RUNS_DIRNAME).glob("*.json")
    )
    assert remaining == ["20260802T090000Z", "20260803T090000Z"]


# ---------------------------------------------------------------------------
# The headroom check, which is the half that runs unattended (ADR-17)
# ---------------------------------------------------------------------------


def test_a_zone_under_the_threshold_passes_and_still_deletes_nothing(zone):
    assert prune_raw.run(zone, keep=2, only=[DATASET], apply=False, max_bytes=10**9) == 0
    assert (zone / TABLE / f"{raw_zone.PARTITION_KEY}={OLD}").exists()


def test_a_zone_over_the_threshold_fails_and_still_deletes_nothing(zone, capsys):
    """The whole point of the scheduled half: it reports and never deletes.

    A threshold of one byte is over by construction, which is the cheap way to
    assert the exit code without building a zone of a particular size.
    """
    code = prune_raw.run(zone, keep=2, only=[DATASET], apply=False, max_bytes=1)
    assert code == prune_raw.OVER_BUDGET_EXIT
    assert (zone / TABLE / f"{raw_zone.PARTITION_KEY}={OLD}").exists()
    assert "prune-raw-apply" in capsys.readouterr().out


def test_an_unproven_partition_outranks_being_over_budget(tmp_path, capsys):
    """Both conditions at once, and the more serious one is the exit code.

    Over budget asks the reader to run the apply. Unproven says a snapshot
    dataset failed its proof, which ADR-14 reads as `refresh` having become a
    lie and answers with "stop pruning until it is understood". Returning 4
    here would invite the action 3 forbids.
    """
    snapshot(tmp_path, OLD, ["a", "b"])
    snapshot(tmp_path, MID, ["a"])
    snapshot(tmp_path, NEW, ["a"])

    code = prune_raw.run(tmp_path, keep=2, only=[DATASET], apply=False, max_bytes=1)
    assert code == prune_raw.UNPROVEN_EXIT
    assert "NOT superseded" in capsys.readouterr().out


def test_the_headroom_line_says_what_an_apply_would_leave(zone, capsys):
    """Report mode subtracts the plan, because that is the actionable number.

    The zone is over the threshold now; the question the reader has is whether
    running the apply would fix it, and that is the difference between two
    numbers on the same line rather than an arithmetic exercise.
    """
    prune_raw.run(zone, keep=2, only=[DATASET], apply=False, max_bytes=1)
    out = capsys.readouterr().out
    total, _ = prune_raw.zone_bytes(zone)
    assert f"zone      {total / 1e6:.1f} MB" in out
    assert "after a full apply of this plan" in out


def test_apply_weighs_the_zone_it_leaves_behind(zone, capsys):
    """With --apply the deletes have happened, so nothing is left to subtract.

    Reporting the pre-delete total here would fail a threshold the run had just
    brought the zone under, which is the one way this check could cry wolf at
    the exact moment someone did the right thing.
    """
    prune_raw.run(zone, keep=2, only=[DATASET], apply=True, max_bytes=10**9)
    out = capsys.readouterr().out
    total, _ = prune_raw.zone_bytes(zone)
    assert f"zone      {total / 1e6:.1f} MB" in out
    assert f"after a full apply of this plan, {total / 1e6:.1f} MB" in out


def test_zone_bytes_counts_datasets_the_registry_does_not_describe(zone):
    """The bill does not care whether the registry knows about a prefix.

    A dataset ADR-10 cut is invisible to every other part of this tool and is
    still storage; `raw_city_budget` and `raw_street_trees` were 55.5 MB of the
    real zone on exactly that basis. `check_runs.py` is what names them.
    """
    before, objects_before = prune_raw.zone_bytes(zone)
    stray = zone / "raw_city_budget" / f"{raw_zone.PARTITION_KEY}={OLD}"
    stray.mkdir(parents=True)
    (stray / "part-0000.parquet").write_bytes(b"x" * 4096)

    after, objects_after = prune_raw.zone_bytes(zone)
    assert after == before + 4096
    assert objects_after == objects_before + 1
