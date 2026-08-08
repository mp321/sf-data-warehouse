"""The dataset registry, loaded from the one file that holds it.

The registry itself is `vars.pipeline_sources` in `dbt/dbt_project.yml`. This
module reads it and presents it to Python as `DATASETS`, a dict keyed by
dataset name. Every field is documented where the data is, at the top of that
vars block; nothing about a dataset is defined here.

WHY THE LIST IS OVER THERE AND NOT HERE. It used to be here, and dbt kept a
second copy of the reporting half of it. Two lists that had to agree, with
nothing checking that they did: adding a dataset to one and not the other
passed `make check` and surfaced later as a source missing from
mart_pipeline_freshness. PLAN-5 step 4 collapsed them into one list, and the
direction was forced rather than chosen. dbt cannot read an arbitrary YAML
file: its Jinja sandbox has no file access and dbt_project.yml has no include.
Python can read anything. So the single copy lives where the less capable
reader already looks, and the capable one comes to it.

The cost of that is this module and a PyYAML import on the ingestion path. The
thing it buys is that "add a dataset in one place and it is missing from the
other" is no longer a state the repo can be in, rather than a state a comment
asks you not to enter.

Renamed from `datasets.py` in PLAN-5 step 7, which closed PLAN-2. The old name
resolved only because Python puts a script's own directory on `sys.path`, so
it shadowed, and was shadowed by, the PyPI `datasets` package. See ruff.toml
for why the sibling modules beside it did not get the same treatment.
"""

from pathlib import Path

import yaml

# dbt/dbt_project.yml, relative to this file. Resolved rather than assumed to
# be relative to the working directory: ingest.py runs from the repo root, the
# tests run from anywhere pytest was started, and CI runs both.
REGISTRY_PATH = Path(__file__).resolve().parent.parent / "dbt" / "dbt_project.yml"

# Present on every entry. Absent means the entry is malformed, which is worth
# an error at import rather than a KeyError somewhere in the middle of a run
# that has already written Parquet. `api` and `socrata_id` are deliberately
# not here: a tigerweb dataset has no socrata_id, and socrata is the default
# transport, so both are optional by design.
REQUIRED_FIELDS = (
    "name",
    "table",
    "staging_model",
    "tier",
    "kind",
    "refresh",
    "grain_key",
    "geometry",
    "start_date",
    "description",
)

KINDS = ("point", "polygon")

# Whether a partition of this dataset can ever be superseded. Required rather
# than defaulted, and required in the unsafe direction: a new dataset with no
# `refresh` fails to load rather than silently becoming prunable, because the
# cost of the two mistakes is not symmetric. Calling a snapshot a delta wastes
# storage; calling a delta a snapshot offers rows for deletion that nothing can
# bring back. PLAN-9 and ADR-14.
REFRESH_KINDS = ("snapshot", "delta")

# Bounding box for validating point coordinates, in degrees. Deliberately
# loose: it is a rejection filter for null-island rows, coordinates that
# arrive swapped, and the Web Mercator metres that DataSF occasionally leaks
# into a lat/long column, not a claim about the city limits. Points on the
# Farallon Islands (part of District 1) sit at about -123.00, so the western
# edge has to reach past them.
#
# Not in the registry file: it is one constant for the whole project rather
# than a property of any dataset, and dbt has no use for it.
SF_BOUNDING_BOX = {
    "min_latitude": 37.60,
    "max_latitude": 37.93,
    "min_longitude": -123.20,
    "max_longitude": -122.28,
}


def load_registry(path: Path | str | None = None) -> dict:
    """Read the registry and return it keyed by dataset name.

    Validates on the way through. The checks are cheap and the alternative is
    a malformed entry surfacing as a KeyError partway through an ingestion
    run, so they run on every import rather than only in the tests.
    """
    path = Path(path) if path is not None else REGISTRY_PATH
    if not path.exists():
        raise RuntimeError(
            f"dataset registry not found at {path}. It is vars.pipeline_sources in "
            "dbt/dbt_project.yml; this module reads it from a fixed path relative to "
            "itself, so a missing file means ingestion/ has been moved away from dbt/."
        )

    project = yaml.safe_load(path.read_text()) or {}
    entries = (project.get("vars") or {}).get("pipeline_sources")
    if not entries:
        raise RuntimeError(f"vars.pipeline_sources is missing or empty in {path}")

    registry = {}
    for entry in entries:
        missing = [field for field in REQUIRED_FIELDS if entry.get(field) in (None, "")]
        if missing:
            raise RuntimeError(
                f"registry entry {entry.get('name', '<unnamed>')} in {path} is missing "
                f"{', '.join(missing)}"
            )
        if entry["kind"] not in KINDS:
            raise RuntimeError(
                f"registry entry {entry['name']} has kind {entry['kind']!r}; "
                f"expected one of {', '.join(KINDS)}"
            )
        if entry["refresh"] not in REFRESH_KINDS:
            raise RuntimeError(
                f"registry entry {entry['name']} has refresh {entry['refresh']!r}; "
                f"expected one of {', '.join(REFRESH_KINDS)}"
            )
        if entry["name"] in registry:
            raise RuntimeError(f"registry entry {entry['name']} is listed twice in {path}")

        # Folded YAML scalars end in a newline. Strip it once here rather than
        # leaving a trailing blank line in whatever prints the description.
        config = dict(entry)
        config["description"] = str(config["description"]).strip()
        registry[entry["name"]] = config

    return registry


DATASETS = load_registry()


def point_datasets() -> dict:
    """Registry entries that carry a single point per row."""
    return {name: cfg for name, cfg in DATASETS.items() if cfg["kind"] == "point"}


def polygon_datasets() -> dict:
    """Registry entries that carry a polygon per row."""
    return {name: cfg for name, cfg in DATASETS.items() if cfg["kind"] == "polygon"}


def snapshot_datasets() -> dict:
    """Registry entries a partition of which can ever be superseded.

    The one caller is `prune_raw.py`, and this is the whole of the filter it
    applies before it starts proving anything: a dataset that is not here is
    not looked at, not reported on as prunable, and not reachable by any flag.
    Membership is necessary and nowhere near sufficient. See ADR-14.
    """
    return {name: cfg for name, cfg in DATASETS.items() if cfg["refresh"] == "snapshot"}
