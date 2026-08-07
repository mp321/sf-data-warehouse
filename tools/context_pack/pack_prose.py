"""The hand-maintained source, and the rule that keeps it honest.

`prose.yml` holds everything in the spec's sections 5 and 6 plus the traps, the
undeclared joins and the candidate examples: one copy, behind all three packs
(section 7). This module loads it, checks its shape, and then applies the one
rule that does two jobs at once:

    **An entry is rendered into a target's pack only when every citation in its
    evidence resolves against that target, and generation fails when an entry
    claims a target where they do not.**

That is not a warning. An entry about `h3_r9` or `street_trees` has to fail the
build rather than sit there reading plausibly, because the failure mode of a
refusal list is not that it is wrong in an obvious way; it is that it goes on
looking complete for a year after the warehouse moved. The same rule keeps
`applies_to` self-checking: an entry claiming the published target while citing a
staging model cannot resolve, so it cannot be claimed.

Six kinds of citation resolve, and the last one is the interesting one:

    model        the model is in this target's model set
    column       `<model>.<column>` exists in this target
    adr          an adr-<n>-*.md exists in docs/decisions/
    doc          a path exists in the repo
    registry     a `<source>.<field>` in the dataset registry, optionally
                 asserted equal to a value, which is how an entry cites a
                 boundary rather than restating a date that can move
    measurement  a number this project measured, carrying the date it was
                 measured on, or `measured: false` for one it has not

**Evidence is measured or it says it is not** (section 5.2). A citation of the
form "measured 2026-07-31" and one of the form "not measured in this project"
are both acceptable; a confident number with no source is not, and
`measured: false` is how `refuse.newest-month-is-partial` stays honest about the
arrival lag nobody here has measured.
"""

import datetime as dt
import hashlib
import re
from pathlib import Path

import yaml

from pack_inputs import REPO_ROOT, registry_field

PROSE_PATH = Path(__file__).resolve().parent / "prose.yml"

REFUSAL_CLASSES = ("absent", "mismeasured", "misnormalised")
EVIDENCE_KINDS = ("model", "column", "adr", "doc", "registry", "measurement")

# The preamble keys the markdown renderer needs. Missing one is a build failure
# rather than a section that quietly renders empty: every one of them is a
# sentence the pack's usefulness depends on.
REQUIRED_PREAMBLE = (
    "no_ground_truth",
    "census_exception",
    "closed_world_rule",
    "self_refusal",
    "how_to_read",
)


class ProseError(Exception):
    """prose.yml is malformed, or an entry does not resolve. Always fatal."""


class TargetFacts:
    """What a citation can resolve against, for one target."""

    def __init__(self, target_name: str, columns: dict[str, list[str]], adrs, sources: dict):
        self.target_name = target_name
        self.columns = {model: set(names) for model, names in columns.items()}
        self.models = set(columns)
        self.adrs = set(adrs)
        self.sources = sources


def load_prose(path: Path | None = None) -> tuple[dict, str]:
    """Returns the parsed prose and its revision hash.

    `prose_revision` is what lets a consumer holding two packs tell whether they
    came from one source (section 3). It hashes the file's bytes rather than the
    parsed structure, so a comment edit changes it: the same trade ADR-11 made
    for the derived zone's code stamp, and for the same reason. A revision that
    only moves when someone remembers to move it is not a revision.
    """
    prose_path = path or PROSE_PATH
    if not prose_path.exists():
        raise ProseError(f"No prose at {prose_path}. Section 7 requires exactly one such file.")
    raw = prose_path.read_bytes()
    revision = hashlib.sha256(raw).hexdigest()[:16]
    parsed = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ProseError(f"{prose_path} did not parse to a mapping.")
    return parsed, revision


# ---------------------------------------------------------------------------
# Structural validation, which is target independent
# ---------------------------------------------------------------------------


REQUIRED_FIELDS = {
    "refusals": ("class", "applies_to", "question_shapes", "rule", "why", "evidence", "instead"),
    "disclosures": ("applies_to", "when", "state", "why", "evidence"),
    "traps": ("applies_to", "state", "why", "evidence"),
    "joins": ("applies_to", "from", "to", "on", "why", "evidence"),
    "examples": ("applies_to", "question", "sql", "verified"),
}


