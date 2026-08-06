"""Load the Parquet raw zone into a warehouse. Separate from ingestion, idempotent.

`ingest.py` pulls from Socrata and writes Parquet. This reads that Parquet and
materialises it as raw tables in DuckDB or BigQuery. It never touches the API,
so it can be run as often as you like, and a warehouse can be rebuilt from
scratch without re-fetching anything (ADR-4).

Load semantics: replace, not append. Each run rebuilds the whole raw table
from the files currently in the zone. That is what makes this step idempotent
with no bookkeeping at all: there is no "which partitions did I already load"
state to get wrong, and a half-finished load leaves nothing behind to
reconcile. Append-only lives where it matters, on the Parquet zone, which is
the record; the warehouse tables are derived mirrors of it.

The cost of replacing rather than appending is a full rewrite per run. That is
free on DuckDB (local) and free on BigQuery (load jobs are not billed; only
storage and query are), so the simpler semantics win until a raw table gets
big enough for the rewrite time itself to hurt. ADR-4 records the threshold.

Both targets read through DuckDB, using the single reader in raw_zone.py, so
the two warehouses cannot disagree about what the raw zone contains.

External tables are the one place that claim had to be defended rather than
just stated, because BigQuery reads the Parquet itself at query time and does
not go through raw_zone.py to do it. What it does go through is the column
list: the external schema is computed by `_union_columns` off the same reader,
so the two engines agree on which columns exist even though only one of them
decides how a file is read. `_external_table`'s docstring carries the failure
that made this necessary.

Usage:
    python ingestion/load.py --all --target duckdb
    python ingestion/load.py 311_cases --target duckdb
    python ingestion/load.py --all --target bigquery

Required environment variables:
    none for --target duckdb
    GCP_PROJECT_ID, GOOGLE_APPLICATION_CREDENTIALS for --target bigquery
Optional:
    DUCKDB_PATH      DuckDB file to load into (default: data/sf.duckdb)
    BQ_RAW_DATASET   BigQuery dataset for raw tables (default: raw_datasf)
    BQ_LOCATION      BigQuery location (default: US)
    RAW_ZONE_DIR     root of the raw zone (default: data/raw)
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

import duckdb

import derived_zone
import raw_zone
import remote
from dataset_registry import DATASETS

# Schema the dbt sources point at, on both engines. Keeping the name identical
# across warehouses is what lets one set of models build against either.
RAW_SCHEMA = "raw_datasf"

# The derived zone lands in its own schema rather than beside the raw tables.
# Two reasons, and the second is the one that matters: it keeps "everything in
# raw_datasf is a STRING and came from an API" true with no exceptions, and it
# makes the H3 tables obviously computed, so nobody goes looking for the
# upstream field that produced them. ingestion/spatial.py writes the zone.
DERIVED_SCHEMA = "derived_spatial"
DERIVED_TABLES = (
    "derived_point_h3",
    "derived_boundary",
    "derived_polygon_h3",
    "derived_point_boundary",
    "derived_h3_population",
    "derived_pip_sample",
)

# Ingestion run manifests land here so mart_pipeline_freshness can read them
# as an ordinary table. It is metadata rather than DataSF data, so it is the
# one table in the raw schema that is not all-STRING.
RUNS_TABLE = "raw_ingest_runs"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required environment variable: {name}. See SETUP.md.")
    return value


def _select_all(table: str, root: Path | str | None) -> str:
    return f"select * from {raw_zone.read_sql(table, root)}"


def _union_columns(
    reader: duckdb.DuckDBPyConnection, table: str, root: Path | str | None
) -> list[str]:
    """Every column one raw table holds, as the zone's single reader sees it.

    This is `union_by_name`'s answer by construction rather than by agreement:
    it goes through `raw_zone.read_sql`, so it cannot drift from what the
    DuckDB load of the same zone produces. `describe` reads Parquet footers
    and not row groups, so the cost is a listing rather than a scan.

    Used only for the external-table path. `_upload` needs nothing equivalent,
    because it consolidates through the same reader and hands BigQuery one
    file whose schema is already the union.
    """
    rows = reader.execute(f"describe {_select_all(table, root)}").fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# DuckDB
# ---------------------------------------------------------------------------


def load_duckdb(
    names: list[str],
    root: Path | str | None,
    duckdb_path: Path,
    derived_root: Path | str | None = None,
) -> None:
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(duckdb_path)) as con:
        # This connection is opened here rather than by raw_zone.connect(), so it
        # needs the gs:// filesystem registering explicitly. DuckDB resolves
        # filesystems per connection, so forgetting this reads a remote zone as a
        # relative path and fails with "No files found". The arguments win over
        # the environment, because they are what the rest of this function reads.
        remote.register(
            con,
            root if root is not None else raw_zone.raw_root(),
            derived_root if derived_root is not None else derived_zone.derived_root(),
        )
        con.execute(f"create schema if not exists {RAW_SCHEMA}")
        con.execute(f"create schema if not exists {DERIVED_SCHEMA}")
        for name in names:
            table = DATASETS[name]["table"]
            if not raw_zone.has_data(table, root):
                print(f"[{name}] no Parquet in the raw zone; skipped")
                continue
            # CREATE OR REPLACE is atomic in DuckDB: readers see either the
            # old table or the new one, never a truncated one.
            con.execute(
                f"create or replace table {RAW_SCHEMA}.{table} as {_select_all(table, root)}"
            )
            count = con.execute(f"select count(*) from {RAW_SCHEMA}.{table}").fetchone()[0]
            print(f"[{name}] {count} rows in {RAW_SCHEMA}.{table}")

        _load_runs_duckdb(con, root)
        _load_derived_duckdb(con, derived_root)
    print(f"\nLoaded into {duckdb_path}")


def _load_derived_duckdb(con: duckdb.DuckDBPyConnection, derived_root: Path | str | None) -> None:
    """Materialise the derived zone, or empty tables shaped like it.

    Empty rather than absent when `make spatial` has not run: every spatial
    model references these, so a missing relation fails the whole dbt build
    with a message about a table nobody has heard of. An empty one fails the
    same build with zero rows in a mart, which points at the step that was
    skipped. `derived_spatial` is dropped and recreated so a table removed
    from spatial.py does not linger.
    """
    for table in DERIVED_TABLES:
        if not derived_zone.has_data(table, derived_root):
            continue
        con.execute(
            f"create or replace table {DERIVED_SCHEMA}.{table} as "
            f"select * from {derived_zone.read_sql(table, derived_root)}"
        )
        count = con.execute(f"select count(*) from {DERIVED_SCHEMA}.{table}").fetchone()[0]
        print(f"[spatial] {count} rows in {DERIVED_SCHEMA}.{table}")

    missing = [table for table in DERIVED_TABLES if not derived_zone.has_data(table, derived_root)]
    if missing:
        print(
            f"[spatial] no derived zone for {', '.join(missing)}. "
            "Run `make spatial`; spatial models will build empty until you do."
        )


def _load_runs_duckdb(con: duckdb.DuckDBPyConnection, root: Path | str | None) -> None:
    if not _has_run_manifests(root):
        # The freshness mart left-joins this table, so it has to exist even
        # when nothing has ever been ingested. An empty table with the right
        # columns is a better answer than a missing relation.
        con.execute(_empty_runs_ddl())
        return
    con.execute(
        f"create or replace table {RAW_SCHEMA}.{RUNS_TABLE} as "
        f"select * from {raw_zone.runs_read_sql(root)}"
    )
    count = con.execute(f"select count(*) from {RAW_SCHEMA}.{RUNS_TABLE}").fetchone()[0]
    print(f"[_runs] {count} ingestion runs in {RAW_SCHEMA}.{RUNS_TABLE}")


def _has_run_manifests(root) -> bool:
    """Is there at least one ingestion run manifest in the zone?

    Local and remote need different listing calls, and both are needed: the
    freshness mart left-joins this table, so an empty one has to be created when
    nothing has ever been ingested.
    """
    location = root if root is not None else raw_zone.raw_root()
    pattern = f"*/{raw_zone.RUNS_DIRNAME}/*.json"
    if remote.is_remote(location):
        return bool(remote.glob(f"{location}/{pattern}"))
    return bool(list(location.glob(pattern)))


def _empty_runs_ddl() -> str:
    return f"""
        create or replace table {RAW_SCHEMA}.{RUNS_TABLE} (
            run_id varchar, dataset varchar, table_name varchar, ingest_date varchar,
            started_at timestamp, finished_at timestamp, watermark_in varchar,
            watermark_out varchar, rows_written bigint, files_written bigint,
            mode varchar, status varchar, error varchar
        )
    """


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------


def load_bigquery(
    names: list[str], root: Path | str | None, derived_root: Path | str | None = None
) -> None:
    # Deferred on purpose, so importing this module costs nothing on the
    # DuckDB path, which is the one that runs on every PR and every local
    # build. Module-scope would make the credential-free path import the
    # google stack to not use it. PLC0415 wants top-level imports and is
    # right nearly everywhere, hence the local silence rather than a repo
    # wide ignore in ruff.toml.
    from google.cloud import bigquery  # noqa: PLC0415

    project = require_env("GCP_PROJECT_ID")
    require_env("GOOGLE_APPLICATION_CREDENTIALS")
    raw_dataset = os.environ.get("BQ_RAW_DATASET", RAW_SCHEMA)

    derived_dataset = os.environ.get("BQ_DERIVED_DATASET", DERIVED_SCHEMA)

    client = bigquery.Client(project=project)
    location = os.environ.get("BQ_LOCATION", "US")
    for name in (raw_dataset, derived_dataset):
        dataset = bigquery.Dataset(f"{project}.{name}")
        dataset.location = location
        client.create_dataset(dataset, exists_ok=True)

    raw_location = root if root is not None else raw_zone.raw_root()
    derived_location = derived_root if derived_root is not None else derived_zone.derived_root()

    with raw_zone.connect() as reader, tempfile.TemporaryDirectory() as tmp:
        remote.register(reader, raw_location, derived_location)
        for name in names:
            table = DATASETS[name]["table"]
            if not raw_zone.has_data(table, root):
                print(f"[{name}] no Parquet in the raw zone; skipped")
                continue
            if remote.is_remote(raw_location):
                _external_table(
                    client,
                    f"{project}.{raw_dataset}.{table}",
                    uris=[f"{remote.child(raw_location, table)}/*.parquet"],
                    hive_prefix=str(remote.child(raw_location, table)),
                    columns=_union_columns(reader, table, root),
                    label=name,
                )
                continue
            _upload(
                client,
                reader,
                f"{project}.{raw_dataset}.{table}",
                _select_all(table, root),
                Path(tmp) / f"{table}.parquet",
                label=name,
            )

        if _has_run_manifests(root):
            _upload(
                client,
                reader,
                f"{project}.{raw_dataset}.{RUNS_TABLE}",
                f"select * from {raw_zone.runs_read_sql(root)}",
                Path(tmp) / f"{RUNS_TABLE}.parquet",
                label="_runs",
            )

        for table in DERIVED_TABLES:
            if not derived_zone.has_data(table, derived_root):
                print(f"[spatial] no derived zone for {table}; skipped. Run `make spatial`.")
                continue
            if remote.is_remote(derived_location):
                # No hive partitioning: the derived zone is one file per table,
                # written by spatial.py, so there are no partition directories to
                # recover and nothing to infer a key from.
                _external_table(
                    client,
                    f"{project}.{derived_dataset}.{table}",
                    uris=[str(derived_zone.derived_path(table, derived_root))],
                    hive_prefix=None,
                    label="spatial",
                )
                continue
            _upload(
                client,
                reader,
                f"{project}.{derived_dataset}.{table}",
                f"select * from {derived_zone.read_sql(table, derived_root)}",
                Path(tmp) / f"{table}.parquet",
                label="spatial",
            )


def _external_table(
    client,
    table_ref: str,
    *,
    uris: list[str],
    hive_prefix,
    label: str,
    columns: list[str] | None = None,
) -> None:
    """Point BigQuery at the Parquet in GCS instead of copying it in (ADR-9).

    Zero BigQuery storage, and no replace-on-load rewrite: the zone is the record
    and BigQuery reads it where it lies. It also removes the arrangement that ran
    out of room, which was a 6.44 GB all-STRING copy of a 162 MB Parquet zone
    against a 10 GiB free tier, since BigQuery's logical bytes for STRING columns
    are roughly ten times the Parquet on disk.

    Four details are load bearing, and each one was found by trying the
    alternative:

      *.parquet, not *   The run manifests live at `<table>/_runs/*.json`, inside
                         the table directory. A bare `*` picks them up and the
                         table fails to read with "Incompatible partition
                         schemas", because a JSON file has no ingest_date=
                         directory above it.
      mode=STRINGS       Keeps `ingest_date` a STRING. BigQuery otherwise infers
                         DATE from `ingest_date=2026-07-31`, which would put one
                         non-STRING column in an all-STRING raw table and break
                         the contract every staging model is written against.
                         This is the exact counterpart of DuckDB's
                         `hive_types_autocast = 0` in raw_zone.read_sql.
      delete then create BigQuery cannot convert a materialized table into an
                         external one in place, and this step exists partly to
                         remove those copies.
      columns, not       `autodetect` infers one schema for the whole table from
      autodetect         a sampled file, so a column that some files lack is
                         absent from the table entirely, while DuckDB's
                         `union_by_name` has it. That is not hypothetical: it
                         cost `stg_datasf__building_permits` five columns and a
                         red `make build-bigquery` on 2026-08-05, with
                         `Unrecognized name: unit_suffix` several steps
                         downstream of the cause. `columns` is the zone's union
                         column list from `_union_columns`, declared STRING
                         because the raw zone's contract is that every column is
                         one (ADR-4), which is what makes an explicit schema
                         cheap enough to be the default here. The derived zone
                         passes None and keeps autodetect: it is one file per
                         table written against a fixed pyarrow schema, so there
                         is no union to take and the types are real.

    `reference_file_schema_uri` was the other candidate and was rejected rather
    than skipped. It pins inference to one named file, which fixes today's
    symptom and fails the next column Socrata adds: new columns arrive in new
    files, the reference URI keeps pointing at an old one, and the zone is
    append-only so that file can never grow the column either. It would need a
    human to repoint it at exactly the moment nobody knows a column appeared.
    The union list is recomputed from the whole zone on every load, so the
    external table is a function of the zone rather than of one file, whichever
    file that is.
    """
    from google.cloud import bigquery  # noqa: PLC0415  (see load_bigquery)

    external = bigquery.ExternalConfig("PARQUET")
    external.source_uris = uris
    external.autodetect = columns is None
    if columns is not None:
        # Includes `ingest_date`, since it comes back from DuckDB's describe.
        # BigQuery reconciles it with the hive partition key rather than looking
        # for it in the files: checked both ways on 2026-08-05, and declaring it
        # or omitting it produce the same 59 column table with the same four
        # distinct partition values.
        external.schema = [bigquery.SchemaField(name, "STRING") for name in columns]
    if hive_prefix:
        options = bigquery.HivePartitioningOptions()
        options.mode = "STRINGS"
        options.source_uri_prefix = hive_prefix
        external.hive_partitioning = options

    table = bigquery.Table(table_ref)
    table.external_data_configuration = external
    client.delete_table(table_ref, not_found_ok=True)
    client.create_table(table)
    # An external table reports num_rows as 0, so count instead of reading
    # metadata. It is also the only cheap proof that the URIs actually resolve:
    # create_table succeeds against a prefix that matches nothing at all.
    rows = next(iter(client.query(f"select count(*) as n from `{table_ref}`").result())).n
    width = f"{len(columns)} columns, " if columns is not None else ""
    print(f"[{label}] {rows} rows in {table_ref} (external, {width}reading {uris[0]})")


def _upload(client, reader, table_ref: str, query: str, staged: Path, *, label: str) -> None:
    """Consolidate one dataset into a single Parquet file, then load it.

    DuckDB does the consolidation and streams to disk, so memory stays flat
    regardless of how many partitions the zone holds; the previous loader
    buffered rows in Python and grew with the table. BigQuery infers the
    schema from the file, and every column is a STRING there, so the raw
    contract survives the round trip without a schema being declared twice.
    """
    from google.cloud import bigquery  # noqa: PLC0415  (see load_bigquery)

    reader.execute(f"copy ({query}) to '{staged}' (format parquet, compression snappy)")
    config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    with staged.open("rb") as handle:
        job = client.load_table_from_file(handle, table_ref, job_config=config)
    job.result()  # wait for completion; raises on failure
    print(f"[{label}] {client.get_table(table_ref).num_rows} rows in {table_ref}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the Parquet raw zone into a warehouse.")
    parser.add_argument("datasets", nargs="*", help=f"dataset names: {', '.join(DATASETS)}")
    parser.add_argument("--all", action="store_true", help="load every registered dataset")
    parser.add_argument(
        "--target",
        choices=["duckdb", "bigquery"],
        default="duckdb",
        help="warehouse to load into (default: duckdb)",
    )
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=None,
        help="DuckDB file for --target duckdb (default: $DUCKDB_PATH or data/sf.duckdb)",
    )
    parser.add_argument(
        "--raw-root",
        type=remote.zone_root,
        default=None,
        help="root of the raw zone: a directory or a gs:// prefix "
        "(default: $RAW_ZONE_DIR, else $RAW_ZONE_URI, else data/raw)",
    )
    parser.add_argument(
        "--derived-root",
        type=remote.zone_root,
        default=None,
        help="root of the derived zone: a directory or a gs:// prefix "
        "(default: $DERIVED_ZONE_DIR, else $DERIVED_ZONE_URI, else data/derived)",
    )
    args = parser.parse_args()

    names = list(DATASETS) if args.all else args.datasets
    if not names:
        parser.error("pass one or more dataset names, or --all")
    unknown = [n for n in names if n not in DATASETS]
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(unknown)}. Valid: {', '.join(DATASETS)}")

    if args.target == "duckdb":
        path = args.duckdb_path or Path(os.environ.get("DUCKDB_PATH", "data/sf.duckdb"))
        load_duckdb(names, args.raw_root, path, args.derived_root)
    else:
        load_bigquery(names, args.raw_root, args.derived_root)


if __name__ == "__main__":
    main()
