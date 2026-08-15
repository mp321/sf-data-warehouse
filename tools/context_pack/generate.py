"""Generate the context pack: what a model must know, and what it must refuse.

PLAN-6 step 2. The contract is `docs/specs/context-pack.md`, which was written
before this code and which this code is verified against rather than the other
way round.

    python tools/context_pack/generate.py --target duckdb
    python tools/context_pack/generate.py --target duckdb --check
    make context-pack

Two files per target under `context-pack/`: the JSON is the artifact and the
markdown is a rendering of it for direct prompt injection, carrying strictly
less. Nothing appears in the markdown that is not in the JSON.

**Four things fail the build, and every one of them is a rule the spec says must
not be a warning.**

    1. A model with no "one row per ..." grain sentence (section 4.3).
    2. An entry in prose.yml that claims a target while citing a model, column,
       registry value or ADR that target does not have (sections 5.2 and 7).
    3. An example query that errors, or whose SQL no longer matches the hash a
       human attested to (sections 4.7 and 8).
    4. A markdown rendering that cannot fit the token budget with every refusal,
       disclosure and grain sentence present (section 9).

The fourth is the one worth stating twice. Under budget pressure this drops
examples, then the markers on undocumented columns, then profile statistics,
then low-signal columns, and then it fails. **A pack missing a refusal is worse
than no pack, because it reads complete.**

**Two targets are generated: `duckdb` and `published`.** Neither needs
credentials, which is what lets CI check both. `bigquery` is declared in
`pack_target.py` so the prose validator can reason about its model set, and
`open_target` raises for it; ADR-15 is why it is not generated.

The sibling modules here are prefixed `pack_` for the reason ruff.toml gives
about `ingestion/datasets.py`: this directory goes on `sys.path` when the script
runs, so a module named `profile` or `render` would shadow, or be shadowed by,
something else on it. `profile` is in the standard library.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack_inputs
import pack_profile
import pack_prose
import pack_render
from pack_inputs import REPO_ROOT, PackInputError
from pack_prose import ProseError, TargetFacts
from pack_target import TARGET_NAMES, TARGETS, TargetError, open_target

# Semver of the pack's own shape, bumped by hand on a breaking change
# (section 3). A consumer that pins this can refuse to read a newer pack rather
# than misread it, which is the same contract MANIFEST_VERSION has in
# publish/export.py.
PACK_VERSION = "1.0.0"

SPEC_PATH = REPO_ROOT / "docs" / "specs" / "context-pack.md"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "context-pack"

# Big enough for the whole duckdb pack with everything present, and not much
# bigger, so that a model or a layer arriving without anyone noticing shows up
# as a build failure rather than as a quietly larger prompt.
#
# Measured on the real warehouse on 2026-08-06, in estimated tokens:
#
#     everything present                       25,301
#     without the surplus examples             24,870
#     without the undocumented-column markers  23,694
#     without profile statistics               16,440
#     without low-signal columns               16,356
#
# Two thirds of it is the models block, and half of that is the profiles, which
# is why they are the third thing dropped rather than the first. The refusals
# and disclosures together are about 6,400 and are never dropped at all.
DEFAULT_TOKEN_BUDGET = 26000

# Columns that carry a serialised shape rather than a value. They are real
# columns and stay in the JSON; they are the first thing worth losing from a
# prompt-sized rendering, because nothing a consumer writes will select one.
GEOMETRY_BLOB_COLUMNS = ("geojson", "the_geom", "polygon")


class GenerationError(Exception):
    """A rule in the spec that must fail the build has failed it."""


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def spec_version() -> str:
    """The version of the spec this pack was built against.

    The spec carries a `date` in its frontmatter and no version number, so the
    date is the version: it moves when the contract is amended, which is exactly
    when a pack needs to say it was built against something older.
    """
    text = SPEC_PATH.read_text()
    match = re.search(r"^date:\s*(\S+)\s*$", text, flags=re.MULTILINE)
    if not match:
        raise PackInputError(f"No `date:` in the frontmatter of {SPEC_PATH}")
    return match.group(1)


def build_identity_block(target, prose: dict, sources: dict, published: dict | None) -> dict:
    identity = prose.get("identity") or {}
    description = pack_render._flat(identity.get("description", ""))
    if target.name == "published":
        # The prose sentence describes the warehouse, which is the one thing it
        # is allowed to describe (spec 4.1: one sentence, written once). In a
        # published pack it would otherwise open by naming staging views the
        # reader does not have, so the target's own sentence is appended here,
        # derived from the model set rather than written a second time.
        description += (
            f" This pack describes the published export of that warehouse and not the warehouse: "
            f"{len(target.model_names)} marts, one Parquet file each, and nothing else. Anything "
            "the export does not carry is a refusal here even where the warehouse can answer it."
        )
    licence = (published or {}).get("license")
    if not licence:
        from export import LICENSE  # noqa: PLC0415  (pack_target put publish/ on sys.path)

        licence = LICENSE
    return {
        "name": "sf-data-warehouse",
        "description": description,
        "publisher": "DataSF and the US Census Bureau, modelled here",
        "jurisdiction": "San Francisco, California",
        "licence": licence,
        "repo": identity.get("repo", ""),
        "source_urls": pack_inputs.source_urls(sources),
        "update_cadence": _update_cadence(sources),
        "how_to_read": pack_render._flat(prose["preamble"]["how_to_read"]),
    }


def _update_cadence(sources: dict) -> list[dict]:
    """Per source, derived: the SLA is a registry field and the schedule is CI's."""
    schedule = _ingest_schedule()
    return [
        {
            "dataset": name,
            "tier": spec["tier"],
            "stale_after_hours": spec.get("stale_after_hours"),
            "ingest_schedule": schedule,
        }
        for name, spec in sources.items()
    ]


