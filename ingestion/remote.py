"""How this project talks to object storage. The one place that knows about gs://.

`raw_zone.py` and `derived_zone.py` own the layout of the two Parquet zones.
This owns the question of whether a zone is a directory or a bucket prefix, so
neither of them has to, and so there is exactly one answer to "how do we
authenticate" rather than one per zone.

**Why fsspec and not DuckDB's httpfs.** Measured on 2026-07-31, recorded in
ADR-9. DuckDB's httpfs extension reaches GCS through the S3-compatible
interoperability layer, which authenticates with HMAC key pairs:
`create secret (type gcs, provider credential_chain)` is rejected outright and a
bare gcs secret returns 403 against a service account. Using it would mean
creating a second credential, storing it in `.env` and in GitHub secrets, and
rotating it, in a project whose hard constraint is that no credential reaches
the repo. fsspec with `gcsfs` reuses `GOOGLE_APPLICATION_CREDENTIALS`, so the
IAM story stays in one place: one service account, object admin on one bucket.

The cost is throughput, and it was measured rather than assumed. A full
materialising scan of the three largest raw tables took 8.83 seconds from GCS
against 1.48 seconds from local disk, so roughly 6x. That is 9 seconds once per
`load.py` run on a zone of a few hundred megabytes, which is not a number worth
a second credential.

**Precedence, and why the local variable wins.** A zone is remote only when its
URI variable is set and its DIR variable is not. `make ci-build` sets
`RAW_ZONE_DIR` and `DERIVED_ZONE_DIR` explicitly, and a developer with
`RAW_ZONE_URI` exported from `.env` must still get a local, credential-free,
bucket-free CI run out of it. ADR-1 requires that, so the explicit local
directory has to beat the ambient remote one. Any other order makes
`set -a; source .env` quietly change what `make check` tests.

**One zone at a time, never two.** When a zone's URI is in play the bucket is
the zone: `ingest.py` and `spatial.py` write there and write nothing locally.
ADR-9 rejected the arrangement where GCS holds the record and a local copy is
kept alongside it, because two copies with nothing to detect divergence is the
failure this project had just finished fixing in the derived zone. Writing both
would be that arrangement, so a run writes to exactly one of them, and which one
is a configuration question answered here.
"""

import argparse
import os
from pathlib import Path

import duckdb

# Schemes that mean "this is not a local path". Only GCS is implemented; s3 and
# r2 are listed so that a location this project cannot read fails with a clear
# message rather than being treated as a relative directory name called "s3:".
REMOTE_SCHEMES = ("gs://", "gcs://", "s3://", "r2://")

SUPPORTED_SCHEMES = ("gs://", "gcs://")


# DuckDB names a registered filesystem after the object's first protocol, which
# for gcsfs is "gs" and not "gcs". Hardcoding the wrong one made `register` think
# it had never run and re-register, which DuckDB rejects outright, so the name is
# read off the filesystem instead of assumed.
def _registered_name(fs: object) -> str:
    protocol = getattr(fs, "protocol", "gs")
    return protocol[0] if isinstance(protocol, (list, tuple)) else str(protocol)


def is_remote(location: object) -> bool:
    return isinstance(location, str) and location.startswith(REMOTE_SCHEMES)


def zone_location(dir_variable: str, uri_variable: str, default_dir: str) -> str:
    """Where a zone lives, as a directory path or a URI prefix.

    Returns a string either way, because both are only ever used to build a glob
    for DuckDB. Callers that need a real `Path` keep taking one as an argument;
    this is the environment-driven default, not a replacement for that.
    """
    directory = os.environ.get(dir_variable)
    if directory:
        return directory
    uri = os.environ.get(uri_variable)
    if uri:
        if not uri.startswith(SUPPORTED_SCHEMES):
            raise ValueError(
                f"{uri_variable}={uri!r} is not supported. Only gs:// is implemented; "
                "see ingestion/remote.py."
            )
        return uri.rstrip("/")
    return default_dir


