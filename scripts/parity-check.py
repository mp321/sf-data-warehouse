"""Compare a model's rows between DuckDB and BigQuery. PLAN-4 step 3, ADR-1.

ADR-1 says every model compiles on both engines. Compiling is not agreeing, and
CI only proves the first: it renders the SQL for BigQuery without connecting to
it. This is the other half. It reads the same model out of both warehouses and
tells you whether they hold the same rows.

Why the comparison happens in Python rather than as a hash on each engine:
a `md5(concat(...))` computed inside each warehouse compares two engines'
*formatting* of a value as much as the value. DuckDB renders a float, a
timestamp and a NULL differently from BigQuery, so that design reports
differences that are not there and buries the ones that are. Pulling both sides
into Python and canonicalising per type compares the values.

Order independence comes from XOR-ing the per-row digests, so neither side has
to sort, and a duplicated row still changes the answer (unlike a sum of
distinct hashes, where two identical rows cancel under XOR only if they are
identical, which is exactly the case a grain test already covers).

The per-column digests exist so a failure localises. A single whole-row hash
tells you the engines disagree; the column digests tell you which column, which
is the difference between a finding and a fix.

`--columns` is the other half, and it compares something else: the column sets
of the raw and derived tables the models are built on, rather than the rows of
one model. PLAN-7 step 2, and the reason it exists is a defect rather than a
worry. DuckDB reads the raw zone with `union_by_name`, so a column present in
only some Parquet files is present there; a BigQuery external table built with
`autodetect` infers one schema from a sampled file and can be missing it. On
2026-08-05 that cost `stg_datasf__building_permits` five columns and a red
`make build-bigquery`, surfacing as `Unrecognized name: unit_suffix` several
steps downstream of the cause. `ingestion/load.py` now passes an explicit union
schema; this is what notices if that ever stops being true.

**`--columns` compares BigQuery against the zone, not against the local DuckDB
file.** That is deliberate and it is the opposite of what the row comparison
does. There is one zone at a time (CLAUDE.md, ADR-9): the local warehouse holds
whatever zone `make load` last read, so if that was `data/raw` while BigQuery
reads `gs://.../raw`, comparing the two warehouses reports a configuration
difference as a column defect and sends the reader after the wrong thing. The
zone is what the external tables are supposed to be a view of, so the zone is
what they are checked against, read through the same `raw_zone.read_sql` that
`load.py` builds the schema from. It also means the check runs straight after
`make load-bigquery` with no local build at all.

An extra table on the BigQuery side warns rather than fails. It is not a column
disagreement: it is a table the registry no longer names, which is what a scope
cut such as ADR-10 leaves behind, and nothing references it so nothing can break
on it. A table the zone has and BigQuery does not is an error, because every
model reading it fails.

Usage:
    python scripts/parity-check.py                       # stg_datasf__311_cases
    python scripts/parity-check.py --model dim_neighborhood --key analysis_neighborhood
    python scripts/parity-check.py --all-staging
    python scripts/parity-check.py --columns             # PLAN-7 step 2

Required environment (load with `set -a; source .env; set +a`):
    GCP_PROJECT_ID, GOOGLE_APPLICATION_CREDENTIALS
Optional:
    DUCKDB_PATH      local warehouse (default: data/sf.duckdb)
    BQ_DATASET       dataset holding the dbt models (default: dbt_dev)
    BQ_RAW_DATASET, BQ_DERIVED_DATASET, RAW_ZONE_URI, DERIVED_ZONE_URI
                     read by --columns, exactly as ingestion/load.py reads them
"""

import argparse
import hashlib
import math
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

# `ingestion/` is a directory of scripts rather than a package, so it is not
# importable from here without this. tests/conftest.py does the same thing and
# its docstring carries the argument for why that arrangement stays. Importing
# is the point rather than a convenience: --columns has to read the zone through
# the same reader load.py writes the schema from, and a second copy of the read
# would be free to disagree with the first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))

import derived_zone
import load
import raw_zone
import remote
from dataset_registry import DATASETS

# Models compared by --all-staging. The point datasets are the interesting ones,
# because they are the models that join to the derived zone and carry the casts
# the dispatch macros exist for.
STAGING_MODELS = (
    ("stg_datasf__311_cases", "case_id"),
    ("stg_datasf__building_permits", "permit_record_id"),
    ("stg_datasf__business_locations", "business_location_id"),
    ("stg_datasf__film_locations", "film_location_id"),
    ("stg_spatial__point_geography", "row_key"),
)