def _ingest_schedule() -> str:
    workflow = REPO_ROOT / ".github" / "workflows" / "ingest.yml"
    if not workflow.exists():
        return "unscheduled"
    match = re.search(r"cron:\s*[\"']([^\"']+)[\"']", workflow.read_text())
    return f'cron "{match.group(1)}" (UTC)' if match else "unscheduled"


def build_build_block(target, manifest: dict, sources: dict, variables: dict) -> dict:
    """Section 4.2. What produced the numbers in this pack."""
    block = dict(pack_inputs.build_identity(manifest))
    block["engine_version"] = target.execute("select version()")[0][0]
    block["h3_resolutions"] = {
        "membership": variables["h3_membership_resolution"],
        "mart": variables["h3_mart_resolution"],
        "available": sorted(int(key) for key in variables["h3_cell_area_sq_km"]),
    }
    block["dataset_registry"] = [
        {
            "name": name,
            "table": spec["table"],
            "staging_model": spec["staging_model"],
            "tier": spec["tier"],
            "kind": spec["kind"],
            "start_date": spec["start_date"],
        }
        for name, spec in sources.items()
    ]
    if target.name == "duckdb":
        # Repo-relative on purpose. This block is committed, so an absolute path
        # would publish the generating machine's home directory into the pack.
        databases = target.execute("pragma database_list")
        path = Path(str(databases[0][2]))
        try:
            block["warehouse_path"] = str(path.relative_to(REPO_ROOT))
        except ValueError:
            block["warehouse_path"] = path.name
    return block


def build_models_block(target, models: dict, warnings: list[str]) -> list[dict]:
    entries = []
    for name in target.model_names:
        model = models[name]
        grain = pack_inputs.grain_sentence(model["description"])
        if not grain:
            raise GenerationError(
                f"{name} has no 'one row per ...' sentence in its description. Every model in "
                "this project has one because CLAUDE.md requires it, so this is a model that is "
                "not finished rather than a field the pack can leave empty (section 4.3)."
            )
        columns = target.columns(name)
        row_count = target.row_count(name)
        profiles = pack_profile.profile_model(target, name, columns, row_count)

        column_entries = []
        for column_name, data_type in columns:
            description = model["column_descriptions"].get(column_name, "")
            profile = profiles[column_name]
            if not description:
                warnings.append(f"{name}.{column_name} has no description in the yml")
            elif profile.get("null_rate") and "null" not in description.lower():
                warnings.append(
                    f"{name}.{column_name} is {profile['null_rate']:.1%} null and its "
                    "description does not say what a null means"
                )
            column_entries.append(
                {
                    "name": column_name,
                    "type": data_type,
                    "description": description,
                    "profile": profile,
                    "low_signal": _is_low_signal(column_name, description, profile),
                }
            )

        entries.append(
            {
                "name": name,
                "layer": model["layer"],
                "materialisation": model["materialisation"],
                "grain": grain,
                "row_count": row_count,
                "depends_on": [dep for dep in model["depends_on"] if dep in target.model_names],
                "columns": column_entries,
            }
        )
    return entries


