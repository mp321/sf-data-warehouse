"""The registry agrees with everything downstream of it, or this fails.

PLAN-5 step 4 collapsed the two dataset lists into one: `vars.pipeline_sources`
in `dbt/dbt_project.yml`, read by dbt natively and by
`ingestion/dataset_registry.py` through PyYAML. That removes the failure this
file's first two tests would otherwise have to catch, because there is no
longer a second list to disagree with.

What it does not remove is the rest of the surface a dataset has to exist on.
A registry entry needs a dbt source table, a staging model and an ingestion
fixture, and none of those are the same file. Each of them fails on its own if
you forget it, and each of them fails late: dbt at parse, `make ci-build` at
the ingest step, several minutes in. These tests fail in a tenth of a second at
the front of `make check` and say which file is missing what.

The reverse direction matters as much and is easier to get wrong: a raw table
declared to dbt but absent from the registry is a table nothing ingests, and
nothing else in the project notices. `test_every_declared_raw_source_is_in_the_registry`
is the only thing that does.
"""

from pathlib import Path

import pytest
import yaml

import dataset_registry

REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_MODELS = REPO_ROOT / "dbt" / "models"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "socrata"

TIERS = ("core", "reference", "demoted")

# Raw tables that dbt declares as sources but that ingest.py does not pull from
# an API, so they are correctly absent from the registry. raw_ingest_runs is
# written by ingest.py itself, one row per run, out of the run manifests.
NON_REGISTRY_RAW_TABLES = {"raw_ingest_runs"}


def declared_source_tables() -> set[str]:
    """Every table name under the raw_datasf source, across all source ymls."""
    tables = set()
    for path in DBT_MODELS.rglob("_*__sources.yml"):
        doc = yaml.safe_load(path.read_text()) or {}
        for source in doc.get("sources", []):
            if source.get("name") != "raw_datasf":
                continue
            for table in source.get("tables", []):
                tables.add(table["name"])
    return tables


def staging_model_names() -> set[str]:
    return {path.stem for path in DBT_MODELS.rglob("stg_*.sql")}


@pytest.fixture(scope="module")
def registry() -> dict:
    return dataset_registry.DATASETS


def test_registry_is_not_empty(registry):
    assert registry, "the registry loaded but is empty"


def test_every_entry_has_a_dbt_source_table(registry):
    declared = declared_source_tables()
    missing = {name: cfg["table"] for name, cfg in registry.items() if cfg["table"] not in declared}
    assert not missing, (
        f"registry entries with no dbt source: {missing}. Add the table under "
        "sources[raw_datasf].tables in dbt/models/staging/datasf/_datasf__sources.yml."
    )


def test_every_declared_raw_source_is_in_the_registry(registry):
    registered = {cfg["table"] for cfg in registry.values()}
    orphans = declared_source_tables() - registered - NON_REGISTRY_RAW_TABLES
    assert not orphans, (
        f"raw tables declared to dbt that nothing ingests: {sorted(orphans)}. Either add "
        "them to vars.pipeline_sources in dbt/dbt_project.yml or to "
        "NON_REGISTRY_RAW_TABLES here, with a reason."
    )


def test_every_entry_has_a_staging_model(registry):
    models = staging_model_names()
    missing = {
        name: cfg["staging_model"]
        for name, cfg in registry.items()
        if cfg["staging_model"] not in models
    }
    assert not missing, f"registry entries naming a staging model that does not exist: {missing}"


def test_every_entry_has_a_fixture(registry):
    """`make ci-build` runs from fixtures, so a missing one is a broken gate.

    ingest.py exits with "No fixture for ..." on a miss, which is a clear
    message arriving after the whole CI pipeline has started. This is the same
    check, at the front.
    """
    missing = [name for name in registry if not (FIXTURE_DIR / f"{name}.json").exists()]
    assert not missing, (
        f"registry entries with no fixture in tests/fixtures/socrata/: {missing}. "
        "See tests/fixtures/make_fixtures.py."
    )


def test_tiers_and_freshness_slas_agree(registry):
    """A tier is a claim about whether staleness counts, so the two must match.

    Core sources are the ones a stale load is a failure for, so they carry an
    SLA. Reference and demoted sources do not: ADR-3's demoted tier reports
    staleness and never counts it, and a boundary set that changes every
    several years has nothing to be late for.
    """
    for name, cfg in registry.items():
        assert cfg["tier"] in TIERS, f"{name} has tier {cfg['tier']!r}, expected one of {TIERS}"
        sla = cfg.get("stale_after_hours")
        if cfg["tier"] == "core":
            assert isinstance(sla, int) and sla > 0, (
                f"{name} is tier core and needs a positive stale_after_hours, got {sla!r}"
            )
        else:
            assert sla is None, (
                f"{name} is tier {cfg['tier']} and must have stale_after_hours: null, got {sla!r}. "
                "A threshold on a source nothing calls stale is a number nobody reads."
            )


def test_transport_fields_match_the_api(registry):
    for name, cfg in registry.items():
        if cfg.get("api") == "tigerweb":
            assert "socrata_id" not in cfg, f"{name} is a tigerweb source and cannot have one"
        else:
            assert cfg.get("socrata_id"), (
                f"{name} has no socrata_id and no api: tigerweb. One or the other."
            )


def test_geometry_shape_matches_kind(registry):
    """spatial.py dispatches on `kind` and then indexes into `geometry`.

    Getting the pair wrong is a KeyError partway through `make spatial`, after
    it has already written part of the derived zone.
    """
    for name, cfg in registry.items():
        spec = cfg["geometry"]
        if cfg["kind"] == "point":
            flat = {"latitude", "longitude"} <= spec.keys()
            geojson = "geojson_point" in spec
            assert flat or geojson, (
                f"{name} is a point and needs either latitude and longitude or "
                f"geojson_point, got {sorted(spec)}"
            )
        else:
            required = {"boundary_set", "geojson", "boundary_id", "boundary_name"}
            assert required <= spec.keys(), (
                f"{name} is a polygon and is missing {sorted(required - spec.keys())}"
            )


def test_a_malformed_entry_raises_rather_than_loading(tmp_path):
    """The validation in load_registry is load bearing, so it is tested.

    Every consumer imports DATASETS at module scope, so a bad entry has to
    stop the process at import rather than surface as a KeyError once ingestion
    has already written Parquet.
    """
    broken = tmp_path / "dbt_project.yml"
    broken.write_text(yaml.safe_dump({"vars": {"pipeline_sources": [{"name": "half_an_entry"}]}}))
    with pytest.raises(RuntimeError, match="half_an_entry"):
        dataset_registry.load_registry(broken)


def test_a_missing_registry_raises_with_a_useful_message(tmp_path):
    with pytest.raises(RuntimeError, match="dataset registry not found"):
        dataset_registry.load_registry(tmp_path / "nope.yml")
