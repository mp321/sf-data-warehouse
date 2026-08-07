"""Everything the pack derives rather than reads from prose.

The rule this module exists to enforce is the one in the spec's section 7: if a
fact can be read from the dbt manifest, the dataset registry or the published
manifest, it is read from there and never from `prose.yml`. So every derived
fact in the pack comes through one of the functions here, and the prose file
stays small enough to be checked by hand.

Four inputs, in the order the generator reaches for them:

    dbt/target/manifest.json    models, layers, descriptions, column
                                descriptions, relationship tests, the dbt and
                                adapter versions, the invocation identity
    dbt/dbt_project.yml         the dataset registry, via
                                ingestion/dataset_registry.py, which is the
                                loader for the one copy (CLAUDE.md)
    published/manifest.json     the published target's identity and integrity
    docs/decisions/             the ADR files a citation can resolve against

`dbt/target/manifest.json` and not `docs/dbt/manifest.json`: the committed copy
is refreshed by `make docs` and is therefore a snapshot of whenever someone last
ran it, while the pack has to describe the build that produced the numbers it is
reporting. A pack whose row counts came from today's warehouse and whose grain
sentences came from a manifest three days old would be internally inconsistent
in a way nothing downstream could detect.
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ingestion/ is a directory of scripts rather than a package, for the reasons
# tests/conftest.py sets out, so importing the registry loader means doing what
# a script in that directory would have done for itself.
sys.path.insert(0, str(REPO_ROOT / "ingestion"))

from dataset_registry import load_registry  # noqa: E402  (path set above)

MANIFEST_PATH = REPO_ROOT / "dbt" / "target" / "manifest.json"
PUBLISHED_MANIFEST_PATH = REPO_ROOT / "published" / "manifest.json"
DECISIONS_DIR = REPO_ROOT / "docs" / "decisions"

LAYERS = ("staging", "intermediate", "marts")

# Abbreviations that end in a period and do not end a sentence. The grain
# extractor below walks periods, and without this list a description reading
# "One row per source, e.g. 311_cases, per month." would yield a grain sentence
# that stops at "e.g.".
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "approx.", "cf.", "vs.")


class PackInputError(Exception):
    """A derived input is missing or unusable. Always fatal: see section 8."""


# ---------------------------------------------------------------------------
# The dbt manifest
# ---------------------------------------------------------------------------


def load_manifest(path: Path | None = None) -> dict:
    manifest_path = path or MANIFEST_PATH
    if not manifest_path.exists():
        raise PackInputError(
            f"No dbt manifest at {manifest_path}. Run `make build` first: the pack "
            "reports on a build rather than on a compile, so there has to have been one."
        )
    return json.loads(manifest_path.read_text())


def build_identity(manifest: dict) -> dict:
    """The dbt run this pack is reporting on. Section 4.2 and section 8."""
    meta = manifest["metadata"]
    return {
        "invocation_id": meta["invocation_id"],
        "manifest_generated_at": meta["generated_at"],
        "dbt_version": meta["dbt_version"],
        "adapter_type": meta["adapter_type"],
        "project_id": meta["project_id"],
    }


def grain_sentence(description: str) -> str | None:
    """The "one row per ..." sentence, or None if the description has none.

    A model with no grain sentence fails generation (section 4.3) rather than
    emitting an empty field, so this returns None and the caller raises. The
    sentence is not written twice: CLAUDE.md already requires every model
    description to open with it, so this reads what is there.
    """
    text = re.sub(r"\s+", " ", description or "").strip()
    match = re.search(r"\bone row per\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    start = match.start()
    # Mask the abbreviations rather than walking back from each period. The
    # replacement is the same length, so an offset into the masked string is an
    # offset into the real one.
    masked = text
    for abbreviation in _ABBREVIATIONS:
        masked = masked.replace(abbreviation, abbreviation.replace(".", "\x00"))
    stop = masked.find(".", start)
    return text[start:].strip() if stop == -1 else text[start : stop + 1].strip()


def _layer_of(node: dict) -> str:
    for part in node.get("fqn", []):
        if part in LAYERS:
            return "mart" if part == "marts" else part
    raise PackInputError(
        f"Model {node['name']} is in none of {LAYERS}. Every model belongs to a layer; "
        "see the directory conventions in CLAUDE.md."
    )


def models(manifest: dict) -> dict[str, dict]:
    """Every model in the project, keyed by name, in dependency order.

    Dependency order rather than alphabetical because the pack is read top to
    bottom by something composing SQL, and a mart is easier to understand after
    the models it selects from. dbt gives the graph; this walks it.
    """
    nodes = {
        key: node for key, node in manifest["nodes"].items() if node["resource_type"] == "model"
    }
    parents = {
        key: [dep for dep in node["depends_on"]["nodes"] if dep in nodes]
        for key, node in nodes.items()
    }

    ordered: list[str] = []
    seen: set[str] = set()

    def visit(key: str, stack: tuple[str, ...] = ()) -> None:
        if key in seen:
            return
        if key in stack:
            raise PackInputError(f"Cycle in the model graph at {key}")
        for parent in sorted(parents[key]):
            visit(parent, (*stack, key))
        seen.add(key)
        ordered.append(key)

    for key in sorted(nodes):
        visit(key)

    result: dict[str, dict] = {}
    for key in ordered:
        node = nodes[key]
        result[node["name"]] = {
            "name": node["name"],
            "unique_id": key,
            "layer": _layer_of(node),
            "materialisation": node["config"]["materialized"],
            "description": re.sub(r"\s+", " ", node.get("description") or "").strip(),
            "column_descriptions": {
                column_name: re.sub(r"\s+", " ", column.get("description") or "").strip()
                for column_name, column in node.get("columns", {}).items()
            },
            "depends_on": [nodes[dep]["name"] for dep in sorted(parents[key])],
        }
    return result


def _ref_target(expression: str) -> str | None:
    """The model name out of a `ref('x')` string, as relationship tests store it."""
    match = re.search(r"ref\(\s*['\"]([^'\"]+)['\"]", expression or "")
    return match.group(1) if match else None


def unique_columns(manifest: dict) -> set[tuple[str, str]]:
    """(model, column) pairs carrying a `unique` test.

    Used for join cardinality: a join onto a column dbt asserts is unique is
    many-to-one, and one onto a column it does not is many-to-many until proven
    otherwise. That is derivable, so it is derived rather than asserted in prose.
    """
    pairs: set[tuple[str, str]] = set()
    for node in manifest["nodes"].values():
        if node["resource_type"] != "test":
            continue
        metadata = node.get("test_metadata") or {}
        if metadata.get("name") != "unique":
            continue
        column = node.get("column_name") or (metadata.get("kwargs") or {}).get("column_name")
        for parent in node["depends_on"]["nodes"]:
            if parent.startswith("model.") and column:
                pairs.add((parent.split(".")[-1], column))
    return pairs


def declared_joins(manifest: dict) -> list[dict]:
    """The joins dbt already knows about, from `relationships` tests.

    Every one of these is a join a consumer might write and that the build
    asserts holds, so the join map starts here and `prose.yml` adds only the
    joins no test declares. A prose entry that restates one of these is the
    duplication section 7 forbids, and the generator fails on it.
    """
    found: list[dict] = []
    for node in manifest["nodes"].values():
        if node["resource_type"] != "test":
            continue
        metadata = node.get("test_metadata") or {}
        if metadata.get("name") != "relationships":
            continue
        kwargs = metadata.get("kwargs") or {}
        to_model = _ref_target(kwargs.get("to", ""))
        to_field = kwargs.get("field")
        from_model = (node.get("attached_node") or "").split(".")[-1]
        from_column = node.get("column_name") or kwargs.get("column_name")
        if not (to_model and to_field and from_model and from_column):
            continue
        found.append(
            {
                "from": f"{from_model}.{from_column}",
                "to": f"{to_model}.{to_field}",
                "on": f"{from_model}.{from_column} = {to_model}.{to_field}",
                "declared_by": "relationships test",
            }
        )
    return sorted(found, key=lambda entry: (entry["from"], entry["to"]))


# ---------------------------------------------------------------------------
# The dataset registry and the published manifest
# ---------------------------------------------------------------------------


def registry() -> dict:
    """`vars.pipeline_sources`, through the loader that owns reading it.

    Not re-read with PyYAML here. There is one copy of the registry and one
    loader for it (CLAUDE.md), and a second reader is the beginning of a second
    copy.
    """
    return load_registry()


def dbt_vars() -> dict:
    """`vars` from dbt_project.yml, minus the registry.

    The registry comes through `registry()` above and never through here, which
    is why this pops it: two readers of one list is how the second copy starts,
    and the H3 settings beside it have no loader of their own to go through.
    """
    import yaml  # noqa: PLC0415  (only this function needs it)

    parsed = yaml.safe_load((REPO_ROOT / "dbt" / "dbt_project.yml").read_text())
    variables = dict(parsed.get("vars") or {})
    variables.pop("pipeline_sources", None)
    return variables


def registry_field(sources: dict, ref: str):
    """A `<source>.<field>` lookup, for citations that assert a registry value.

    `refuse.no-cross-dataset-series-before-2024` is the reason this exists: the
    backfill boundary it warns about is a registry value, so the entry cites the
    value rather than restating the date, and generation fails if the registry
    moves under it.
    """
    source_name, _, field = ref.partition(".")
    if source_name not in sources:
        return None, f"no source named {source_name!r} in the dataset registry"
    if not field:
        return None, f"citation {ref!r} names a source but no field"
    if field not in sources[source_name]:
        return None, f"source {source_name!r} has no registry field {field!r}"
    return sources[source_name][field], None


def published_manifest(path: Path | None = None) -> dict | None:
    manifest_path = path or PUBLISHED_MANIFEST_PATH
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text())


def adr_ids() -> set[str]:
    """The ADRs a citation can resolve against, from the files themselves."""
    return {path.name.split("-")[0] + "-" + path.name.split("-")[1] for path in decisions()}


def decisions() -> list[Path]:
    return sorted(DECISIONS_DIR.glob("adr-*.md"))


def source_urls(sources: dict) -> list[dict]:
    """Where each dataset comes from, built from the registry rather than listed.

    Socrata ids are in the registry, so the URL is a template over them. The one
    tigerweb source has no Socrata id and says so, which is also derived: the
    registry's `api` field is what distinguishes them.
    """
    urls = []
    for name, spec in sources.items():
        if spec.get("api") == "tigerweb":
            urls.append(
                {
                    "dataset": name,
                    "publisher": "US Census Bureau",
                    "url": "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb",
                }
            )
        else:
            urls.append(
                {
                    "dataset": name,
                    "publisher": "DataSF",
                    "url": f"https://data.sfgov.org/d/{spec['socrata_id']}",
                }
            )
    return urls