def _is_low_signal(name: str, description: str, profile: dict) -> bool:
    """The last thing dropped under budget pressure, and only ever from the markdown.

    Carrying no information a consumer can act on: a serialised shape, or a
    column that is entirely null or constant and that nobody thought worth
    describing. The blob check comes before the description check on purpose,
    because those columns are described and are still the first thing a
    prompt-sized rendering should lose: nothing a consumer writes selects one.
    """
    if name.lower() in GEOMETRY_BLOB_COLUMNS:
        return True
    if description:
        return False
    if profile.get("null_rate") == 1.0:
        return True
    return profile.get("distinct_count") == 1


def build_joins_block(derived: list[dict], prose_joins: list[dict], target, unique) -> list[dict]:
    """Section 4.4. The derived joins first, then the ones no test declares."""
    entries = []
    for join in derived:
        from_model = join["from"].split(".")[0]
        to_model, to_column = join["to"].split(".")
        if from_model not in target.model_names or to_model not in target.model_names:
            continue
        entries.append(
            {
                "from": join["from"],
                "to": join["to"],
                "on": join["on"],
                "cardinality": "many to one" if (to_model, to_column) in unique else "many to many",
                "safe": True,
                "why": "",
                "declared_by": join["declared_by"],
            }
        )
    for join in prose_joins:
        entries.append(
            {
                "from": join["from"],
                "to": join["to"],
                "on": join["on"],
                "cardinality": join.get("cardinality", "unknown"),
                "safe": bool(join.get("safe", False)),
                "why": join.get("why", ""),
                "declared_by": f"prose.yml, {join['id']}",
            }
        )
    return entries


def build_freshness_block(target, published: dict | None) -> dict:
    """Section 4.5. mart_pipeline_freshness projected, not a second calculation.

    **The published target answers a different question and says so** (spec 4.5,
    PLAN-8 step 2). Its headline freshness is the publish time from
    `published/manifest.json`, because that is the age of the artifact the
    consumer is holding, and the gap between it and the build has been days. The
    per-source rows below it are still projected from `mart_pipeline_freshness`,
    which the export happens to carry, but they date the raw zone as it stood in
    the build the export was written from and not the export. Giving one of those
    two numbers without the other is how a reader concludes the export is as
    fresh as the pipeline that produced it.
    """
    rows = target.execute(
        "select source_name, tier, row_count, last_load_at, last_run_finished_at, "
        "stale_after_hours, is_stale from main.mart_pipeline_freshness order by source_name"
    )
    if target.name == "published":
        if not published:
            raise GenerationError(
                "The published target has no manifest.json, so the pack has no publish time to "
                "report as its freshness (spec 4.5)."
            )
        basis = (
            "published/manifest.json. published_at is when this export was written, which is the "
            "age of the files you are reading. The per-source rows are mart_pipeline_freshness as "
            "it stood in the build this export was written from: last_load_at is when rows landed "
            "in the raw zone, not when this export was published, and the two have been days "
            "apart. Neither number is the other's substitute."
        )
    else:
        basis = (
            "mart_pipeline_freshness, projected. last_load_at is when rows last landed in the "
            "raw zone, which is the build's own view of its inputs and not a publish time."
        )
    block = {
        "basis": basis,
        "sources": [
            {
                "source": source,
                "tier": tier,
                "row_count": row_count,
                "last_load_at": pack_profile._scalar(last_load_at),
                "last_run_finished_at": pack_profile._scalar(last_run),
                "stale_after_hours": sla,
                "is_stale": bool(is_stale),
            }
            for source, tier, row_count, last_load_at, last_run, sla, is_stale in rows
        ],
    }
    if target.name == "published":
        block["published_at"] = published["generated_at"]
        block["manifest_version"] = published["manifest_version"]
    return block


