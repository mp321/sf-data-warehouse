"""The exposures declare every consumer of the marts, and only what each reads.

PLAN-10 step 6. `dbt/models/marts/_exposures.yml` puts the published export
and the two context packs on the lineage graph. dbt checks that a `ref` in an
exposure resolves; it cannot check that the list is the right one. These tests
compare each exposure against the thing that actually decides what the
consumer reads: `PUBLISHED_MARTS` for the export, the committed pack JSON for
each pack. A mart added to either without an exposure change fails here.

The url test exists because `leak-check` scans for credentials and not for a
bucket name, so nothing else would stop one reaching dbt docs.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

from pack_target import PUBLISHED_MARTS

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPOSURES_FILE = REPO_ROOT / "dbt" / "models" / "marts" / "_exposures.yml"
PACK_DIR = REPO_ROOT / "context-pack"
REPO_URL = "https://github.com/mp321/sf-data-warehouse/"

MART_PREFIXES = ("mart_", "dim_")
EXPOSURE_NAMES = ["published_export", "context_pack_duckdb", "context_pack_published"]


def _exposures() -> dict[str, dict]:
    doc = yaml.safe_load(EXPOSURES_FILE.read_text())
    return {e["name"]: e for e in doc["exposures"]}


def _refs(exposure: dict) -> set[str]:
    return {re.fullmatch(r"ref\('([a-z0-9_]+)'\)", d).group(1) for d in exposure["depends_on"]}


def _pack_marts(target: str) -> set[str]:
    pack = json.loads((PACK_DIR / f"context_pack.{target}.json").read_text())
    return {m["name"] for m in pack["models"] if m["name"].startswith(MART_PREFIXES)}


def test_exactly_three_consumers_are_declared():
    assert set(_exposures()) == set(EXPOSURE_NAMES)


def test_published_export_depends_on_exactly_the_published_marts():
    assert _refs(_exposures()["published_export"]) == set(PUBLISHED_MARTS)


@pytest.mark.parametrize("target", ["duckdb", "published"])
def test_each_context_pack_depends_on_the_marts_it_describes(target):
    assert _refs(_exposures()[f"context_pack_{target}"]) == _pack_marts(target)


@pytest.mark.parametrize("name", EXPOSURE_NAMES)
def test_every_exposure_has_an_owner_and_a_repo_url_with_no_bucket(name):
    exposure = _exposures()[name]
    assert exposure["owner"]["name"]
    assert exposure["url"].startswith(REPO_URL)
    assert "gs://" not in exposure["url"]
    assert "storage.googleapis.com" not in exposure["url"]
