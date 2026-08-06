"""What `check_runs.py` calls a defect, and what it deliberately lets pass.

`reconcile` and `watermark_drift` are pure functions of two dicts, which is
what lets the decisions be tested without a zone. They are also the part worth
testing: the two branches that stay silent are decisions to not report
something, and a wrong one turns the check into either a liar or a nuisance.
A check that fires on a healthy zone gets `DERIVED_CHECK=0`'d out of the way
and stops protecting anything.

The end-to-end proof is a different thing and is in the dev note for
2026-08-05: a fixture zone with a manifest edited, a manifest deleted and a
Parquet file deleted, exiting 3, 4 and 3. This covers the decisions behind it.
"""

import check_runs

TABLE = "raw_311_cases"
OTHER = "raw_film_locations"
RUN = "20260806T051924Z"
LATER = "20260806T091700Z"
DAY = "2026-08-06"


def claim(rows: int, files: int = 1, ingest_date: str = DAY, watermark: str | None = None) -> dict:
    return {
        "ingest_date": ingest_date,
        "rows": rows,
        "files": files,
        "status": "success",
        "watermark": watermark,
    }


def held(rows: int, files: int = 1, partitions: tuple[str, ...] = (DAY,)) -> dict:
    return {"rows": rows, "files": files, "partitions": set(partitions)}


# ---------------------------------------------------------------------------
# Agreement, and the two disagreements that are not defects
# ---------------------------------------------------------------------------


def test_a_run_that_wrote_what_it_claims_is_silent():
    miscounted, unrecorded = check_runs.reconcile(
        {(TABLE, RUN): claim(26)}, {(TABLE, RUN): held(26)}
    )
    assert not miscounted
    assert not unrecorded


def test_a_run_that_found_nothing_new_is_not_a_missing_file():
    """The commonest manifest in a healthy zone: it wrote no Parquet on purpose.

    This is the whole reason the manifests exist. Reporting it would make the
    check red on every zone that has ever been ingested twice.
    """
    miscounted, unrecorded = check_runs.reconcile({(TABLE, RUN): claim(0, files=0)}, {})
    assert not miscounted
    assert not unrecorded


def test_a_failed_run_whose_numbers_reconcile_is_not_reported():
    """`_flush` counts as it writes, so a run that died mid-fetch claims what it wrote.

    Whether the newest run failed is `mart_pipeline_freshness`'s question. This
    one is whether the manifest describes the zone, and here it does.
    """
    partial = claim(26) | {"status": "failed"}
    miscounted, unrecorded = check_runs.reconcile({(TABLE, RUN): partial}, {(TABLE, RUN): held(26)})
    assert not miscounted
    assert not unrecorded


# ---------------------------------------------------------------------------
# Miscounted
# ---------------------------------------------------------------------------


def test_a_row_count_mismatch_names_the_table_the_run_and_both_numbers():
    miscounted, _ = check_runs.reconcile({(TABLE, RUN): claim(999)}, {(TABLE, RUN): held(26)})
    assert len(miscounted) == 1
    line = miscounted[0]
    assert TABLE in line
    assert RUN in line
    assert "999" in line
    assert "26" in line


def test_a_file_count_mismatch_is_a_defect_even_when_the_rows_agree():
    """Two files' worth of rows in one file means a file was rewritten.

    The zone is append-only (ADR-4), so nothing legitimate produces it, and a
    row count alone cannot see it.
    """
    miscounted, _ = check_runs.reconcile(
        {(TABLE, RUN): claim(50, files=2)}, {(TABLE, RUN): held(50, files=1)}
    )
    assert len(miscounted) == 1
    assert "2 file(s)" in miscounted[0]


def test_a_miscount_on_a_failed_run_says_the_run_failed():
    """`status` is only ever printed beside a defect, and it changes what it means.

    "A run wrote the wrong number" and "a run died and this is what it left"
    lead to different next steps, and the second is the likelier one.
    """
    partial = claim(999) | {"status": "failed"}
    miscounted, _ = check_runs.reconcile({(TABLE, RUN): partial}, {(TABLE, RUN): held(26)})
    assert len(miscounted) == 1
    assert "status: failed" in miscounted[0]


