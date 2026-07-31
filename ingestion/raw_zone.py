"""Layout of the Parquet raw zone, and the only code that reads it.

Layout (ADR-4):

    data/raw/<table>/ingest_date=YYYY-MM-DD/part-<run_id>-<seq>.parquet
    data/raw/<table>/_runs/<run_id>.json

`<table>` is the registry's `table` value (`raw_311_cases`), not the friendly
dataset name, so one directory maps to exactly one dbt source table on both
warehouses.

Two invariants the rest of the pipeline depends on:

  - Every data column is a STRING. The zone stores what the API sent,
    unparsed. Typing happens in dbt (ADR-1).
  - Files are only ever added. Nothing rewrites or deletes a partition except
    `ingest.py --full-refresh`, which swaps the whole tree atomically.

`ingest_date` is a hive partition key, so it lives in the directory name and
NOT inside the files. Every reader therefore has to ask for hive
partitioning, which is why `read_sql()` below is the single reader: get the
options wrong in one place and you get a silently different table.

Reads go through DuckDB rather than pyarrow. DuckDB is the canonical engine
(ADR-1) and its Parquet reader already handles hive partitioning and
schema-drifting files correctly; doing the same in pyarrow means unifying
schemas by hand. Writes use pyarrow, because building a table from a list of
dicts against a fixed schema is what pyarrow is for.
"""

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

# Directory names. The leading underscore on _runs is load bearing: DuckDB's
# **/*.parquet glob ignores it because it holds JSON, and pyarrow's dataset
# discovery ignores "_" and "." prefixes by default. Renaming it to something
# without the underscore would make it look like a partition.
RUNS_DIRNAME = "_runs"
PARTITION_KEY = "ingest_date"

# Socrata's :updated_at, after sanitize_column(). The watermark column.
WATERMARK_COLUMN = "_socrata_updated_at"

# Metadata columns ingestion adds to every row. Kept STRING like everything
# else so the raw contract has no exceptions to remember.
INGESTED_AT_COLUMN = "_ingested_at"
RUN_ID_COLUMN = "_ingest_run_id"


def raw_root() -> Path:
    """Root of the raw zone. RAW_ZONE_DIR exists so CI can point at a fixture tree."""
    return Path(os.environ.get("RAW_ZONE_DIR", "data/raw"))


def dataset_dir(table: str, root: Path | None = None) -> Path:
    return (root or raw_root()) / table


def runs_dir(table: str, root: Path | None = None) -> Path:
    return dataset_dir(table, root) / RUNS_DIRNAME


def new_run_id(now: datetime | None = None) -> str:
    """Compact UTC timestamp, e.g. 20260730T203312Z.

    Sorts lexically in time order, is filename safe, and is unique per run at
    second resolution. It ends up in the file name, in the `_ingest_run_id`
    column, and in the run manifest, which is what lets a row in the warehouse
    be traced back to the file and the API call that produced it.
    """
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _sql_literal(value: object) -> str:
    return str(value).replace("'", "''")


def read_sql(table: str, root: Path | None = None) -> str:
    """SQL fragment that reads one dataset's entire Parquet tree.

    Three options are load bearing:

      hive_partitioning       recovers ingest_date from the directory names,
                              since it is not stored in the files.
      hive_types_autocast=0   keeps ingest_date a VARCHAR. DuckDB otherwise
                              infers DATE, which would put one non-STRING
                              column in an all-STRING raw table and break the
                              contract staging models are written against.
      union_by_name           Socrata omits null fields per record, so a
                              batch's column set depends on which rows it
                              contained and files genuinely differ between
                              runs. Positional union would silently misalign
                              columns across files.
    """
    pattern = dataset_dir(table, root) / "**" / "*.parquet"
    return (
        f"read_parquet('{_sql_literal(pattern)}'"
        ", hive_partitioning = true"
        ", hive_types_autocast = 0"
        ", union_by_name = true)"
    )


