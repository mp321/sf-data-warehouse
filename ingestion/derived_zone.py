"""Layout of the derived zone, and the only code that reads or writes it.

Under `data/derived`, or under a `gs://` prefix when `DERIVED_ZONE_URI` is set
(ADR-9). One layout, one reader, one writer, either way:

    <root>/<table>.parquet
    <root>/_manifest.json

The sibling of `raw_zone.py`, and deliberately almost its opposite. The raw
zone is append-only, hive-partitioned, all-STRING, and holds what an API sent
us. The derived zone is replaced wholesale on every run, unpartitioned, and
properly typed, because nothing here was received: every column in it was
computed by `ingestion/spatial.py` from the raw zone, and can be recomputed
from the raw zone at any time.

That difference is the whole argument for a separate zone rather than extra
columns on the raw tables. Writing an H3 cell into `raw_311_cases` would mean
raw is no longer raw, and fixing a bug in the cell computation would mean
re-ingesting from Socrata rather than re-running a local step (ADR-2 rejected
exactly that under option B). Keeping it separate means `make spatial` is
free to be wrong and cheap to re-run.

Three consequences worth knowing:

  - There is no watermark and no run history here, and there does not need to
    be: the zone is a pure function of the raw zone plus the code, so what it
    should contain is always recomputable and never has to be remembered.
    `_manifest.json` records what was built, from which raw rows and
    partitions, and by which code, for the same reason ADR-4 wanted run
    manifests: so an empty table can be told apart from a step that never ran.
    Those three records are also what make the zone checkable without
    recomputing it, and what let `spatial.py` skip the parts of a rebuild
    whose inputs have not moved (PLAN-5 step 9). `check_derived.py` compares
    all three against the raw zone and the code as they are now.
  - Types are real. `h3_r8` is a BIGINT, not a string of digits. The
    all-STRING contract exists so that the raw zone cannot silently lose
    information the API sent; it buys nothing for a column we computed.
  - Deleting the zone is always safe, local or remote. `make spatial` rebuilds
    it exactly, which is why `write_table` is allowed to replace an object here
    and `raw_zone.write_batch` is not.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import remote

MANIFEST_NAME = "_manifest.json"

# Top-level manifest keys holding what this zone was built from. Separate from
# the `tables` list, which is about what came out, because these are about what
# went in, and they are what make the zone checkable without recomputing it.
# See derived_state.py for what fills them and check_derived.py for what reads
# them.
#
#   raw_inputs      per raw table, the deduplicated row count and the
#                   watermark. Answers "has the raw zone moved".
#   raw_partitions  per raw table, per ingest_date partition, the row and file
#                   counts. Answers "which partitions has this zone seen",
#                   which is the finer question incrementality is keyed on.
#   code_version    what computed the zone: a stamp over the source of every
#                   module that decides its contents, plus the resolutions and
#                   table lists in readable form. Answers "was this zone built
#                   by code that still exists", which no row count can.
RAW_INPUTS_KEY = "raw_inputs"
PARTITIONS_KEY = "raw_partitions"
CODE_VERSION_KEY = "code_version"


def derived_root() -> Path | str:
    """Root of the derived zone: a local directory, or a gs:// prefix.

    `DERIVED_ZONE_DIR` exists so CI can isolate it, `DERIVED_ZONE_URI` so the
    zone can live in a bucket (ADR-9). DIR wins when both are set, for the reason
    in ingestion/remote.py: `make ci-build` sets DIR and must stay local.
    """
    location = remote.zone_location("DERIVED_ZONE_DIR", "DERIVED_ZONE_URI", "data/derived")
    return location if remote.is_remote(location) else Path(location)


def derived_path(table: str, root: Path | str | None = None) -> Path | str:
    return remote.child(root if root is not None else derived_root(), f"{table}.parquet")


def has_data(table: str, root: Path | str | None = None) -> bool:
    path = derived_path(table, root)
    if remote.is_remote(path):
        return bool(remote.glob(str(path)))
    return path.exists()


def read_sql(table: str, root: Path | str | None = None) -> str:
    """SQL fragment that reads one derived table.

    No hive partitioning and no union_by_name, unlike the raw zone: there is
    exactly one file and this module wrote it, so its schema is known rather
    than discovered.
    """
    path = str(derived_path(table, root)).replace("'", "''")
    return f"read_parquet('{path}')"


def read_table(table: str, root: Path | str | None = None) -> list[dict] | None:
    """One derived table back as the list of dicts that wrote it, or None.

    The read half of incrementality (PLAN-5 step 9). `write_table` takes a list
    of dicts against a fixed schema, so this returns the same shape and the
    round trip is exact: every column in the derived zone is a float, an int, a
    bool or a string, and Parquet holds all four without narrowing.

    None means the table is not there, which a caller has to treat as "recompute
    it" rather than "it is empty". Both are legitimate states of a derived zone
    and only one of them is safe to reuse.
    """
    path = derived_path(table, root)
    if remote.is_remote(path):
        if not remote.glob(str(path)):
            return None
        with remote.open_read(path) as handle:
            return pq.read_table(handle).to_pylist()
    if not path.exists():
        return None
    return pq.read_table(path).to_pylist()


def write_table(
    table: str, rows: list[dict], schema: pa.Schema, root: Path | str | None = None
) -> Path | str:
    """Replace one derived table. Returns its path.

    The schema is passed in rather than inferred. Inference on a list of dicts
    guesses from the values present, so a run where every `is_interior` came
    out false would type the column differently from one where some were null,
    and the two Parquet files would then disagree about a column that never
    changed meaning.

    Replacing is allowed here and forbidden in the raw zone, which is the whole
    difference between the two. Overwriting one object in GCS is atomic in the
    same sense the local write is: a reader gets the old file or the new one.
    Neither is atomic across the six tables, remote or local, so a reader during
    a `make spatial` can still see a mix; `_manifest.json` is written last so
    that a mix is at least detectable afterwards.
    """
    destination = derived_path(table, root)
    batch = pa.Table.from_pylist(rows, schema=schema)
    if remote.is_remote(destination):
        with remote.open_write(destination) as handle:
            pq.write_table(batch, handle, compression="snappy")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(batch, destination, compression="snappy")
    return destination


def write_manifest(
    entries: list[dict],
    root: Path | str | None = None,
    raw_inputs: dict | None = None,
    partitions: dict | None = None,
    code_version: dict | None = None,
) -> Path | str:
    """Record what this run of spatial.py built, next to what it built.

    The three optional blocks are what it was built from. Each is omitted
    rather than written empty when a caller has nothing to record, so
    `read_manifest` can tell "built by a spatial.py that did not record this"
    apart from "built from nothing". The first cannot be checked; the second
    can, and a zone written by an older spatial.py is the case that makes the
    distinction worth keeping.

    `generated_at` is when spatial.py last ran, which is not the same as when
    any given table was last written: the per-table `built_at` in `entries` is
    that, and it is what makes a rebuild attributable to a run rather than to
    an object mtime.
    """
    directory = root if root is not None else derived_root()
    payload: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tables": entries,
    }
    if code_version is not None:
        payload[CODE_VERSION_KEY] = code_version
    if raw_inputs is not None:
        payload[RAW_INPUTS_KEY] = raw_inputs
    if partitions is not None:
        payload[PARTITIONS_KEY] = partitions
    text = json.dumps(payload, indent=2) + "\n"
    if remote.is_remote(directory):
        return remote.write_text(remote.child(directory, MANIFEST_NAME), text)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / MANIFEST_NAME
    destination.write_text(text)
    return destination


def read_manifest(root: Path | str | None = None) -> dict | None:
    """The manifest this zone was written with, or None if there is not one.

    None covers both "the zone was never built" and "the directory exists but
    the manifest does not", which are the same answer to every caller: there
    is nothing here to compare against.
    """
    location = root if root is not None else derived_root()
    if remote.is_remote(location):
        # The manifest travels with the zone, so a remote zone has a remote
        # manifest. Read it through the same filesystem the Parquet goes through.
        try:
            return json.loads(remote.read_text(remote.child(location, MANIFEST_NAME)))
        except (OSError, ValueError):
            return None
    path = location / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        # A truncated manifest is a half-finished `make spatial`, so treat it
        # as absent rather than crashing the caller. Rebuilding fixes it.
        return None