# Number of differing keys to print per column. Enough to see a pattern, few
# enough that a systematic difference does not scroll the summary away.
EXAMPLES = 5


def canonical(value: object) -> str:
    """One string per value, meaning the same thing on both engines.

    Every branch here is a difference the engines genuinely have, and each one
    would otherwise show up as a false mismatch:

      Decimal vs float   BigQuery returns NUMERIC as Decimal, DuckDB as float.
      datetime tzinfo    BigQuery timestamps come back UTC-aware, DuckDB naive.
      float repr         0.1 + 0.2 formats differently in the two client libs,
                         so floats are rounded to 9 decimal places, which is
                         finer than any coordinate in this warehouse needs
                         (9 places is about 0.1 mm) and coarser than the noise.
      bool vs int        DuckDB hands back True, BigQuery sometimes 1.
    """
    if value is None:
        return "\x00NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        value = float(value)

    if isinstance(value, float):
        text = "\x00NAN" if math.isnan(value) else f"{value:.9f}"
    elif isinstance(value, datetime):
        text = value.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
    elif isinstance(value, date):
        text = value.isoformat()
    elif isinstance(value, (bytes, bytearray)):
        text = value.hex()
    else:
        text = str(value)
    return text


def digest(parts: list[str]) -> bytes:
    return hashlib.md5("\x1f".join(parts).encode()).digest()


def _xor(into: bytearray, other: bytes) -> None:
    for i, byte in enumerate(other):
        into[i] ^= byte


def fingerprint(columns: list[str], rows: list[tuple], key_index: int) -> dict:
    """Whole-relation digest, per-column digests, and the key to value maps.

    The per-column digest includes the key, so a value that moved from one row
    to another still changes it. Without that, swapping two rows' statuses would
    leave every column digest untouched.
    """
    whole = bytearray(16)
    per_column = {name: bytearray(16) for name in columns}
    values: dict[str, dict[str, str]] = {name: {} for name in columns}

    for row in rows:
        canonical_row = [canonical(value) for value in row]
        _xor(whole, digest(canonical_row))
        key = canonical_row[key_index]
        for name, text in zip(columns, canonical_row, strict=True):
            _xor(per_column[name], digest([key, text]))
            values[name][key] = text

    return {
        "rows": len(rows),
        "whole": bytes(whole).hex(),
        "columns": {name: bytes(value).hex() for name, value in per_column.items()},
        "values": values,
    }


def fetch_duckdb(model: str, path: Path) -> tuple[list[str], list[tuple]]:
    with duckdb.connect(str(path), read_only=True) as con:
        cursor = con.execute(f'select * from main."{model}"')
        columns = [d[0] for d in cursor.description]
        return columns, cursor.fetchall()


def fetch_bigquery(model: str, project: str, dataset: str) -> tuple[list[str], list[tuple]]:
    from google.cloud import bigquery  # noqa: PLC0415  (deferred, as in load.py)

    client = bigquery.Client(project=project)
    job = client.query(f"select * from `{project}.{dataset}.{model}`")
    result = job.result()
    columns = [field.name for field in result.schema]
    return columns, [tuple(row.values()) for row in result]


