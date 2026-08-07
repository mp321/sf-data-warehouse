"""The three targets, and what it means to open one.

A pack describes exactly one target (spec section 2), so every question the
generator asks - which models exist, what a column's type is, what a query
returns, what a schema hashes to - is asked of a target and never of "the
warehouse". This module is where that indirection lives.

    duckdb      all 19 models, from the local DuckDB file. No credentials.
    published   the 6 marts in PUBLISHED_MARTS, read as Parquet. No credentials.
    bigquery    all 19 models, from BigQuery. Credentials, and by hand.

**Only `duckdb` can be opened today.** PLAN-6 step 2 built the generator against
the target that needs no credentials, because that is the one CI can gate on,
and the other two are declared here rather than implemented so that the model
set, the freshness source and the schema-hash policy of each are written down in
one place where the prose validator can already see them. `open_target` raises
for both with a pointer rather than pretending. An unimplemented target that
fails loudly is a smaller lie than one that silently emits a DuckDB pack under
another name.

**The schema hash is `publish/export.py`'s, imported and not reimplemented.**
PLAN-6 says reuse it, and the spec says the same in section 2. It renders DuckDB
type names, which is why it carries across `duckdb` and `published` and why the
`bigquery` target declares it absent with a reason instead.
"""

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import duckdb

from pack_inputs import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "publish"))

from export import PUBLISHED_MARTS, _schema_hash

TARGET_NAMES = ("duckdb", "published", "bigquery")


class TargetError(Exception):
    """The target cannot be opened, or cannot answer a question about itself."""


# Declared for all three, opened for one. Every field here is consulted by the
# generator or the prose validator regardless of whether the target can be
# opened, which is what keeps `applies_to: [published]` in prose.yml meaningful
# before the published target is built.
TARGETS = {
    "duckdb": {
        "models": "all",
        "freshness_from": "mart_pipeline_freshness",
        "schema_hash": True,
        "needs_credentials": False,
        "generated_by": "CI, every pull request",
    },
    "published": {
        "models": "published_marts",
        "freshness_from": "published/manifest.json",
        "schema_hash": True,
        "needs_credentials": False,
        "generated_by": "CI, every pull request",
    },
    "bigquery": {
        # No schema hash on purpose: publish/export.py renders DuckDB type
        # names, so VARCHAR against STRING would differ for a schema that is
        # identical. `make parity-columns` is the guarantee in its place, and
        # the pack says so rather than omitting the field silently (section 2).
        "models": "all",
        "freshness_from": "mart_pipeline_freshness",
        "schema_hash": False,
        "schema_hash_absent_because": (
            "publish/export.py hashes DuckDB type names, so every hash would differ for a "
            "schema that is identical. `make parity-columns` compares the zone's column "
            "sets against the BigQuery tables and is the guarantee in its place."
        ),
        "needs_credentials": True,
        "generated_by": "by hand, beside `make build-bigquery`",
    },
}


class Target:
    """One opened target. Everything the generator measures goes through here."""

    def __init__(self, name: str, connection, schema: str, model_names: list[str]):
        self.name = name
        self.connection = connection
        self.schema = schema
        self.model_names = model_names
        # Both are asked for more than once per generation, and neither can
        # change under an open read-only connection. Without this the row
        # counts are computed twice, once for the models block and once for
        # integrity, and half of them are views over the Parquet zone.
        self._columns: dict[str, list[tuple[str, str]]] = {}
        self._row_counts: dict[str, int] = {}

    def relation(self, model: str) -> str:
        return f"{self.schema}.{model}"

    def execute(self, sql: str, parameters: list | None = None) -> list[tuple]:
        return self.connection.execute(sql, parameters or []).fetchall()

    def columns(self, model: str) -> list[tuple[str, str]]:
        """(name, type) in ordinal order, from the target itself.

        From the live target and not from `catalog.json`: the catalog is written
        by `dbt docs generate` and is therefore as old as the last time someone
        ran it, while a column list that disagrees with the target is the exact
        failure the pack exists to prevent a consumer from hitting.
        """
        if model not in self._columns:
            rows = self.execute(
                "select column_name, data_type from information_schema.columns "
                "where table_schema = ? and table_name = ? order by ordinal_position",
                [self.schema, model],
            )
            if not rows:
                raise TargetError(
                    f"Model {model} is in the dbt manifest and not in the {self.name} target. "
                    "The manifest and the warehouse disagree; rebuild before generating."
                )
            self._columns[model] = [(name, dtype) for name, dtype in rows]
        return self._columns[model]

    def row_count(self, model: str) -> int:
        if model not in self._row_counts:
            self._row_counts[model] = self.execute(f"select count(*) from {self.relation(model)}")[
                0
            ][0]
        return self._row_counts[model]

    def schema_hash(self, model: str) -> str | None:
        if not TARGETS[self.name]["schema_hash"]:
            return None
        return _schema_hash(self.connection, model)


def model_set(target_name: str, all_models: dict) -> list[str]:
    """The models this target holds, in the dependency order the manifest gave.

    This is the function the whole three-pack argument rests on. The published
    target is six marts and no staging models, so a refusal citing
    `stg_spatial__polygon_h3` cannot resolve there and is not rendered into that
    pack. Getting this wrong makes every other rule in the spec quietly wrong.
    """
    rule = TARGETS[target_name]["models"]
    if rule == "all":
        return list(all_models)
    if rule == "published_marts":
        missing = [name for name in PUBLISHED_MARTS if name not in all_models]
        if missing:
            raise TargetError(
                f"PUBLISHED_MARTS names models the dbt manifest does not have: {missing}"
            )
        return [name for name in all_models if name in PUBLISHED_MARTS]
    raise TargetError(f"Unknown model rule {rule!r} for target {target_name!r}")


@contextmanager
def open_target(target_name: str, all_models: dict, duckdb_path: Path | None = None):
    if target_name not in TARGETS:
        raise TargetError(f"Unknown target {target_name!r}. One of: {', '.join(TARGET_NAMES)}")
    if target_name != "duckdb":
        raise TargetError(
            f"The {target_name} target is declared in tools/context_pack/pack_target.py and not "
            "yet implemented. PLAN-6 step 2 built the generator against duckdb, the target that "
            "needs no credentials and that CI can therefore gate on. What is missing for "
            f"{target_name} is a connection factory here and its entries in prose.yml; "
            "everything else in the generator is already target-agnostic."
        )

    path = duckdb_path or Path(os.environ.get("DUCKDB_PATH", REPO_ROOT / "data" / "sf.duckdb"))
    if not Path(path).exists():
        raise TargetError(
            f"No warehouse at {path}. Run `make build` first, or point DUCKDB_PATH at one."
        )
    connection = duckdb.connect(str(path), read_only=True)
    try:
        yield Target(
            name=target_name,
            connection=connection,
            schema="main",
            model_names=model_set(target_name, all_models),
        )
    finally:
        connection.close()
