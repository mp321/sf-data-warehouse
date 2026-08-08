"""Export marts to Parquet under a published/ prefix, with a manifest.

The last step of the pipeline, and the only one that produces something meant
to leave the machine.

    make ingest    Socrata, TIGERweb -> data/raw
    make spatial   data/raw          -> data/derived
    make load      both zones        -> warehouse
    make build     dbt run + test
    make publish   warehouse         -> published/            <- here

**Local first, always.** The destination defaults to `published/` on disk and
works with no bucket, no credentials and no network. A remote target is opted
into with --destination, and the local path is not a fallback for it: the
files are written locally and then uploaded, so a failed upload leaves a
complete local export rather than half a bucket. Nothing in this project is
ever blocked on a bucket existing, which is the same property ADR-1 bought for
the build and ADR-4 for the raw zone.

**Partitioning, and why there is none right now.** A mart is hive-partitioned
when `PUBLISHED_MARTS` says so and written as a single file when it does not,
and today none of the six does. The two that were partitioned by month cost
2,275 objects and 5.8x the bytes of the same data in one file per mart, because
the median monthly partition held 40 rows and a 5 KB Parquet file is mostly
footer. ADR-12 has the measurement and the reversal; the comment above
`PUBLISHED_MARTS` has the numbers. The mechanism stays for the day a mart has
enough rows per partition to want it.

**The manifest is the point.** `published/manifest.json` records, per dataset:
path, row count, byte size, a schema hash, and when it was generated. The
schema hash is what lets a consumer notice that a column changed type without
diffing data, and it is what the context pack in PLAN-6 will hang off.

**The uploader copies and never deletes, and `--prune` is the opt-in that does.**
Until PLAN-9 step 6 there was nothing to remove an object a later export stopped
writing, so the bucket held the 2,280 objects of the pre-ADR-12 month-partitioned
layout alongside the 7 of the current one: 18.6 MB where the export is 3.0 MB,
and a consumer that globbed the old paths still found a whole stale export
sitting there. `--prune` deletes objects under the destination prefix that the
export it just finished did not write. Three constraints, and each one is a rule
this project already had rather than a new one:

  - it runs AFTER the manifest lands, so ADR-8's ordering survives. Until the
    manifest arrives a consumer sees the previous export, coherent; after it
    arrives the new one is complete, and only then does anything get removed.
  - it prints every object it removes, because a flag that deletes remote data
    and reports a count is one nobody can audit afterwards.
  - it is off by default, and it refuses a destination with no prefix. A bare
    `gs://bucket` would make "everything the export did not write" mean the raw
    and derived zones.

Usage:
    python publish/export.py --all
    python publish/export.py --all --output-dir published
    python publish/export.py --all --destination r2://my-bucket/sf
    python publish/export.py --all --destination gs://my-bucket/sf
    python publish/export.py --all --destination gs://my-bucket/sf --prune
    python publish/export.py --list

Required environment variables: none for a local export.
Optional:
    DUCKDB_PATH             warehouse to read (default: data/sf.duckdb)
    PUBLISH_DIR             local output root (default: published)
    R2_ACCOUNT_ID           Cloudflare R2, with the two below
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
    GOOGLE_APPLICATION_CREDENTIALS   for a gs:// destination
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

# What gets published, and how. Marts only: staging models are views over raw
# and republishing them would ship the same rows twice under two names.
#
# `partition_by` names a DATE column to hive-partition on, or None for a single
# file. It is deliberately not inferred from the schema: a mart could gain a
# date column that is not the one anyone filters by, and silently repartitioning
# a published dataset breaks every consumer's paths.
#
# **NOTHING IS PARTITIONED TODAY, AND THAT IS A MEASUREMENT (ADR-12, PLAN-5
# step 12).** `mart_activity_by_h3` and `mart_activity_by_neighborhood` were
# partitioned by `event_month`, and `business_locations` carries
# `location_started_at` values from 1849 to 2028, so the two marts spread 180k
# rows over 874 and 868 monthly partitions. One publish was 2,280 objects
# against a free tier of 5,000 Class A operations a month, which put a daily
# publish over the tier on day three and is why `make publish` was run by hand.
#
# Measured on the real warehouse on 2026-08-05, those two marts alone:
#
#     by month   2,275 objects   11.0 MB    the median partition held 40 rows
#     by year      248 objects    4.2 MB
#     one file       2 objects    1.9 MB
#
# The size column is the part worth keeping. Month partitioning was not a trade
# of bytes for query pruning; it cost 5.8x the bytes as well, because a 5 KB
# Parquet file is mostly footer and dictionary pages and compression has
# nothing to work across. A layout that is worse on every axis is not a
# tradeoff, and ADR-8 chose it against a guessed access pattern rather than a
# real one. Its own revisit clause asked for a consumer with a real one before
# arguing about the key; none has appeared.
#
# The field and the code path stay. Partitioning is right when a partition is
# large, and the day a mart has millions of rows a month this is a one-line
# decision rather than a mechanism to rebuild.
PUBLISHED_MARTS = {
    "mart_activity_by_h3": {
        "partition_by": None,
        "description": "Counts and normalised rates per H3 cell, dataset, category and month.",
    },
    "mart_activity_by_neighborhood": {
        "partition_by": None,
        "description": "Counts and normalised rates per neighborhood, dataset, category, month.",
    },
    "mart_film_locations": {
        "partition_by": None,
        "description": "Film and TV shoot locations joined to their neighborhood.",
    },
    "dim_neighborhood": {
        "partition_by": None,
        "description": "The 41 analysis neighborhoods with area, population and denominators.",
    },
    "dim_supervisor_district": {
        "partition_by": None,
        "description": "The 11 supervisor districts (2022 boundaries) with denominators.",
    },
    "mart_pipeline_freshness": {
        "partition_by": None,
        "description": "Per-source pipeline health, row counts, staleness and coordinate quality.",
    },
}

MANIFEST_NAME = "manifest.json"

# One licence statement covers the whole export, and now the context pack as
# well: PLAN-6's spec says the pack states the licence `publish/export.py`
# already writes rather than a second one of its own, so this became a constant
# when tools/context_pack/ started importing it. DataSF and the Census Bureau
# are different publishers under the same public-domain terms, and the spec
# records leaving the per-dataset distinction out.
LICENSE = "Public domain. Source data from DataSF (data.sfgov.org) and the US Census Bureau."

# Bumped when the layout or the manifest shape changes in a way a consumer
# would notice. A consumer that pins this can refuse to read a newer export
# rather than misreading it.
#
# 2 on 2026-08-05: the two activity marts stopped being hive-partitioned by
# `event_month` and became one file each (ADR-12). Paths changed from
# `mart_activity_by_h3/event_month=YYYY-MM-01 00:00:00/data_0.parquet` to
# `mart_activity_by_h3/mart_activity_by_h3.parquet`, which breaks any consumer
# that globbed the old shape. `partitioned_by` in each manifest entry is null
# for every dataset now, so a consumer that reads the manifest before the files
# sees it rather than getting an empty listing.
MANIFEST_VERSION = 2


def warehouse_path() -> Path:
    return Path(os.environ.get("DUCKDB_PATH", "data/sf.duckdb"))


def output_root() -> Path:
    return Path(os.environ.get("PUBLISH_DIR", "published"))


def _schema_hash(con: duckdb.DuckDBPyConnection, table: str) -> str:
    """Stable hash of a mart's column names and types.

    Ordered by position and rendered as "name:type" pairs, so it changes when
    a column is added, removed, renamed, retyped or reordered, and does not
    change when a single row does. That is the distinction a consumer needs:
    data churns daily, schema should not, and finding out that it did by
    hitting a cast error in production is the failure this exists to prevent.
    """
    columns = con.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_name = ? order by ordinal_position",
        [table],
    ).fetchall()
    fingerprint = ";".join(f"{name}:{dtype}" for name, dtype in columns)
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]


def _directory_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def export_one(
    con: duckdb.DuckDBPyConnection, table: str, spec: dict, root: Path, schema: str
) -> dict:
    """Write one mart to Parquet and return its manifest entry."""
    qualified = f"{schema}.{table}"
    row_count = con.execute(f"select count(*) from {qualified}").fetchone()[0]
    partition_by = spec["partition_by"]

    destination = root / table
    # Removed rather than overwritten. DuckDB's OVERWRITE_OR_IGNORE replaces
    # the partitions it writes and leaves the ones it does not, so a month
    # that disappeared upstream would linger in the export forever and the
    # manifest's row count would disagree with what is on disk.
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    if partition_by:
        con.execute(
            f"copy (select * from {qualified}) to '{destination}' "
            f"(format parquet, compression snappy, partition_by ({partition_by}), "
            "overwrite_or_ignore true)"
        )
        path = f"{table}/"
    else:
        con.execute(
            f"copy (select * from {qualified}) to '{destination / (table + '.parquet')}' "
            "(format parquet, compression snappy)"
        )
        path = f"{table}/{table}.parquet"

    return {
        "dataset": table,
        "description": spec["description"],
        "path": path,
        "partitioned_by": partition_by,
        "row_count": row_count,
        "byte_size": _directory_size(destination),
        "schema_hash": _schema_hash(con, table),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_manifest(entries: list[dict], root: Path) -> Path:
    destination = root / MANIFEST_NAME
    destination.write_text(
        json.dumps(
            {
                "manifest_version": MANIFEST_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": "sf-data-warehouse",
                "license": LICENSE,
                "datasets": entries,
            },
            indent=2,
        )
        + "\n"
    )
    return destination


# ---------------------------------------------------------------------------
# Remote destinations
# ---------------------------------------------------------------------------


def _blob_name(root: Path, item: Path, prefix: str) -> str:
    """The object key one exported file lands at. One definition, three callers.

    The prune compares what is in the bucket against what was just written, so
    the two sides have to agree about this exactly. Computing it twice is how a
    prune deletes the export it just uploaded.
    """
    relative = item.relative_to(root).as_posix()
    return f"{prefix.rstrip('/')}/{relative}" if prefix else relative


def upload(root: Path, destination: str, prune: bool = False) -> None:
    """Copy a finished local export to R2 or GCS, and optionally remove orphans.

    Deliberately a second step over a completed local directory rather than a
    streaming write. An interrupted upload then costs a retry rather than
    leaving a bucket holding some of one export and some of the last one, with
    a manifest that describes neither.

    The manifest is uploaded last, for the same reason: until it lands, a
    consumer reading the bucket sees the previous manifest and the previous
    view of the data, which is stale but coherent. A manifest that arrives
    before the files it describes is worse than one that arrives after.

    The prune runs after all of that, and the ordering is ADR-8's rather than a
    preference. Deleting before the manifest lands would put the destination
    into a state no consumer should ever see: a manifest describing files that
    have been removed. Deleting after it lands means the worst any consumer
    sees is the complete new export plus some objects nothing references.
    """
    scheme, _, remainder = destination.partition("://")
    if not remainder:
        sys.exit(f"Malformed destination {destination!r}. Expected r2://bucket/prefix or gs://...")
    bucket, _, prefix = remainder.partition("/")

    if prune and not prefix.strip("/"):
        sys.exit(
            f"--prune refuses {destination!r}: it has no prefix. Prune means 'delete what this "
            "export did not write under this prefix', and with no prefix that is every object "
            "in the bucket, which here would be the raw and derived zones. Give it a prefix."
        )

    files = sorted(item for item in root.rglob("*") if item.is_file())
    manifest = root / MANIFEST_NAME
    ordered = [item for item in files if item != manifest] + [manifest]
    written = {_blob_name(root, item, prefix) for item in ordered}

    if scheme == "gs":
        _upload_gcs(root, ordered, bucket, prefix)
    elif scheme == "r2":
        _upload_r2(root, ordered, bucket, prefix)
    else:
        sys.exit(f"Unknown destination scheme {scheme!r}. Supported: r2, gs.")
    print(f"Uploaded {len(ordered)} files to {destination}")

    if not prune:
        return
    orphans = _list_remote(scheme, bucket, prefix) - written
    if not orphans:
        print(f"Prune: nothing under {destination} that this export did not write.")
        return
    for key in sorted(orphans):
        print(f"  removing {scheme}://{bucket}/{key}")
    _delete_remote(scheme, bucket, sorted(orphans))
    print(f"Pruned {len(orphans)} object(s) from {destination}")


def _upload_gcs(root: Path, files: list[Path], bucket_name: str, prefix: str) -> None:
    from google.cloud import storage  # noqa: PLC0415  (import cost only on this path)

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    for item in files:
        bucket.blob(_blob_name(root, item, prefix)).upload_from_filename(item)


def _list_remote(scheme: str, bucket_name: str, prefix: str) -> set[str]:
    """Every object key currently under the destination prefix."""
    if scheme == "gs":
        from google.cloud import storage  # noqa: PLC0415

        client = storage.Client()
        under = f"{prefix.rstrip('/')}/"
        return {blob.name for blob in client.list_blobs(bucket_name, prefix=under)}

    client = _r2_client()
    keys: set[str] = set()
    token = None
    while True:
        page = client.list_objects_v2(
            Bucket=bucket_name,
            Prefix=f"{prefix.rstrip('/')}/",
            **({"ContinuationToken": token} if token else {}),
        )
        keys.update(item["Key"] for item in page.get("Contents", []))
        token = page.get("NextContinuationToken")
        if not token:
            return keys


def _delete_remote(scheme: str, bucket_name: str, keys: list[str]) -> None:
    """Remove the objects named. Deletes are free operations on both providers."""
    if scheme == "gs":
        from google.cloud import storage  # noqa: PLC0415

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        for key in keys:
            bucket.blob(key).delete()
        return

    client = _r2_client()
    # S3 caps a batch delete at 1000 keys, and the pre-ADR-12 layout was 2,280
    # objects, so this is a real bound rather than a theoretical one.
    for start in range(0, len(keys), 1000):
        client.delete_objects(
            Bucket=bucket_name,
            Delete={"Objects": [{"Key": key} for key in keys[start : start + 1000]]},
        )


def _r2_client():
    """One authenticated R2 client, for the upload path and the prune path.

    Factored out when `--prune` needed to list and delete as well as upload
    (PLAN-9 step 6). boto3 stays an optional import: the local export does not
    need it and this project does not depend on it.
    """
    try:
        import boto3  # noqa: PLC0415
    except ImportError:
        sys.exit(
            "An r2:// destination needs boto3, which is not a dependency of this project "
            "because the local export does not require it. Install it with `pip install boto3`."
        )

    account = os.environ.get("R2_ACCOUNT_ID")
    key_id = os.environ.get("R2_ACCESS_KEY_ID")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not (account and key_id and secret):
        sys.exit(
            "An r2:// destination needs R2_ACCOUNT_ID, R2_ACCESS_KEY_ID and "
            "R2_SECRET_ACCESS_KEY. See .env.example."
        )

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
    )


def _upload_r2(root: Path, files: list[Path], bucket_name: str, prefix: str) -> None:
    client = _r2_client()
    for item in files:
        client.upload_file(str(item), bucket_name, _blob_name(root, item, prefix))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export marts to partitioned Parquet with a manifest (ADR-8)."
    )
    parser.add_argument("marts", nargs="*", help=f"marts to export: {', '.join(PUBLISHED_MARTS)}")
    parser.add_argument("--all", action="store_true", help="export every published mart")
    parser.add_argument("--list", action="store_true", help="list the published marts and exit")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="local export root (default: $PUBLISH_DIR or published/)",
    )
    parser.add_argument(
        "--destination",
        default=None,
        help="also upload to r2://bucket/prefix or gs://bucket/prefix",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="after the manifest lands, delete objects under the destination prefix that "
        "this export did not write. Off by default: a flag that deletes remote data is "
        "not a default. Named in ADR-8's revisit clause as the thing to run when "
        "MANIFEST_VERSION changes.",
    )
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=None,
        help="warehouse to read (default: $DUCKDB_PATH or data/sf.duckdb)",
    )
    parser.add_argument("--schema", default="main", help="schema the marts live in (default: main)")
    args = parser.parse_args()

    if args.list:
        for name, spec in PUBLISHED_MARTS.items():
            partition = (
                f"partitioned by {spec['partition_by']}" if spec["partition_by"] else "single file"
            )
            print(f"  {name:34} {partition:28} {spec['description']}")
        return

    names = list(PUBLISHED_MARTS) if args.all else args.marts
    if not names:
        parser.error("pass one or more mart names, --all, or --list")
    unknown = [name for name in names if name not in PUBLISHED_MARTS]
    if unknown:
        parser.error(f"unknown mart(s): {', '.join(unknown)}. Valid: {', '.join(PUBLISHED_MARTS)}")

    warehouse = args.duckdb_path or warehouse_path()
    if not warehouse.exists():
        sys.exit(f"No warehouse at {warehouse}. Run `make build` first.")

    root = args.output_dir or output_root()
    root.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(warehouse), read_only=True) as con:
        entries = []
        for name in names:
            entry = export_one(con, name, PUBLISHED_MARTS[name], root, args.schema)
            entries.append(entry)
            print(
                f"[{name}] {entry['row_count']} rows, {entry['byte_size'] / 1024:.0f} KiB, "
                f"schema {entry['schema_hash']} -> {root / entry['path']}"
            )

    manifest = write_manifest(entries, root)
    print(f"\nWrote {manifest}")

    if args.destination:
        upload(root, args.destination, prune=args.prune)
    else:
        if args.prune:
            sys.exit(
                "--prune needs --destination. It removes objects a remote destination holds "
                "and the export did not write; the local export already rewrites each mart "
                "directory wholesale, so there is nothing under published/ for it to do."
            )
        print("Local export only. Pass --destination r2://bucket/prefix or gs://... to upload.")


if __name__ == "__main__":
    main()