def runs_read_sql(root: Path | None = None) -> str:
    """SQL fragment reading every dataset's run manifests as one table.

    Columns are declared rather than inferred: `error` is null on every
    successful run, and read_json_auto would type a directory of
    all-successful manifests differently from one containing a failure.
    """
    pattern = (root or raw_root()) / "*" / RUNS_DIRNAME / "*.json"
    columns = {
        "run_id": "VARCHAR",
        "dataset": "VARCHAR",
        "table_name": "VARCHAR",
        "ingest_date": "VARCHAR",
        "started_at": "TIMESTAMP",
        "finished_at": "TIMESTAMP",
        "watermark_in": "VARCHAR",
        "watermark_out": "VARCHAR",
        "rows_written": "BIGINT",
        "files_written": "BIGINT",
        "mode": "VARCHAR",
        "status": "VARCHAR",
        "error": "VARCHAR",
    }
    spec = ", ".join(f"'{name}': '{dtype}'" for name, dtype in columns.items())
    return f"read_json('{_sql_literal(pattern)}', columns = {{{spec}}}, format = 'array')"


def connect() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB used purely as a Parquet reader."""
    return duckdb.connect()


def has_data(table: str, root: Path | None = None) -> bool:
    return any(dataset_dir(table, root).glob(f"{PARTITION_KEY}=*/*.parquet"))


def read_watermark(table: str, root: Path | None = None) -> str | None:
    """Newest _socrata_updated_at in the zone, or None if it is empty.

    This is a lexical max over strings, and it is correct only because Socrata
    renders :updated_at as a fixed-width UTC instant
    (2026-07-30T20:33:12.895Z), so string order equals time order. It is the
    load-bearing assumption in incremental ingestion: if the format ever
    varies in width or offset, this returns a plausible wrong answer and rows
    get skipped silently rather than erroring.

    Casting to a timestamp instead would be no safer, because a malformed
    value would then become NULL and drop out of max() just as quietly.
    """
    if not has_data(table, root):
        return None
    with connect() as con:
        row = con.execute(f"select max({WATERMARK_COLUMN}) from {read_sql(table, root)}").fetchone()
    return row[0] if row else None


def write_batch(
    table: str,
    rows: list[dict],
    run_id: str,
    seq: int,
    *,
    ingest_date: str | None = None,
    root: Path | None = None,
) -> Path:
    """Write one buffered batch as a single Parquet file. Returns its path.

    The schema is the union of keys present in this batch, all STRING, sorted
    for stable file layout. Rows missing a key get NULL rather than the key
    being dropped, which is what makes files with different column sets
    readable together under union_by_name.
    """
    ingest_date = ingest_date or date.today().isoformat()
    columns = sorted({column for row in rows for column in row})
    batch = pa.Table.from_pylist(rows, schema=pa.schema([(c, pa.string()) for c in columns]))

    partition = dataset_dir(table, root) / f"{PARTITION_KEY}={ingest_date}"
    partition.mkdir(parents=True, exist_ok=True)
    destination = partition / f"part-{run_id}-{seq:04d}.parquet"
    pq.write_table(batch, destination, compression="snappy")
    return destination


def write_run_manifest(table: str, manifest: dict, root: Path | None = None) -> Path:
    """Record one ingestion run next to the data it produced.

    The manifests are what let mart_pipeline_freshness distinguish "ingestion
    ran and found nothing new" from "ingestion has not run in three days". A
    run that fetches zero rows writes no Parquet file, so the data alone
    cannot tell those apart, and they mean opposite things to whoever is
    reading the freshness view.
    """
    directory = runs_dir(table, root)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{manifest['run_id']}.json"
    # Written as a one-element array so read_json can use format = 'array'
    # uniformly across files, and so a manifest is a valid JSON document on
    # its own rather than newline-delimited fragments.
    destination.write_text(json.dumps([manifest], indent=2) + "\n")
    return destination
