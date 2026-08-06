"""Layout of the Parquet raw zone, and the only code that reads or writes it.

Layout (ADR-4), under `data/raw` or under a `gs://` prefix (ADR-9). The layout
is identical either way, which is what lets one reader serve both:

    <root>/<table>/ingest_date=YYYY-MM-DD/part-<run_id>-<seq>.parquet
    <root>/<table>/_runs/<run_id>.json

`<table>` is the registry's `table` value (`raw_311_cases`), not the friendly
dataset name, so one directory maps to exactly one dbt source table on both
warehouses.

Two invariants the rest of the pipeline depends on:

  - Every data column is a STRING. The zone stores what the API sent,
    unparsed. Typing happens in dbt (ADR-1).
  - Files are only ever added. Nothing rewrites or deletes a partition except
    `ingest.py --full-refresh`, which swaps the whole tree atomically, and
    which is refused on a remote zone because object storage has no rename to
    do it with. See `ingest.reject_remote_full_refresh`.

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
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

import remote

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


def raw_root() -> Path | str:
    """Root of the raw zone: a local directory, or a gs:// prefix.

    `RAW_ZONE_DIR` exists so CI can point at a fixture tree, and `RAW_ZONE_URI`
    so the zone can live in a bucket (ADR-9). A `Path` comes back for the first,
    a string for the second, and `remote.child` is what joins either kind.

    The precedence is DIR then URI, and it is load bearing rather than
    arbitrary: `make ci-build` sets `RAW_ZONE_DIR`, and it has to stay local and
    credential-free even in a shell that has sourced a `.env` with
    `RAW_ZONE_URI` in it. See ingestion/remote.py.
    """
    location = remote.zone_location("RAW_ZONE_DIR", "RAW_ZONE_URI", "data/raw")
    return location if remote.is_remote(location) else Path(location)


def dataset_dir(table: str, root: Path | str | None = None) -> Path | str:
    return remote.child(root if root is not None else raw_root(), table)


def runs_dir(table: str, root: Path | str | None = None) -> Path | str:
    return remote.child(dataset_dir(table, root), RUNS_DIRNAME)


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


def read_sql(
    table: str,
    root: Path | str | None = None,
    partitions: list[str] | None = None,
    filename: bool = False,
) -> str:
    """SQL fragment that reads one dataset's Parquet tree, or part of it.

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

    `partitions` narrows the read to a list of `ingest_date` values, as one
    glob each rather than one glob over the tree. That is what makes
    `spatial.py` incremental (PLAN-5 step 9): a scheduled run reads the
    partitions that arrived since it last ran instead of the whole zone.
    Hive partitioning still applies, so `ingest_date` comes back either way and
    a narrowed read is a subset of the full one rather than a different shape.
    An empty list is rejected rather than read as "everything", because that
    mistake would silently turn an incremental read into a full one.

    `filename` adds DuckDB's synthetic filename column, which `partition_state`
    counts distinct values of. Off by default: it is metadata rather than data,
    and no reader that is after rows wants it.
    """
    directory = dataset_dir(table, root)
    if partitions is None:
        patterns = [remote.child(directory, "**", "*.parquet")]
    elif not partitions:
        raise ValueError(f"read_sql({table!r}) got an empty partition list; pass None for all")
    else:
        patterns = [
            remote.child(directory, f"{PARTITION_KEY}={value}", "*.parquet")
            for value in sorted(partitions)
        ]
    globs = ", ".join(f"'{_sql_literal(pattern)}'" for pattern in patterns)
    return (
        f"read_parquet([{globs}]"
        ", hive_partitioning = true"
        ", hive_types_autocast = 0"
        ", union_by_name = true"
        f", filename = {'true' if filename else 'false'})"
    )


def partition_state(con, table: str, root: Path | str | None = None) -> dict[str, dict]:
    """Rows and files in each `ingest_date` partition of one raw table.

    The key incrementality is decided on (PLAN-5 step 9). The zone is
    append-only (ADR-4), so a partition whose row and file counts are both
    unchanged holds the same files it held last time, and anything derived from
    it is still correct. Counting both rather than one of them is what keeps
    that true through a local `--full-refresh`, which is the one operation that
    can replace a partition's contents rather than add to them: it would have to
    land on the same row count *and* the same file count in every partition to
    pass unnoticed.

    Cheap enough to run unconditionally. `count(*)` and the distinct filename
    count come out of the Parquet footers rather than the row groups, measured
    at about 3 ms per table on the local zone, so this is not a scan added to
    every run.
    """
    rows = con.execute(
        f"select {PARTITION_KEY}, count(*), count(distinct filename) "
        f"from {read_sql(table, root, filename=True)} group by 1"
    ).fetchall()
    return {str(partition): {"rows": rows_in, "files": files} for partition, rows_in, files in rows}


def runs_read_sql(root: Path | str | None = None) -> str:
    """SQL fragment reading every dataset's run manifests as one table.

    Columns are declared rather than inferred: `error` is null on every
    successful run, and read_json_auto would type a directory of
    all-successful manifests differently from one containing a failure.
    """
    pattern = remote.child(root if root is not None else raw_root(), "*", RUNS_DIRNAME, "*.json")
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


def connect(root: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB used purely as a Parquet reader.

    Registers the gs:// filesystem when the zone is remote, because DuckDB
    resolves a filesystem per connection rather than per process. A caller that
    opens its own connection has to do the same; `load.py` does.
    """
    con = duckdb.connect()
    remote.register(con, root if root is not None else raw_root())
    return con