def test_a_manifest_claiming_rows_with_nothing_behind_it_is_a_defect():
    miscounted, _ = check_runs.reconcile({(TABLE, RUN): claim(681)}, {})
    assert len(miscounted) == 1
    assert "holds none" in miscounted[0]


def test_rows_under_a_partition_the_manifest_does_not_name():
    miscounted, _ = check_runs.reconcile(
        {(TABLE, RUN): claim(26)}, {(TABLE, RUN): held(26, partitions=("2026-08-05",))}
    )
    assert len(miscounted) == 1
    assert f"ingest_date={DAY}" in miscounted[0]
    assert "2026-08-05" in miscounted[0]


def test_two_runs_on_one_day_do_not_cancel():
    """The reason the grain is the run id and not the `ingest_date` partition.

    One run over-claims by 10 and the next under-claims by 10, in the same
    partition on the same day. Compared per partition this is 100 rows against
    100 rows and silent; compared per run it is two defects.
    """
    claimed = {(TABLE, RUN): claim(60), (TABLE, LATER): claim(40)}
    present = {(TABLE, RUN): held(50), (TABLE, LATER): held(50)}
    miscounted, unrecorded = check_runs.reconcile(claimed, present)
    assert len(miscounted) == 2
    assert not unrecorded


# ---------------------------------------------------------------------------
# Unrecorded
# ---------------------------------------------------------------------------


def test_rows_whose_run_has_no_manifest():
    _, unrecorded = check_runs.reconcile({}, {(TABLE, RUN): held(26)})
    assert len(unrecorded) == 1
    assert TABLE in unrecorded[0]
    assert RUN in unrecorded[0]
    assert "no manifest" in unrecorded[0]


def test_rows_with_no_run_id_at_all_report_as_unrecorded():
    _, unrecorded = check_runs.reconcile({}, {(TABLE, check_runs.NO_RUN_ID): held(26)})
    assert len(unrecorded) == 1
    assert check_runs.NO_RUN_ID in unrecorded[0]


def test_one_run_id_in_two_tables_is_two_runs():
    """Run ids are unique per table, not globally: `new_run_id` is a UTC second.

    One `ingest.py --all` can start two datasets inside the same second, so a
    key that is the run id alone would let one table's manifest answer for
    another table's rows.
    """
    claimed = {(TABLE, RUN): claim(26)}
    present = {(TABLE, RUN): held(26), (OTHER, RUN): held(25)}
    miscounted, unrecorded = check_runs.reconcile(claimed, present)
    assert not miscounted
    assert len(unrecorded) == 1
    assert OTHER in unrecorded[0]


# ---------------------------------------------------------------------------
# Watermark drift, which warns and never fails
# ---------------------------------------------------------------------------


def test_no_drift_when_the_newest_claim_is_the_newest_row():
    claimed = {
        (TABLE, RUN): claim(26, watermark="2026-08-01T00:00:00.000Z"),
        (TABLE, LATER): claim(0, files=0, watermark="2026-08-06T00:00:00.000Z"),
    }
    assert check_runs.watermark_drift(claimed, TABLE, "2026-08-06T00:00:00.000Z") is None


def test_a_manifest_ahead_of_the_data_says_rows_were_not_written():
    claimed = {(TABLE, RUN): claim(26, watermark="2030-01-01T00:00:00.000Z")}
    line = check_runs.watermark_drift(claimed, TABLE, "2026-08-06T00:00:00.000Z")
    assert line is not None
    assert "did not write" in line


def test_data_ahead_of_every_manifest_says_rows_arrived_unfetched():
    claimed = {(TABLE, RUN): claim(26, watermark="2026-08-01T00:00:00.000Z")}
    line = check_runs.watermark_drift(claimed, TABLE, "2026-08-06T00:00:00.000Z")
    assert line is not None
    assert "no recorded run fetched" in line


def test_an_empty_table_has_no_drift_to_report():
    assert check_runs.watermark_drift({}, TABLE, None) is None
    assert check_runs.watermark_drift({(TABLE, RUN): claim(0, files=0)}, TABLE, None) is None