def validate_shape(prose: dict, target_names) -> None:
    problems: list[str] = []

    preamble = prose.get("preamble") or {}
    problems.extend(
        f"preamble.{key} is missing or empty"
        for key in REQUIRED_PREAMBLE
        if not (preamble.get(key) or "").strip()
    )

    seen_ids: dict[str, str] = {}
    for block, required in REQUIRED_FIELDS.items():
        for entry in prose.get(block) or []:
            problems.extend(_common_problems(entry, block, required, target_names, seen_ids))

    for entry in prose.get("refusals") or []:
        problems.extend(_refusal_problems(entry, prose))
    for entry in prose.get("examples") or []:
        problems.extend(_example_problems(entry, prose))

    if problems:
        raise ProseError("prose.yml is malformed:\n  - " + "\n  - ".join(problems))


def _common_problems(entry, block, required, target_names, seen_ids) -> list[str]:
    entry_id = entry.get("id")
    if not entry_id:
        return [f"an entry in {block} has no id"]
    problems = []
    if entry_id in seen_ids:
        problems.append(f"duplicate id {entry_id!r}, in {seen_ids[entry_id]} and {block}")
    seen_ids[entry_id] = block
    problems.extend(
        f"{entry_id}: {field} is missing or empty" for field in required if not entry.get(field)
    )
    unknown = [name for name in entry.get("applies_to") or [] if name not in target_names]
    if unknown:
        problems.append(f"{entry_id}: applies_to names unknown targets {unknown}")
    for citation in entry.get("evidence") or []:
        kind = citation.get("kind")
        if kind not in EVIDENCE_KINDS:
            problems.append(f"{entry_id}: evidence kind {kind!r} is not one of {EVIDENCE_KINDS}")
        if not citation.get("ref"):
            problems.append(f"{entry_id}: an evidence entry has no ref")
        if kind == "measurement":
            problems.extend(_measurement_problems(entry_id, citation))
    return problems


def _refusal_problems(entry: dict, prose: dict) -> list[str]:
    problems = []
    entry_id = entry.get("id")
    if entry.get("class") not in REFUSAL_CLASSES:
        problems.append(f"{entry_id}: class {entry.get('class')!r} is not one of {REFUSAL_CLASSES}")
    instead = entry.get("instead")
    if not isinstance(instead, dict):
        return [*problems, f"{entry_id}: instead must be a mapping with an answer"]
    if not (instead.get("answer") or "").strip():
        # A refusal with no substitute is a dead end, and a model that wants to
        # be useful will route around a dead end (section 5.2).
        problems.append(f"{entry_id}: instead.answer is mandatory")
    example = instead.get("example")
    known = {candidate.get("id") for candidate in prose.get("examples") or []}
    if example and example not in known:
        problems.append(f"{entry_id}: instead.example {example!r} is not an example")
    return problems


def _example_problems(entry: dict, prose: dict) -> list[str]:
    problems = []
    entry_id = entry.get("id")
    if not ((entry.get("verified") or {}).get("sql_sha256") or "").strip():
        problems.append(
            f"{entry_id}: verified.sql_sha256 is mandatory. It is the only thing that stops an "
            "example rotting quietly, since a wrong query usually still runs."
        )
    known = {refusal.get("id") for refusal in prose.get("refusals") or []}
    problems.extend(
        f"{entry_id}: demonstrates {refusal_id!r}, which is not a refusal"
        for refusal_id in entry.get("demonstrates") or []
        if refusal_id not in known
    )
    return problems


