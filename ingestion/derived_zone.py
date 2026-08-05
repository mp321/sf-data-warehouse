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

  - There is no watermark and no run history here. The zone is a pure
    function of the raw zone plus the code, so "resume from where we were" is
    not a question that arises. `_manifest.json` records what was built and
    from how many input rows, for the same reason ADR-4 wanted run manifests:
    so an empty table can be told apart from a step that never ran. The input
    counts are also what make the zone's staleness checkable without
    recomputing it; `check_derived.py` compares them against the raw zone.
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

# Top-level manifest key holding what the raw zone looked like when this zone
# was built: per raw table, the deduplicated row count and the watermark.
# Separate from the `tables` list, which is about what came out, because this
# is about what went in, and it is the only thing that makes staleness
# detectable. See check_derived.py.
RAW_INPUTS_KEY = "raw_inputs"


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
    entries: list[dict], root: Path | str | None = None, raw_inputs: dict | None = None
) -> Path | str:
    """Record what this run of spatial.py built, next to what it built.

    `raw_inputs` is what it was built from. Omitted rather than written empty
    when a caller has nothing to record, so `read_manifest` can tell "built by
    a spatial.py that did not record its inputs" apart from "built from an
    empty raw zone". The first cannot be checked for staleness; the second can.
    """
    directory = root if root is not None else derived_root()
    payload: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tables": entries,
    }
    if raw_inputs is not None:
        payload[RAW_INPUTS_KEY] = raw_inputs
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
