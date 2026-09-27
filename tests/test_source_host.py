"""The DataSF host, in the one place it is allowed to be wrong.

DataSF moved the portal from `data.sfgov.org` to `data.sf.gov`. The old host is
not an alias that merely redirects: it 301s a bare `/resource/<id>.json` but
returns a bare nginx 403, HTML body and no Socrata error, to any request
carrying a `$select` -- which is every request `ingest.py` makes. That killed
the scheduled ingest silently for four days and read as a credentials failure,
because a 403 with an empty `SOCRATA_APP_TOKEN` in the job env looks like one.

Two different things are pinned here, and they fail for different reasons.

`test_no_stale_host_*` is a grep. It exists because the rename was seven
strings in six files and only one of them was on the request path; the other
six were prose and generated data that nobody would have noticed for months.
The next host move should fail a test rather than a cron job.

`test_committed_pack_urls_match_the_generator` is the one that catches silent
divergence rather than a stale string. `context-pack/*.json` is COMMITTED and
`check_pack` compares four things -- target, prose revision, spec version and
per-model schema hashes -- so a source URL or a licence line can disagree with
the code that generates it and CI stays green. That is exactly what a hostname
change does. Both values are pure templates over the registry and over
`publish.export.LICENSE`, with no warehouse state in either, so they can be
checked here with no build, no credentials and no network.
"""

import json
import subprocess
from pathlib import Path

import ingest
import pytest

from dataset_registry import DATASETS
from pack_inputs import source_urls

REPO_ROOT = Path(__file__).resolve().parent.parent
STALE_HOST = "data.sfgov.org"

# The host in URL position, which is the only form that can cause a 403. Both
# of the shapes this repo actually used are covered: `https://data.sfgov.org/d/`
# in the generated packs and the request path, and `DataSF (data.sfgov.org)` in
# the licence line. A bare prose mention is not matched, which is deliberate --
# the comment above SOCRATA_DOMAIN in ingest.py names the dead host on purpose
# and a test that forbade saying its name would delete the explanation.
STALE_URL_FORMS = [f"//{STALE_HOST}", f"({STALE_HOST})"]

# Where a live URL can actually appear. Deliberately not the whole tree:
#
#   docs/dev-notes/ and docs/decisions/ are history and are immutable by the
#   working agreement in CLAUDE.md. The 09-12 and 09-17 notes and ADR-16 name
#   the old host because the old host is what happened.
#
#   tests/fixtures/socrata/*.json holds `mobile311.sfgov.org` photo links,
#   which are captured API values, not requests this repo makes. Rewriting
#   them would falsify the fixture.
SEARCH_PATHS = [
    "ingestion",
    "tools",
    "publish",
    "scripts",
    "dbt",
    "context-pack",
    ".github",
    "tests/fixtures/make_fixtures.py",
    "Makefile",
    "README.md",
    "CLAUDE.md",
    "SETUP.md",
]


def _grep(patterns: list[str], path: str) -> list[str]:
    """Hits for any of `patterns` under `path`, via git grep so ignored files are out."""
    args = ["git", "grep", "-n", "--fixed-strings"]
    for pattern in patterns:
        args += ["-e", pattern]
    result = subprocess.run(
        [*args, "--", path], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    # git grep exits 1 for "no matches", which is the passing case.
    if result.returncode not in (0, 1):
        pytest.fail(f"git grep failed: {result.stderr}")
    return [line for line in result.stdout.splitlines() if line.strip()]


@pytest.mark.parametrize("path", SEARCH_PATHS)
def test_no_stale_host_outside_the_historical_record(path: str) -> None:
    hits = _grep(STALE_URL_FORMS, path)
    assert not hits, (
        f"{STALE_HOST} is dead for any request carrying a $select and returns "
        f"403, not a redirect. Use data.sf.gov:\n  " + "\n  ".join(hits)
    )


def test_the_request_path_uses_the_live_host() -> None:
    """Pinned as an equality, not an absence, so a typo fails too."""
    assert ingest.SOCRATA_DOMAIN == "https://data.sf.gov"


def test_committed_pack_urls_match_the_generator() -> None:
    """The committed packs are data the drift check does not compare."""
    live = {entry["dataset"]: entry["url"] for entry in source_urls(DATASETS)}
    for pack_path in sorted((REPO_ROOT / "context-pack").glob("context_pack.*.json")):
        pack = json.loads(pack_path.read_text())
        committed = {e["dataset"]: e["url"] for e in pack["identity"]["source_urls"]}
        assert committed == live, (
            f"{pack_path.name} disagrees with pack_inputs.source_urls. "
            "Regenerate with `make context-pack` (and TARGET=published)."
        )


def test_committed_pack_licence_matches_the_export() -> None:
    # Deferred and in this order, which is load-bearing rather than untidy:
    # publish/ reaches sys.path only when pack_target is imported, so a
    # top-level `from export import ...` would be sorted above it and fail.
    # generate.py:131 takes the same route to the same constant.
    import pack_target  # noqa: F401, PLC0415
    from export import LICENSE  # noqa: PLC0415

    for pack_path in sorted((REPO_ROOT / "context-pack").glob("context_pack.*.json")):
        pack = json.loads(pack_path.read_text())
        assert pack["identity"]["licence"] == LICENSE, (
            f"{pack_path.name} carries a licence string the export no longer writes."
        )
