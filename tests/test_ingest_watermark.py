"""Where one run starts fetching from, which is `resolve_watermark`'s whole job.

The precedence is `--since`, then `--full-refresh`, then `refresh: snapshot`,
then the zone. Three of those four branches are decisions rather than
mechanics, and the third is new (ADR-19 step 2): a snapshot dataset fetches
from its `start_date` every run so that the partition it writes holds the
whole dataset by construction, rather than because the city happens to bump
`:updated_at` across the registry on every bulk refresh.

That branch is free today for exactly the reason it was needed: the watermark
already matches every row, so it changes the request and not the result. Which
means no run and no fixture would notice if it were removed again, and a test
is the only thing that would. That is what this file is for.

`resolve_watermark` reads the zone only on the delta path, so the snapshot
cases here need no zone at all; the delta case is given a fake reader.
"""

import argparse

import ingest

import dataset_registry
import raw_zone

SNAPSHOT = ingest.DATASETS["business_locations"]
DELTA = ingest.DATASETS["311_cases"]


def args(since=None, full_refresh=False, raw_root=None) -> argparse.Namespace:
    return argparse.Namespace(since=since, full_refresh=full_refresh, raw_root=raw_root)


# ---------------------------------------------------------------------------
# The registry field is what decides it, and it has to be there
# ---------------------------------------------------------------------------


def test_the_two_datasets_under_test_are_the_refresh_kinds_they_claim():
    assert SNAPSHOT["refresh"] == "snapshot"
    assert DELTA["refresh"] == "delta"


# ---------------------------------------------------------------------------
# A snapshot dataset does not resume from the zone
# ---------------------------------------------------------------------------


def test_a_snapshot_fetches_from_its_start_date(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("a snapshot must not read the zone's watermark")

    monkeypatch.setattr(raw_zone, "read_watermark", fail)
    assert ingest.resolve_watermark(SNAPSHOT, args()) == SNAPSHOT["start_date"]


def test_a_snapshot_ignores_a_watermark_the_zone_could_have_given_it(monkeypatch):
    """The regression this exists to catch, and it is invisible in production.

    A watermark fetch returns every row today because the city bumps the whole
    registry, so putting the zone's watermark back here would change nothing
    observable until the day it did.
    """
    monkeypatch.setattr(raw_zone, "read_watermark", lambda *_a, **_k: "2026-09-06T12:00:00.000Z")
    assert ingest.resolve_watermark(SNAPSHOT, args()) == SNAPSHOT["start_date"]


def test_since_still_beats_a_snapshot(monkeypatch):
    """Narrowing a snapshot by hand stays possible; check_snapshots.py is what
    notices the partial partition it writes."""
    monkeypatch.setattr(raw_zone, "read_watermark", lambda *_a, **_k: "2026-09-06T12:00:00.000Z")
    asked = "2026-01-01T00:00:00.000Z"
    assert ingest.resolve_watermark(SNAPSHOT, args(since=asked)) == asked


def test_full_refresh_on_a_snapshot_is_the_same_answer():
    assert ingest.resolve_watermark(SNAPSHOT, args(full_refresh=True)) == SNAPSHOT["start_date"]


# ---------------------------------------------------------------------------
# A delta dataset still resumes, and must
# ---------------------------------------------------------------------------


def test_a_delta_resumes_from_the_zone(monkeypatch):
    monkeypatch.setattr(raw_zone, "read_watermark", lambda *_a, **_k: "2026-09-06T12:00:00.000Z")
    assert ingest.resolve_watermark(DELTA, args()) == "2026-09-06T12:00:00.000Z"


def test_an_empty_zone_makes_a_delta_a_backfill_from_start_date(monkeypatch, capsys):
    monkeypatch.setattr(raw_zone, "read_watermark", lambda *_a, **_k: None)
    assert ingest.resolve_watermark(DELTA, args()) == DELTA["start_date"]
    assert "full backfill" in capsys.readouterr().out


def test_since_beats_the_zone_for_a_delta(monkeypatch):
    monkeypatch.setattr(raw_zone, "read_watermark", lambda *_a, **_k: "2026-09-06T12:00:00.000Z")
    asked = "2024-01-01T00:00:00.000Z"
    assert ingest.resolve_watermark(DELTA, args(since=asked)) == asked


# ---------------------------------------------------------------------------
# What the manifest records, which mart_pipeline_freshness reports
# ---------------------------------------------------------------------------


def test_every_snapshot_dataset_starts_from_its_start_date(monkeypatch):
    """Not just business_locations: the guarantee is the registry field's, so
    it has to hold for the boundary sets and film_locations too."""

    def fail(*_args, **_kwargs):
        raise AssertionError("a snapshot must not read the zone's watermark")

    monkeypatch.setattr(raw_zone, "read_watermark", fail)
    for cfg in dataset_registry.snapshot_datasets().values():
        assert ingest.resolve_watermark(cfg, args()) == cfg["start_date"]