def build_examples_block(target, candidates: list[dict], required_ids: set[str]) -> list[dict]:
    """Execute every candidate. An unverified example is worse than none.

    Two failures, and they catch different rot. A query that errors fails the
    build (section 8, rule 1). A query whose text no longer hashes to what a
    human attested to also fails it, because a wrong query usually still runs:
    re-executing proves it parses, and only the attestation says someone read the
    result and agreed it answers the question (section 4.7, rule 2).
    """
    entries = []
    for candidate in candidates:
        sql = candidate["sql"].strip()
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        attested = candidate["verified"]["sql_sha256"]
        if digest != attested:
            raise GenerationError(
                f"Example {candidate['id']}: the SQL does not match the hash it was verified "
                f"under.\n  attested: {attested}\n  actual:   {digest}\n"
                "Run the query, read the result, confirm it still answers the question, then "
                "put the actual hash in prose.yml. Editing an example without re-verifying it "
                "is the quiet rot section 4.7 exists to stop."
            )
        try:
            rows = target.execute(sql)
        except Exception as error:
            raise GenerationError(
                f"Example {candidate['id']} failed against the {target.name} target: {error}"
            ) from error
        entries.append(
            {
                "id": candidate["id"],
                "question": candidate["question"],
                "sql": sql,
                "demonstrates": candidate.get("demonstrates") or [],
                "required": bool(set(candidate.get("demonstrates") or []) & required_ids),
                "verified": {
                    "target": target.name,
                    "at": _now(),
                    "rows": len(rows),
                    "sql_sha256": digest,
                },
            }
        )
    return entries


def build_integrity_block(target, manifest: dict, prose: dict, published: dict | None) -> dict:
    """Section 8. What a consumer compares against the target before trusting this."""
    block = {
        "self_refusal": pack_render._flat(prose["preamble"]["self_refusal"]),
        "dbt": pack_inputs.build_identity(manifest),
        "models": {},
    }
    absent_because = TARGETS[target.name].get("schema_hash_absent_because")
    if absent_because:
        block["schema_hash_absent_because"] = absent_because
    for name in target.model_names:
        block["models"][name] = {
            "schema_hash": target.schema_hash(name),
            "row_count": target.row_count(name),
        }
    if target.name == "published" and published:
        block["published_manifest_version"] = published["manifest_version"]
        block["published_generated_at"] = published["generated_at"]
        block.update(_published_manifest_hashes(target, block["models"], published))
    return block


