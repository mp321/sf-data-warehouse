"""Three rules from CLAUDE.md "Hard constraints" that had no mechanism.

No geometry or H3 in SQL (ADR-5, ADR-6), the local zone variable beats the
bucket one (ADR-9), and no workflow deletes from the raw zone (ADR-18).
"""

import re
from pathlib import Path

import pytest
import yaml

import derived_zone
import raw_zone
import remote

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_FILES = sorted(
    path
    for folder in ("models", "macros", "tests")
    for path in (REPO_ROOT / "dbt" / folder).rglob("*.sql")
)
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))

# Comments are stripped first: several models explain what they avoid by name.
SQL_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/|\{#.*?#\}", re.DOTALL)

FORBIDDEN_SQL = {
    "a spatial function (ADR-6)": re.compile(r"\bst_\w+\s*\(", re.IGNORECASE),
    "an H3 function (ADR-5)": re.compile(r"\bh3_\w+\s*\(", re.IGNORECASE),
    # Cast forms only: point_geography.sql uses `geography` as a table alias.
    "a geometry type (ADR-6)": re.compile(
        r"(::\s*|\bas\s+)(geography|geometry)\s*\)|::\s*(geography|geometry)\b", re.IGNORECASE
    ),
    "the spatial extension (ADR-6)": re.compile(r"\b(install|load)\s+spatial\b", re.IGNORECASE),
}

# The two spellings of a raw zone deletion: the flag and the make target.
APPLY = re.compile(r"--apply\b|prune-raw-apply")


def test_the_sql_scan_found_files() -> None:
    assert SQL_FILES, "no SQL under dbt/; the scan below would pass on nothing"
    assert WORKFLOWS, "no workflow files; the scan below would pass on nothing"


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_no_geometry_or_h3_in_sql(path: Path) -> None:
    code = SQL_COMMENTS.sub("", path.read_text())
    hits = [what for what, pattern in FORBIDDEN_SQL.items() if pattern.search(code)]
    assert not hits, (
        f"{path.relative_to(REPO_ROOT)} uses {', '.join(hits)}. Geography is a "
        "precomputed column; see stg_spatial__point_geography."
    )


@pytest.mark.parametrize(
    ("root", "dir_variable", "uri_variable"),
    [
        (raw_zone.raw_root, "RAW_ZONE_DIR", "RAW_ZONE_URI"),
        (derived_zone.derived_root, "DERIVED_ZONE_DIR", "DERIVED_ZONE_URI"),
    ],
)
def test_the_local_zone_variable_beats_the_bucket_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root, dir_variable: str, uri_variable: str
) -> None:
    """A shell that sourced `.env` must still get a local `make ci-build`."""
    monkeypatch.setenv(uri_variable, "gs://example-bucket/zone")
    monkeypatch.setenv(dir_variable, str(tmp_path))
    assert root() == tmp_path

    monkeypatch.delenv(dir_variable)
    assert root() == "gs://example-bucket/zone"
    assert remote.is_remote(root())


def _run_lines(workflow: Path) -> list[str]:
    """Every shell line a workflow executes, across all jobs and steps."""
    jobs = yaml.safe_load(workflow.read_text()).get("jobs", {})
    scripts = [step.get("run", "") for job in jobs.values() for step in job.get("steps", [])]
    return [line.strip() for script in scripts for line in script.splitlines()]


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_deletes_from_the_raw_zone(workflow: Path) -> None:
    # An echo that tells a human to run the apply is allowed; `4) echo ...` too.
    executed = [
        line
        for line in _run_lines(workflow)
        if not re.sub(r"^\S+\)\s*", "", line).startswith("echo ")
    ]
    hits = [line for line in executed if APPLY.search(line)]
    assert not hits, (
        f"{workflow.name} runs the prune apply. The proof may run on a schedule; "
        f"the deletion is by hand (ADR-18):\n  " + "\n  ".join(hits)
    )