def _measurement_problems(entry_id: str, citation: dict) -> list[str]:
    if "measured" not in citation:
        return [
            f"{entry_id}: a measurement citation must carry `measured`, either the date it was "
            "measured on or false for something this project has not measured"
        ]
    measured = citation["measured"]
    if measured is False:
        return []
    if isinstance(measured, dt.date):
        return []
    if isinstance(measured, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", measured):
        return []
    return [
        f"{entry_id}: measurement `measured` must be a YYYY-MM-DD date or false, got {measured!r}"
    ]


# ---------------------------------------------------------------------------
# Citation resolution, which is what makes a pack target specific
# ---------------------------------------------------------------------------


def _resolve_model(citation: dict, facts: TargetFacts) -> str | None:
    ref = citation["ref"]
    if ref not in facts.models:
        return f"model {ref!r} is not in the {facts.target_name} target"
    return None


def _resolve_column(citation: dict, facts: TargetFacts) -> str | None:
    model, _, column = str(citation["ref"]).partition(".")
    if model not in facts.models:
        return (
            f"column citation {citation['ref']!r} names model {model!r}, "
            f"not in the {facts.target_name} target"
        )
    if column not in facts.columns[model]:
        return f"model {model!r} has no column {column!r} in the {facts.target_name} target"
    return None


def _resolve_adr(citation: dict, facts: TargetFacts) -> str | None:
    if citation["ref"] not in facts.adrs:
        return f"no ADR {citation['ref']!r} in docs/decisions/"
    return None


def _resolve_doc(citation: dict, _facts: TargetFacts) -> str | None:
    if not (REPO_ROOT / citation["ref"]).exists():
        return f"no file at {citation['ref']}"
    return None


def _resolve_registry(citation: dict, facts: TargetFacts) -> str | None:
    value, problem = registry_field(facts.sources, citation["ref"])
    if problem:
        return problem
    if "equals" in citation and value != citation["equals"]:
        return (
            f"the registry says {citation['ref']} is {value!r} and the citation asserts "
            f"{citation['equals']!r}. The prose is describing a boundary that moved."
        )
    return None


def _resolve_measurement(citation: dict, facts: TargetFacts) -> str | None:
    """A measurement resolves on its own terms.

    It is a number this project took, and the shape check has already
    established that it says when, or says that it never did. What it must not
    do is describe a model the target does not have, which is what `about` is
    for: it is how `disclose.coordinate-drop-rates` stops applying to a target
    that has no mart_pipeline_freshness in it.
    """
    about = citation.get("about")
    return _resolve_model({"ref": about}, facts) if about else None


_RESOLVERS = {
    "model": _resolve_model,
    "column": _resolve_column,
    "adr": _resolve_adr,
    "doc": _resolve_doc,
    "registry": _resolve_registry,
    "measurement": _resolve_measurement,
}


def resolve_citation(citation: dict, facts: TargetFacts) -> str | None:
    """None when it resolves, otherwise why it does not."""
    resolver = _RESOLVERS.get(citation["kind"])
    if resolver is None:
        return f"unknown evidence kind {citation['kind']!r}"
    return resolver(citation, facts)


def select_for_target(prose: dict, facts: TargetFacts) -> dict:
    """Split every block into what this target renders, and fail on a bad claim.

    Two outcomes and they are deliberately asymmetric. An entry that claims this
    target and cannot resolve is a build failure. An entry that does not claim
    this target is simply not rendered, whether or not it would have resolved:
    `applies_to` is the author's statement of intent and resolution is the check
    on it, not a second way to get in.
    """
    selected = {"refusals": [], "disclosures": [], "traps": [], "joins": [], "examples": []}
    failures: list[str] = []

    for block, chosen in selected.items():
        for entry in prose.get(block) or []:
            if facts.target_name not in (entry.get("applies_to") or []):
                continue
            problems = [
                problem
                for problem in (
                    resolve_citation(citation, facts) for citation in entry.get("evidence") or []
                )
                if problem
            ]
            if problems:
                failures.append(
                    f"{entry['id']} claims the {facts.target_name} target, and "
                    + "; ".join(problems)
                )
                continue
            chosen.append(entry)

    if failures:
        raise ProseError(
            "Citations that do not resolve against the "
            f"{facts.target_name} target:\n  - " + "\n  - ".join(failures)
        )
    return selected


def check_join_duplication(prose_joins: list[dict], derived_joins: list[dict]) -> None:
    """Nothing derivable may live in prose.yml (section 7).

    dbt's `relationships` tests already declare a set of joins, so a prose entry
    restating one of them is two copies of a fact with nothing checking they
    agree, which is the failure this repo has already paid for twice. The prose
    file is for the joins no test declares: the dirty ones, and the ones a
    consumer will write anyway.
    """
    derived_keys = {(entry["from"], entry["to"]) for entry in derived_joins}
    duplicated = [
        entry["id"] for entry in prose_joins if (entry.get("from"), entry.get("to")) in derived_keys
    ]
    if duplicated:
        raise ProseError(
            "These prose joins restate a join a dbt relationships test already declares, "
            "which section 7 forbids because it is derivable: " + ", ".join(sorted(duplicated))
        )


def check_class_three_examples(refusals: list[dict], examples: list[dict]) -> None:
    """Every class 3 refusal must have a rendered example (section 4.7, rule 3).

    That is what makes the examples a consequence of the refusals rather than a
    wishlist beside them: a refusal of the form of an answer is only obeyable if
    the pack shows the form that works.
    """
    demonstrated = {
        refusal_id for example in examples for refusal_id in (example.get("demonstrates") or [])
    }
    missing = [
        entry["id"]
        for entry in refusals
        if entry["class"] == "misnormalised" and entry["id"] not in demonstrated
    ]
    if missing:
        raise ProseError(
            "Class 3 refusals with no verified example demonstrating the substitute: "
            + ", ".join(sorted(missing))
        )