def _published_manifest_hashes(target, models: dict, published: dict) -> dict:
    """Both hashes, and the fact that three of six disagree (spec section 8).

    Section 8 has the published pack carry the per-dataset `schema_hash` from
    `published/manifest.json`, "which is the authority on the export and not
    something the pack recomputes". Both halves of that are worth stating exactly,
    because they are not the same hash.

    `publish/export.py` computes its hash against the warehouse table it is about
    to write. The pack computes its own against the Parquet that was written.
    Measured 2026-08-07 on the six marts: three agree and three do not, because
    HUGEINT has no Parquet type and DuckDB writes those columns as DOUBLE. So a
    count column that is an exact integer in the warehouse is a float in the
    export, and neither hash is wrong: one describes what was exported and the
    other describes what a consumer opens.

    This does not fail generation. The difference is a property of the format
    rather than of this export, so failing here would mean no published pack can
    ever be generated, and the honest thing is to carry both numbers and say
    which is which. What a consumer compares against the bucket is the manifest
    hash; what they compare against the file they just opened is the pack's.
    """
    from_manifest = {entry["dataset"]: entry["schema_hash"] for entry in published["datasets"]}
    disagreeing = sorted(
        name
        for name, entry in models.items()
        if name in from_manifest and from_manifest[name] != entry["schema_hash"]
    )
    block = {
        "published_manifest_schema_hashes": {name: from_manifest.get(name) for name in models},
        "schema_hash_basis": (
            "schema_hash is over the Parquet as read, which is what you opened. "
            "published_manifest_schema_hashes are publish/export.py's, over the warehouse tables "
            "the export was written from, copied from published/manifest.json unmodified. Compare "
            "the second against the manifest in the bucket and the first against the files."
        ),
    }
    if disagreeing:
        block["schema_hash_disagreements"] = disagreeing
        block["schema_hash_disagreements_because"] = (
            "HUGEINT has no Parquet type, so DuckDB writes those columns as DOUBLE and the two "
            "hashes differ for a schema that is otherwise identical. A count read from this "
            "export is a float. Expected, and not a sign that the export is corrupt."
        )
    return block


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_pack(target_name: str, duckdb_path: Path | None = None) -> tuple[dict, list[str]]:
    manifest = pack_inputs.load_manifest()
    all_models = pack_inputs.models(manifest)
    sources = pack_inputs.registry()
    variables = pack_inputs.dbt_vars()
    published = pack_inputs.published_manifest()
    prose, prose_revision = pack_prose.load_prose()
    pack_prose.validate_shape(prose, TARGET_NAMES)

    warnings: list[str] = []
    with open_target(target_name, all_models, duckdb_path) as target:
        facts = TargetFacts(
            target_name=target.name,
            columns={
                name: [column for column, _ in target.columns(name)] for name in target.model_names
            },
            adrs=pack_inputs.adr_ids(),
            sources=sources,
        )
        selected = pack_prose.select_for_target(prose, facts)
        derived_joins = pack_inputs.declared_joins(manifest)
        pack_prose.check_join_duplication(selected["joins"], derived_joins)
        pack_prose.check_class_three_examples(selected["refusals"], selected["examples"])
        selected["refusals"] = pack_prose.point_at_this_target_examples(
            selected["refusals"], selected["examples"]
        )

        required_ids = {
            entry["id"] for entry in selected["refusals"] if entry["class"] == "misnormalised"
        }
        pack = {
            "spec_version": spec_version(),
            "pack_version": PACK_VERSION,
            "target": target.name,
            "generated_at": _now(),
            "prose_revision": prose_revision,
            "identity": build_identity_block(target, prose, sources, published),
            "build": build_build_block(target, manifest, sources, variables),
            "models": build_models_block(target, all_models, warnings),
            "joins": build_joins_block(
                derived_joins, selected["joins"], target, pack_inputs.unique_columns(manifest)
            ),
            "freshness": build_freshness_block(target, published),
            "traps": selected["traps"],
            "refusals": {
                "no_ground_truth": pack_render._flat(prose["preamble"]["no_ground_truth"]),
                "census_exception": pack_render._flat(prose["preamble"]["census_exception"]),
                "closed_world_rule": pack_render._flat(prose["preamble"]["closed_world_rule"]),
                "entries": selected["refusals"],
            },
            "disclosures": selected["disclosures"],
            "examples": build_examples_block(target, selected["examples"], required_ids),
            "integrity": build_integrity_block(target, manifest, prose, published),
        }
    return pack, warnings


# ---------------------------------------------------------------------------
# Drift: does an existing pack still describe the target? (section 8, PLAN-6 step 3)
# ---------------------------------------------------------------------------


def disagreements(pack: dict, live: dict) -> list[str]:
    """Every way a written pack can disagree with what is live. Pure, so it is testable.

    `live` carries the target's name, its prose revision, its spec version and
    its schema hash per model. Row counts are deliberately not compared: they
    move every time ingestion runs, and a gate that fires daily is a gate
    someone switches off.
    """
    if pack.get("target") != live["target"]:
        return [f"this is a {pack.get('target')} pack, not a {live['target']} one"]

    problems: list[str] = []
    if pack.get("prose_revision") != live["prose_revision"]:
        problems.append(
            f"prose revision {pack.get('prose_revision')} in the pack against "
            f"{live['prose_revision']} on disk: prose.yml moved and the pack was not regenerated"
        )
    if pack.get("spec_version") != live["spec_version"]:
        problems.append(
            f"spec version {pack.get('spec_version')} in the pack against "
            f"{live['spec_version']} on disk: the contract moved and the pack was not regenerated"
        )

    recorded = pack.get("integrity", {}).get("models", {})
    missing = [name for name in live["models"] if name not in recorded]
    extra = [name for name in recorded if name not in live["models"]]
    if missing:
        problems.append(f"the target holds models the pack does not describe: {sorted(missing)}")
    if extra:
        problems.append(f"the pack describes models the target does not hold: {sorted(extra)}")
    for name, live_hash in live["models"].items():
        if name in recorded and recorded[name]["schema_hash"] != live_hash:
            problems.append(
                f"{name}: schema hash {recorded[name]['schema_hash']} in the pack against "
                f"{live_hash} live. A column was added, removed, renamed, retyped or reordered."
            )
    return problems


