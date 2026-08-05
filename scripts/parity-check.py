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

Usage:
    python scripts/parity-check.py                       # stg_datasf__311_cases
    python scripts/parity-check.py --model dim_neighborhood --key analysis_neighborhood
    python scripts/parity-check.py --all-staging

Required environment (load with `set -a; source .env; set +a`):
    GCP_PROJECT_ID, GOOGLE_APPLICATION_CREDENTIALS
Optional:
    DUCKDB_PATH   local warehouse (default: data/sf.duckdb)
    BQ_DATASET    dataset holding the dbt models (default: dbt_dev)
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
        "--duckdb-path",
        type=Path,
        default=None,
        help="local warehouse (default: $DUCKDB_PATH or data/sf.duckdb)",
    )
    args = parser.parse_args()

    project = os.environ.get("GCP_PROJECT_ID")
    if not project:
        sys.exit("Missing GCP_PROJECT_ID. Load it with `set -a; source .env; set +a`.")
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