def has_data(table: str, root: Path | str | None = None) -> bool:
    directory = dataset_dir(table, root)
    pattern = f"{PARTITION_KEY}=*/*.parquet"
    if remote.is_remote(directory):
        return bool(remote.glob(f"{directory}/{pattern}"))
    return any(directory.glob(pattern))


def read_watermark(table: str, root: Path | str | None = None) -> str | None:
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
    with connect(root if root is not None else raw_root()) as con:
        row = con.execute(f"select max({WATERMARK_COLUMN}) from {read_sql(table, root)}").fetchone()
    return row[0] if row else None


def write_batch(
    table: str,
    rows: list[dict],
    run_id: str,
    seq: int,
    *,
    ingest_date: str | None = None,
    root: Path | str | None = None,
) -> Path | str:
    """Write one buffered batch as a single Parquet file. Returns its path.

    The schema is the union of keys present in this batch, all STRING, sorted
    for stable file layout. Rows missing a key get NULL rather than the key
    being dropped, which is what makes files with different column sets
    readable together under union_by_name.

    Local or remote is decided by the root and by nothing else, and either way
    this only ever adds a file: the name carries the run id and a sequence
    number, so it cannot collide with a file an earlier run wrote. That is the
    append-only rule (ADR-4), and it is the same rule on a bucket as on a disk.
    """
    ingest_date = ingest_date or date.today().isoformat()
    destination_root = root if root is not None else raw_root()
    columns = sorted({column for row in rows for column in row})
    batch = pa.Table.from_pylist(rows, schema=pa.schema([(c, pa.string()) for c in columns]))

    partition = remote.child(dataset_dir(table, destination_root), f"{PARTITION_KEY}={ingest_date}")
    filename = f"part-{run_id}-{seq:04d}.parquet"
    if remote.is_remote(partition):
        # No mkdir: GCS has no directories. The partition prefix exists because
        # an object is named with it, which is also why hive partitioning works
        # unchanged on the bucket.
        destination = remote.child(partition, filename)
        with remote.open_write(destination) as handle:
            pq.write_table(batch, handle, compression="snappy")
        return destination

    partition.mkdir(parents=True, exist_ok=True)
    destination = partition / filename
    pq.write_table(batch, destination, compression="snappy")
    return destination


def write_run_manifest(table: str, manifest: dict, root: Path | str | None = None) -> Path | str:
    """Record one ingestion run next to the data it produced.

    The manifests are what let mart_pipeline_freshness distinguish "ingestion
    ran and found nothing new" from "ingestion has not run in three days". A
    run that fetches zero rows writes no Parquet file, so the data alone
    cannot tell those apart, and they mean opposite things to whoever is
    reading the freshness view.
    """
    directory = runs_dir(table, root)
    # Written as a one-element array so read_json can use format = 'array'
    # uniformly across files, and so a manifest is a valid JSON document on
    # its own rather than newline-delimited fragments.
    payload = json.dumps([manifest], indent=2) + "\n"
    filename = f"{manifest['run_id']}.json"
    if remote.is_remote(directory):
        return remote.write_text(remote.child(directory, filename), payload)

    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / filename
    destination.write_text(payload)
    return destination