def check_pack(pack_path: Path, target_name: str, duckdb_path: Path | None = None) -> list[str]:
    """Compare a written pack against the live target. Returns the disagreements.

    This is the half of section 8 that a consumer is told to do and that CI does
    for them: a pack whose integrity block disagrees with the target describes
    something the target does not contain.
    """
    if not pack_path.exists():
        return [f"no pack at {pack_path}. Run `make context-pack`."]
    pack = json.loads(pack_path.read_text())
    _, prose_revision = pack_prose.load_prose()
    manifest = pack_inputs.load_manifest()
    all_models = pack_inputs.models(manifest)
    with open_target(target_name, all_models, duckdb_path) as target:
        live = {
            "target": target_name,
            "prose_revision": prose_revision,
            "spec_version": spec_version(),
            "models": {name: target.schema_hash(name) for name in target.model_names},
        }
    return disagreements(pack, live)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def write_pack(pack: dict, markdown: str, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"context_pack.{pack['target']}.json"
    md_path = output_dir / f"context_pack.{pack['target']}.md"
    json_path.write_text(json.dumps(pack, indent=2, sort_keys=False) + "\n")
    md_path.write_text(markdown)
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the context pack for one target (PLAN-6 step 2)."
    )
    parser.add_argument("--target", default="duckdb", choices=TARGET_NAMES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--token-budget",
        type=int,
        default=DEFAULT_TOKEN_BUDGET,
        help=f"markdown budget, estimated at {pack_render.CHARS_PER_TOKEN} characters per token",
    )
    parser.add_argument("--duckdb-path", type=Path, default=None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write: compare the pack on disk against the live target and exit nonzero "
        "on drift",
    )
    args = parser.parse_args()

    try:
        if args.check:
            pack_path = args.output_dir / f"context_pack.{args.target}.json"
            problems = check_pack(pack_path, args.target, args.duckdb_path)
            if problems:
                print(f"The {args.target} pack disagrees with the target:")
                for problem in problems:
                    print(f"  - {problem}")
                sys.exit(3)
            print(f"{pack_path} agrees with the live {args.target} target.")
            return

        pack, warnings = build_pack(args.target, args.duckdb_path)
        markdown, report = pack_render.render(pack, args.token_budget)
        json_path, md_path = write_pack(pack, markdown, args.output_dir)
    except (
        GenerationError,
        ProseError,
        PackInputError,
        TargetError,
        pack_render.BudgetExceeded,
    ) as error:
        sys.exit(f"\ncontext pack generation failed.\n\n{error}\n")

    refusals = pack["refusals"]["entries"]
    columns = sum(len(model["columns"]) for model in pack["models"])
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(
        f"  {len(pack['models'])} models, {columns} columns, "
        f"{len(refusals)} refusals, {len(pack['disclosures'])} disclosures, "
        f"{len(pack['traps'])} traps, {len(pack['examples'])} verified examples, "
        f"{len(pack['joins'])} joins"
    )
    print(
        f"  markdown about {report['estimated_tokens']} tokens against a budget of "
        f"{report['budget']}"
    )
    if report["dropped"]:
        print(f"  dropped to fit: {', '.join(report['dropped'])}")
    if warnings:
        print(f"\n{len(warnings)} warnings. These are a to-do list for the yml, not pack fields:")
        for warning in warnings:
            print(f"  - {warning}")


if __name__ == "__main__":
    main()
