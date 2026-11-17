"""The counts README.md states, checked against the files they describe.

They were kept by hand and drifted (dev note 2026-11-13). The tour page needs
no check: `tools/tour/build.py` counts the same things when it builds.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = " ".join((REPO_ROOT / "README.md").read_text().split())
DOCS = REPO_ROOT / "docs"


def _stated(pattern: str) -> tuple[int, ...]:
    match = re.search(pattern, README)
    assert match, f"README.md no longer has a sentence matching {pattern!r}; update this test"
    return tuple(int(number) for number in match.groups())


def _adr_statuses() -> list[str]:
    files = [*DOCS.glob("decisions/adr-*.md"), *DOCS.glob("archive/adr-*.md")]
    return [re.search(r"^status: (\w+)", path.read_text(), re.MULTILINE).group(1) for path in files]


def test_adr_and_plan_counts() -> None:
    statuses = _adr_statuses()
    plans = len(list(DOCS.glob("archive/plan-*.md")))
    assert _stated(r"(\d+) ADRs \((\d+) active\), (\d+) closed plans") == (
        len(statuses),
        statuses.count("active"),
        plans,
    )


def test_dbt_test_count() -> None:
    nodes = json.loads((DOCS / "dbt" / "manifest.json").read_text())["nodes"].values()
    assert _stated(r"(\d+) dbt tests") == (sum(n["resource_type"] == "test" for n in nodes),)


def test_context_pack_counts() -> None:
    pack = json.loads((REPO_ROOT / "context-pack" / "context_pack.duckdb.json").read_text())
    assert _stated(
        r"(\d+) refusals, (\d+) mandatory disclosures, (\d+) traps, "
        r"(\d+) declared joins and (\d+) examples"
    ) == (
        len(pack["refusals"]["entries"]),
        len(pack["disclosures"]),
        len(pack["traps"]),
        len(pack["joins"]),
        len(pack["examples"]),
    )