def compare(model: str, key: str, duckdb_path: Path, project: str, dataset: str) -> bool:
    """True when the two engines hold the same rows. Prints the difference if not."""
    print(f"\n{model}")
    left_columns, left_rows = fetch_duckdb(model, duckdb_path)
    right_columns, right_rows = fetch_bigquery(model, project, dataset)

    if set(left_columns) != set(right_columns):
        only_left = sorted(set(left_columns) - set(right_columns))
        only_right = sorted(set(right_columns) - set(left_columns))
        print(f"  FAIL column sets differ. duckdb only: {only_left}. bigquery only: {only_right}")
        return False

    # Compare in one column order, since select * is not ordered identically by
    # the two engines and a positional mismatch would fail for the wrong reason.
    order = sorted(left_columns)
    if key not in order:
        print(f"  FAIL key column {key} is not in the model")
        return False
    left_map = [left_columns.index(name) for name in order]
    right_map = [right_columns.index(name) for name in order]
    left = fingerprint(
        order, [tuple(row[i] for i in left_map) for row in left_rows], order.index(key)
    )
    right = fingerprint(
        order, [tuple(row[i] for i in right_map) for row in right_rows], order.index(key)
    )

    print(f"  duckdb   {left['rows']:>9} rows  {left['whole']}")
    print(f"  bigquery {right['rows']:>9} rows  {right['whole']}")

    if left["whole"] == right["whole"] and left["rows"] == right["rows"]:
        print("  PASS identical")
        return True

    if left["rows"] != right["rows"]:
        print(f"  row counts differ by {abs(left['rows'] - right['rows'])}")
        left_keys = set(left["values"][key])
        right_keys = set(right["values"][key])
        for label, missing in (
            ("bigquery is missing", sorted(left_keys - right_keys)),
            ("duckdb is missing", sorted(right_keys - left_keys)),
        ):
            if missing:
                print(f"    {label} {len(missing)}: {missing[:EXAMPLES]}")

    differing = [name for name in order if left["columns"][name] != right["columns"][name]]
    print(f"  columns that differ ({len(differing)}): {differing}")
    for name in differing:
        shared = set(left["values"][name]) & set(right["values"][name])
        examples = [
            k for k in sorted(shared) if left["values"][name][k] != right["values"][name][k]
        ]
        print(f"    {name}: {len(examples)} shared key(s) disagree")
        for k in examples[:EXAMPLES]:
            here = left["values"][name][k]
            there = right["values"][name][k]
            print(f"      {key}={k}: duckdb={here!r} bigquery={there!r}")
    return False


# ---------------------------------------------------------------------------
# --columns: the zone's column sets against BigQuery's. PLAN-7 step 2.
# ---------------------------------------------------------------------------

# `raw_ingest_runs` is left out on purpose, and named in the output so that is
# visible rather than looking like an oversight. It is the one raw table that is
# materialized rather than external (ADR-9: the manifests are JSON arrays, not
# newline-delimited JSON), and `raw_zone.runs_read_sql` declares its columns
# instead of inferring them. This check exists to catch two readers inferring
# differently, and that table has no inference in it to disagree about.
SKIPPED_TABLES = (load.RUNS_TABLE,)


def _column_set(con: duckdb.DuckDBPyConnection, relation: str) -> set[str]:
    """Column names of a read_parquet fragment, without scanning the rows.

    `describe` reads Parquet footers rather than row groups, so this costs a
    listing per table even on the largest ones.
    """
    return {row[0] for row in con.execute(f"describe select * from {relation}").fetchall()}


def zone_column_sets(raw_root, derived_root, raw_dataset: str, derived_dataset: str) -> dict:
    """What the zones hold, keyed `dataset.table` the way BigQuery names them.

    Deliberately re-derived here rather than imported from `load._union_columns`.
    Both go through `raw_zone.read_sql`, so it is the same reader either way, but
    a check that calls the code under test cannot notice that code being wrong.
    """
    columns: dict[str, set[str]] = {}
    with raw_zone.connect(raw_root) as con:
        remote.register(con, raw_root, derived_root)
        for meta in DATASETS.values():
            table = meta["table"]
            if raw_zone.has_data(table, raw_root):
                columns[f"{raw_dataset}.{table}"] = _column_set(
                    con, raw_zone.read_sql(table, raw_root)
                )
        for table in load.DERIVED_TABLES:
            if derived_zone.has_data(table, derived_root):
                columns[f"{derived_dataset}.{table}"] = _column_set(
                    con, derived_zone.read_sql(table, derived_root)
                )
    return columns


def bigquery_column_sets(project: str, datasets: tuple[str, ...]) -> dict:
    """What BigQuery thinks it has, from INFORMATION_SCHEMA rather than from get_table.

    INFORMATION_SCHEMA because it answers for a whole dataset in one query, which
    is also how a table present on one side and absent on the other gets noticed
    rather than raising NotFound halfway through.
    """
    from google.cloud import bigquery  # noqa: PLC0415  (deferred, as in load.py)

    client = bigquery.Client(project=project)
    columns: dict[str, set[str]] = {}
    for dataset in datasets:
        query = (
            f"select table_name, column_name from `{project}.{dataset}`.INFORMATION_SCHEMA.COLUMNS"
        )
        for row in client.query(query).result():
            columns.setdefault(f"{dataset}.{row.table_name}", set()).add(row.column_name)
    return columns