def zone_root(value: str) -> "str | Path":
    """argparse type for a `--raw-root` or `--derived-root` flag.

    `type=Path` was wrong once the zones could be remote, and wrong in the
    expensive direction: `Path("gs://b/raw")` collapses to the relative path
    `gs:/b/raw`, which no glob matches, so `read_watermark` returns None and
    ingestion backfills from `start_date` rather than erroring. For 311 that is
    8.8 million rows fetched to be written into a directory called `gs:`.
    """
    if is_remote(value):
        if not value.startswith(SUPPORTED_SCHEMES):
            raise argparse.ArgumentTypeError(
                f"{value!r} is not supported. Only gs:// is implemented; see ingestion/remote.py."
            )
        return value.rstrip("/")
    return Path(value)


def child(location: "str | Path", *parts: str) -> "str | Path":
    """One path join that works for a local directory and a bucket prefix.

    Returns whatever kind it was given: a `Path` stays a `Path`, a URI stays a
    string. `Path("gs://b/raw")` would collapse the double slash and silently
    produce the relative path `gs:/b/raw`, which reads as a directory called
    `gs:` and fails much later, so the two cases cannot share `pathlib`.
    """
    if not is_remote(location):
        result = Path(location)
        for part in parts:
            result = result / part
        return result
    tail = "/".join(str(part).strip("/") for part in parts if str(part) != "")
    return f"{str(location).rstrip('/')}/{tail}" if tail else str(location)


def filesystem():
    """The gcsfs filesystem, authenticated the same way every other client is.

    `token` is the service account JSON when `GOOGLE_APPLICATION_CREDENTIALS`
    points at one, and otherwise falls through to gcsfs's default chain, which
    picks up an attached identity. That is what lets this keep working if CI
    ever moves to workload identity federation instead of a key file.
    """
    import fsspec  # noqa: PLC0415  (import cost only on the remote path)

    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    return fsspec.filesystem("gcs", token=credentials or None)


def _object_path(uri: "str | Path") -> str:
    """A gs:// URI as the bucket-relative path gcsfs takes."""
    return str(uri).split("://", 1)[1]


def glob(pattern: str) -> list[str]:
    """Object names matching a gs:// glob. Used to answer "is there data here"."""
    return [f"gs://{hit}" for hit in filesystem().glob(_object_path(pattern))]


def open_write(uri: "str | Path"):
    """Binary handle for one object, for writers that stream rather than buffer.

    `pyarrow.parquet.write_table` takes a file object, so a Parquet file goes
    straight to the bucket without a temporary file on the way.

    An object appears in GCS only when its upload finalises, so a writer that
    dies partway leaves no object rather than a truncated one. That is what lets
    the raw zone's append-only rule survive a crash: the zone gains a whole file
    or it gains nothing, and either way what is already there is untouched.
    """
    return filesystem().open(_object_path(uri), "wb")


def open_read(uri: "str | Path"):
    """Binary handle for one object. The mirror of `open_write`.

    `pyarrow.parquet.read_table` takes a file object, so a derived table can be
    read back out of the bucket without a temporary file. That read-back is what
    lets an incremental `spatial.py` reuse what the last run computed rather
    than recomputing it (PLAN-5 step 9).
    """
    return filesystem().open(_object_path(uri), "rb")


def write_text(uri: "str | Path", text: str) -> str:
    """Write one small text object, for the JSON manifests beside the Parquet."""
    with filesystem().open(_object_path(uri), "w") as handle:
        handle.write(text)
    return str(uri)


def read_text(uri: "str | Path") -> str:
    """Read one small text object. Raises like any other missing file."""
    with filesystem().open(_object_path(uri), "r") as handle:
        return handle.read()


def register(con: duckdb.DuckDBPyConnection, *locations: "str | Path") -> None:
    """Teach one DuckDB connection to read gs://, if any location needs it.

    Callers pass the zones they are about to read, which keeps this module from
    having to know how many zones exist or what their environment variables are
    called. A no-op for local zones, and idempotent, because the filesystem is
    registered per connection rather than per process and `load.py` opens
    several.
    """
    if not any(is_remote(location) for location in locations):
        return
    fs = filesystem()
    if con.filesystem_is_registered(_registered_name(fs)):
        return
    con.register_filesystem(fs)
