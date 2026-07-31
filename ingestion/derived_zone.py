"""Layout of the derived zone, and the only code that reads or writes it.

    data/derived/<table>.parquet
    data/derived/_manifest.json

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
  - Types are real. `h3_r9` is a BIGINT, not a string of digits. The
    all-STRING contract exists so that the raw zone cannot silently lose
    information the API sent; it buys nothing for a column we computed.
  - Deleting `data/derived/` is always safe.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

MANIFEST_NAME = "_manifest.json"

# Top-level manifest key holding what the raw zone looked like when this zone
# was built: per raw table, the deduplicated row count and the watermark.
# Separate from the `tables` list, which is about what came out, because this
# is about what went in, and it is the only thing that makes staleness
# detectable. See check_derived.py.
RAW_INPUTS_KEY = "raw_inputs"


def derived_root() -> Path:
    """Root of the derived zone. DERIVED_ZONE_DIR exists so CI can isolate it."""
    return Path(os.environ.get("DERIVED_ZONE_DIR", "data/derived"))


def derived_path(table: str, root: Path | None = None) -> Path:
    return (root or derived_root()) / f"{table}.parquet"


def has_data(table: str, root: Path | None = None) -> bool:
    return derived_path(table, root).exists()


def read_sql(table: str, root: Path | None = None) -> str:
    """SQL fragment that reads one derived table.

    No hive partitioning and no union_by_name, unlike the raw zone: there is
    exactly one file and this module wrote it, so its schema is known rather
    than discovered.
    """
    path = str(derived_path(table, root)).replace("'", "''")
    return f"read_parquet('{path}')"


def write_table(table: str, rows: list[dict], schema: pa.Schema, root: Path | None = None) -> Path:
    """Replace one derived table. Returns its path.

    The schema is passed in rather than inferred. Inference on a list of dicts
    guesses from the values present, so a run where every `is_interior` came
    out false would type the column differently from one where some were null,
    and the two Parquet files would then disagree about a column that never
    changed meaning.
    """
    destination = derived_path(table, root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(rows, schema=schema),
        destination,
        compression="snappy",
    )
    return destination


def write_manifest(
    entries: list[dict], root: Path | None = None, raw_inputs: dict | None = None
) -> Path:
    """Record what this run of spatial.py built, next to what it built.

    `raw_inputs` is what it was built from. Omitted rather than written empty
    when a caller has nothing to record, so `read_manifest` can tell "built by
    a spatial.py that did not record its inputs" apart from "built from an
    empty raw zone". The first cannot be checked for staleness; the second can.
    """
    directory = root or derived_root()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / MANIFEST_NAME
    payload: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tables": entries,
    }
    if raw_inputs is not None:
        payload[RAW_INPUTS_KEY] = raw_inputs
    destination.write_text(json.dumps(payload, indent=2) + "\n")
    return destination


def read_manifest(root: Path | None = None) -> dict | None:
    """The manifest this zone was written with, or None if there is not one.

    None covers both "the zone was never built" and "the directory exists but
    the manifest does not", which are the same answer to every caller: there
    is nothing here to compare against.
    """
    path = (root or derived_root()) / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        # A truncated manifest is a half-finished `make spatial`, so treat it
        # as absent rather than crashing the caller. Rebuilding fixes it.
        return None
