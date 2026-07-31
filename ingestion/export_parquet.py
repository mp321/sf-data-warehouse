"""Dump raw BigQuery tables to local Parquet. Opt-in, not scheduled.

Why this exists
---------------
ADR 0001 (docs/decisions/0001-warehouse-targets.md) makes Parquet under
data/ the durable raw zone and DuckDB the canonical engine. It does not
make that switch in one step, because ingest.py works and rewriting a
working loader and introducing a docs structure in the same change is how
you end up unable to tell which one broke things.

So this script is the bridge. It reads what ingest.py already landed in
BigQuery and writes it to data/*.parquet, giving you a real Parquet raw
zone to point DuckDB at without touching ingestion logic at all. Nothing
calls it automatically. No workflow runs it.

The end state is that ingest.py writes Parquet as its output of record and
BigQuery becomes a load target fed from those files, at which point this
script is deleted. Until then it is the only thing populating data/.

Usage
-----
    python ingestion/export_parquet.py 311_cases
    python ingestion/export_parquet.py --all
    python ingestion/export_parquet.py --all --out-dir data

Requires the same credentials as ingest.py:
    GCP_PROJECT_ID
    GOOGLE_APPLICATION_CREDENTIALS
Optional:
    BQ_RAW_DATASET   defaults to raw_datasf

Requires pyarrow, which is in requirements.txt as a dbt-duckdb dependency
but is imported lazily here so the rest of the ingestion path does not
depend on it.

Then point DuckDB at the result:
    duckdb data/sf.duckdb
    select count(*) from read_parquet('data/raw_311_cases.parquet');
"""

import argparse
import os
import sys
from pathlib import Path

from datasets import DATASETS

DEFAULT_OUT_DIR = "data"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required environment variable: {name}. See SETUP.md.")
    return value


def export_one(client, project: str, raw_dataset: str, name: str, out_dir: Path) -> None:
    """Export one registered dataset's raw table to Parquet.

    Columns stay STRING, matching what ingest.py lands. Typing is dbt's
    job, and doing it here would put logic in the raw zone, which ADR 0001
    forbids for the same reason ELT exists.
    """
    cfg = DATASETS[name]
    table_ref = f"{project}.{raw_dataset}.{cfg['table']}"
    destination = out_dir / f"{cfg['table']}.parquet"

    print(f"[{name}] reading {table_ref}")
    # to_arrow streams via the BigQuery Storage API when available and
    # falls back to the REST API when it is not. Either way this pulls the
    # whole table into memory, which is fine at current volumes and is the
    # first thing that will break as they grow. When it does, switch to an
    # EXTRACT job writing Parquet to GCS instead.
    table = client.list_rows(table_ref).to_arrow()

    import pyarrow.parquet as pq  # noqa: PLC0415  (lazy on purpose, see module docstring)

    pq.write_table(table, destination, compression="snappy")
    print(f"[{name}] wrote {table.num_rows} rows to {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump raw BigQuery tables to local Parquet (opt-in bridge, see ADR 0001)."
    )
    parser.add_argument(
        "datasets",
        nargs="*",
        help=f"dataset names to export: {', '.join(DATASETS)}",
    )
    parser.add_argument("--all", action="store_true", help="export every registered dataset")
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"directory for the Parquet files (default: {DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args()

    names = list(DATASETS) if args.all else args.datasets
    if not names:
        parser.error("pass one or more dataset names, or --all")
    unknown = [n for n in names if n not in DATASETS]
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(unknown)}. Valid: {', '.join(DATASETS)}")

    project = require_env("GCP_PROJECT_ID")
    require_env("GOOGLE_APPLICATION_CREDENTIALS")
    raw_dataset = os.environ.get("BQ_RAW_DATASET", "raw_datasf")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    from google.cloud import bigquery  # noqa: PLC0415

    client = bigquery.Client(project=project)

    for name in names:
        export_one(client, project, raw_dataset, name, out_dir)

    print(
        "\nDone. data/ is gitignored and is not yet durable against losing this "
        "machine; see docs/plans/0001-duckdb-parquet-migration.md."
    )


if __name__ == "__main__":
    main()