def _report_table(name: str, zone: set[str] | None, warehouse: set[str] | None) -> str:
    """One table's verdict: 'PASS', 'WARN ...' or 'FAIL ...', naming every column."""
    if zone is None:
        return (
            f"WARN {len(warehouse)} columns in BigQuery, nothing in the zone. "
            "Not in the dataset registry: a leftover from a scope cut such as "
            "ADR-10, and nothing references it. Drop it by hand when convenient."
        )
    if warehouse is None:
        return (
            f"FAIL {len(zone)} columns in the zone, no table in BigQuery. "
            "Every model reading it fails. Run `make load-bigquery`."
        )
    missing = sorted(zone - warehouse)
    extra = sorted(warehouse - zone)
    if not missing and not extra:
        return f"PASS {len(zone)} columns"
    lines = [f"FAIL {len(zone)} in the zone, {len(warehouse)} in BigQuery"]
    if missing:
        lines.append(f"     in the zone, missing from BigQuery: {', '.join(missing)}")
    if extra:
        lines.append(f"     in BigQuery, missing from the zone: {', '.join(extra)}")
    lines.append("     Re-run `make load-bigquery`; if that does not fix it, the")
    lines.append("     external schema is not being built from the zone's union.")
    return "\n".join(lines)


def compare_columns(project: str) -> bool:
    """True when every zone table's column set matches its BigQuery table's.

    Warnings do not fail: an extra BigQuery table cannot break a model. A
    disagreement on a shared table does, and so does a missing table.
    """
    raw_root = raw_zone.raw_root()
    derived_root = derived_zone.derived_root()
    raw_dataset = os.environ.get("BQ_RAW_DATASET", load.RAW_SCHEMA)
    derived_dataset = os.environ.get("BQ_DERIVED_DATASET", load.DERIVED_SCHEMA)

    print("column sets, the zone against BigQuery")
    print(f"  raw zone      {raw_root}")
    print(f"  derived zone  {derived_root}")
    print(f"  bigquery      {project}.{{{raw_dataset},{derived_dataset}}}\n")

    zone = zone_column_sets(raw_root, derived_root, raw_dataset, derived_dataset)
    warehouse = bigquery_column_sets(project, (raw_dataset, derived_dataset))
    skipped = {f"{raw_dataset}.{table}" for table in SKIPPED_TABLES}

    failed = []
    for name in sorted(set(zone) | set(warehouse)):
        if name in skipped:
            print(f"{name:44s} SKIP see SKIPPED_TABLES")
            continue
        verdict = _report_table(name, zone.get(name), warehouse.get(name))
        print(f"{name:44s} {verdict}")
        if verdict.startswith("FAIL"):
            failed.append(name)

    print(f"\nsummary\n  {len(zone)} zone table(s) checked, {len(failed)} disagreeing")
    for name in failed:
        print(f"  FAIL {name}")
    if not failed:
        print("  PASS every table's column set agrees")
    return not failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare a dbt model across DuckDB and BigQuery.")
    parser.add_argument("--model", default="stg_datasf__311_cases", help="model to compare")
    parser.add_argument("--key", default="case_id", help="grain column of that model")
    parser.add_argument(
        "--all-staging",
        action="store_true",
        help="compare every point staging model instead of one",
    )
    parser.add_argument(
        "--columns",
        action="store_true",
        help="compare the zone's column sets against the BigQuery tables (PLAN-7 step 2) "
        "instead of comparing rows",
    )
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=None,
        help="local warehouse (default: $DUCKDB_PATH or data/sf.duckdb)",
    )
    args = parser.parse_args()

    project = os.environ.get("GCP_PROJECT_ID")
    if not project:
        sys.exit("Missing GCP_PROJECT_ID. Load it with `set -a; source .env; set +a`.")
    if args.columns:
        sys.exit(0 if compare_columns(project) else 1)

    dataset = os.environ.get("BQ_DATASET", "dbt_dev")
    duckdb_path = args.duckdb_path or Path(os.environ.get("DUCKDB_PATH", "data/sf.duckdb"))

    targets = list(STAGING_MODELS) if args.all_staging else [(args.model, args.key)]
    results = {model: compare(model, key, duckdb_path, project, dataset) for model, key in targets}

    print("\nsummary")
    for model, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'} {model}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
