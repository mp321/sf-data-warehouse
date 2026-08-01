"""Incremental ingestion from DataSF (Socrata) into the Parquet raw zone.

How it works, in plain terms:
  1. Look in <zone>/<table>/ for the newest _socrata_updated_at we already
     have. If the zone is empty, fall back to the dataset's start_date.
  2. Ask the Socrata API for rows updated after that watermark, ordered by
     update time, 5000 rows per page.
  3. Land every value as a STRING in a Parquet file under an
     ingest_date=YYYY-MM-DD partition. No cleaning here on purpose: raw stays
     raw, and all typing/renaming happens in dbt staging.
  4. Record the run in <zone>/<table>/_runs/<run_id>.json.
  5. Appends can create multiple versions of the same record over time. That
     is expected. Staging models deduplicate to the latest version.

**Where the zone is, and it is one place at a time.** `<zone>` is `data/raw` by
default and a `gs://` prefix when `RAW_ZONE_URI` is set (ADR-9), and a run
writes to whichever one is configured and to nothing else. A remote run does
not also update `data/raw`: ADR-9 considered exactly that arrangement, GCS
holding the record with a local copy beside it, and rejected it because two
copies with nothing to detect divergence is the failure this project had just
spent a session fixing in the derived zone. So `data/raw` is not a cache of the
bucket, and it is not stale-but-usable when the bucket is in use; it is
whatever the last local run left there, and the way to read the zone is to point
at the zone. `RAW_ZONE_DIR` beats `RAW_ZONE_URI` so that CI stays local.

This writes Parquet and nothing else. Getting those files into DuckDB or
BigQuery is `ingestion/load.py`, a separate and idempotent step (ADR-4). The
split matters because it means a warehouse can be rebuilt without re-hitting
the API, and an API pull cannot be lost by a warehouse failure.

Usage:
    python ingestion/ingest.py 311_cases
    python ingestion/ingest.py 311_cases film_locations
    python ingestion/ingest.py --all
    python ingestion/ingest.py 311_cases --full-refresh      # local zones only
    python ingestion/ingest.py 311_cases --since 2023-01-01T00:00:00.000Z
    python ingestion/ingest.py --all --fixtures tests/fixtures/socrata

Required environment variables: none. This step needs no credentials against a
local zone. A `gs://` zone authenticates through GOOGLE_APPLICATION_CREDENTIALS
like everything else that touches the bucket; see ingestion/remote.py.
Optional:
    SOCRATA_APP_TOKEN  free token from data.sfgov.org, raises rate limits
    RAW_ZONE_DIR       root of the raw zone as a directory (default: data/raw)
    RAW_ZONE_URI       root of the raw zone as a gs:// prefix, when DIR is unset
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import raw_zone
import remote
from census import census_pages
from datasets import DATASETS

SOCRATA_DOMAIN = "https://data.sfgov.org"
PAGE_SIZE = 5000  # rows per API request
ROWS_PER_FILE = 50000  # buffer this many rows before writing each Parquet file
MAX_RETRIES = 3


def sanitize_column(name: str) -> str:
    """Make a Socrata field name safe as a warehouse column name.

    Socrata system fields start with ':' (e.g. :updated_at); we rename
    them to _socrata_updated_at etc. so they are valid column names.
    """
    if name.startswith(":"):
        name = "_socrata_" + name[1:]
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name)
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = "_" + cleaned
    return cleaned.lower()


def normalize_record(record: dict, ingested_at: str, run_id: str) -> dict:
    """Flatten one API record into an all-STRING row for the raw zone."""
    row = {}
    for key, value in record.items():
        col = sanitize_column(key)
        if value is None:
            row[col] = None
        elif isinstance(value, dict | list):
            # Nested objects (point geometries, media_url) are kept as JSON
            # text. Staging pulls fields out with x_json_extract_scalar; the
            # raw zone does not get to decide which of them matter.
            row[col] = json.dumps(value)
        else:
            row[col] = str(value)
    row[raw_zone.INGESTED_AT_COLUMN] = ingested_at
    row[raw_zone.RUN_ID_COLUMN] = run_id
    return row


def socrata_pages(socrata_id: str, watermark: str, app_token: str):
    """Yield pages of records updated after the watermark, oldest first.

    Ordering by :updated_at is what makes the watermark safe to resume from:
    a run that dies halfway has still written a contiguous prefix, so the next
    run picks up exactly where it stopped rather than leaving a hole.

    The :id tiebreaker is not decoration. DataSF bulk-refreshes these datasets,
    so tens of thousands of rows can share one :updated_at value: a single
    slice of building_permits had a 7425-row tie. Ordering by :updated_at
    alone leaves rows within a tie in an order the API does not promise to
    repeat, and $offset paging then re-reads some of them and skips others,
    which loses data with no error and no duplicate to notice it by. Sorting
    on (:updated_at, :id) is a total order, so page boundaries are stable.
    """
    session = requests.Session()
    headers = {"X-App-Token": app_token} if app_token else {}
    url = f"{SOCRATA_DOMAIN}/resource/{socrata_id}.json"
    offset = 0

    while True:
        params = {
            "$select": "*,:id,:created_at,:updated_at",
            "$where": f":updated_at > '{watermark}'",
            "$order": ":updated_at,:id",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        }
        page = _get_with_retries(session, url, params, headers)
        yield page
        if len(page) < PAGE_SIZE:
            return
        offset += PAGE_SIZE
        time.sleep(0.3)  # be polite to the API


def _get_with_retries(session: requests.Session, url: str, params: dict, headers: dict) -> list:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=90)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == MAX_RETRIES:
                raise
            wait = 5 * attempt
            print(f"  request failed ({exc}); retrying in {wait}s")
            time.sleep(wait)
    return []


def fixture_pages(fixture_dir: Path, name: str, watermark: str):
    """Yield pages from a committed JSON fixture instead of the API.

    This exists so CI can run the whole pipeline with no network and no
    credentials. It filters, orders and pages exactly as socrata_pages does,
    so everything downstream of this generator is the same code path that
    runs in production; only the transport changes.
    """
    path = fixture_dir / f"{name}.json"
    if not path.exists():
        sys.exit(f"No fixture for '{name}' at {path}")
    records = json.loads(path.read_text())
    matching = sorted(
        (r for r in records if r.get(":updated_at", "") > watermark),
        key=lambda r: (r.get(":updated_at", ""), r.get(":id", "")),
    )
    for start in range(0, len(matching), PAGE_SIZE):
        yield matching[start : start + PAGE_SIZE]


def _pages_for(name: str, cfg: dict, args: argparse.Namespace, watermark: str, app_token: str):
    """Pick the transport for one dataset.

    Three of them now, and the choice is the only thing that varies: each
    yields pages of raw records filtered to the watermark, and everything
    downstream of here is shared. `--fixtures` wins over the registry so that
    a fixture run exercises the same buffering, flushing and manifest code for
    every dataset, including the Census one, without any network at all.
    """
    if args.fixtures:
        return fixture_pages(args.fixtures, name, watermark)
    if cfg.get("api") == "tigerweb":
        return census_pages(cfg, watermark)
    return socrata_pages(cfg["socrata_id"], watermark, app_token)


def resolve_watermark(cfg: dict, args: argparse.Namespace) -> str:
    """--since beats --full-refresh beats what is already in the zone.

    The last branch is the one to be careful with. An empty zone and a zone
    pointed somewhere that holds nothing are the same answer here, None, and
    None falls through to `start_date` and refetches everything: 8.8 million
    rows for 311, correct and expensive and previously silent. It announces
    itself now, because reading the zone is the only source of the position
    (ADR-4) and there is nothing to cross-check it against.

    A bucket that genuinely cannot be listed raises rather than coming back
    empty, so that case fails loudly on its own and is not what this guards.
    What it guards is a zone that is fine and simply is not the one you meant.
    """
    if args.since:
        return args.since
    if args.full_refresh:
        return cfg["start_date"]
    watermark = raw_zone.read_watermark(cfg["table"], args.raw_root)
    if watermark is None:
        root = args.raw_root if args.raw_root is not None else raw_zone.raw_root()
        print(
            f"[{cfg['table']}] no rows in {root}, so this is a full backfill from "
            f"{cfg['start_date']}, not a resume."
        )
        return cfg["start_date"]
    return watermark


def ingest_one(name: str, args: argparse.Namespace, app_token: str) -> dict:
    """Fetch one dataset into the raw zone. Returns its run manifest."""
    cfg = DATASETS[name]
    table = cfg["table"]
    run_id = raw_zone.new_run_id()
    started_at = datetime.now(timezone.utc)
    ingest_date = started_at.date().isoformat()
    watermark = resolve_watermark(cfg, args)

    # A full refresh writes a complete new copy beside the old one and swaps
    # it in only once the fetch has finished. Writing in place would mean a
    # network failure halfway through leaves the durable raw zone holding
    # neither the old data nor the new.
    write_table = f"{table}.rebuild-{run_id}" if args.full_refresh else table

    mode = "fixtures" if args.fixtures else ("full-refresh" if args.full_refresh else "incremental")
    print(f"[{name}] {mode}: fetching rows with :updated_at > {watermark}")

    pages = _pages_for(name, cfg, args, watermark, app_token)

    manifest = {
        "run_id": run_id,
        "dataset": name,
        "table_name": table,
        "ingest_date": ingest_date,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "watermark_in": watermark,
        "watermark_out": watermark,
        "rows_written": 0,
        "files_written": 0,
        "mode": mode,
        "status": "failed",
        "error": None,
    }

    buffer: list[dict] = []
    try:
        for page in pages:
            if page:
                ingested_at = datetime.now(timezone.utc).isoformat()
                buffer.extend(normalize_record(r, ingested_at, run_id) for r in page)
                manifest["watermark_out"] = max(
                    manifest["watermark_out"], page[-1].get(":updated_at", watermark)
                )
            if len(buffer) >= ROWS_PER_FILE:
                _flush(
                    write_table,
                    buffer,
                    run_id,
                    manifest,
                    root=args.raw_root,
                    ingest_date=ingest_date,
                )
                buffer = []
        if buffer:
            _flush(
                write_table, buffer, run_id, manifest, root=args.raw_root, ingest_date=ingest_date
            )
        manifest["status"] = "success"
    except Exception as exc:
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        _finish(manifest, table, args.raw_root)
        raise

    if args.full_refresh:
        _swap_in(table, write_table, run_id, args.raw_root)

    _finish(manifest, table, args.raw_root)

    if manifest["rows_written"] == 0:
        print(f"[{name}] already up to date, nothing new to load")
    else:
        print(
            f"[{name}] done: {manifest['rows_written']} rows in "
            f"{manifest['files_written']} file(s) under "
            f"{raw_zone.dataset_dir(table, args.raw_root)}"
        )
    return manifest


def _flush(
    table: str,
    buffer: list[dict],
    run_id: str,
    manifest: dict,
    *,
    root: Path | str | None,
    ingest_date: str,
) -> None:
    seq = manifest["files_written"]
    path = raw_zone.write_batch(table, buffer, run_id, seq, ingest_date=ingest_date, root=root)
    manifest["rows_written"] += len(buffer)
    manifest["files_written"] += 1
    print(f"  wrote {len(buffer)} rows to {path}")


def _finish(manifest: dict, table: str, root: Path | str | None) -> None:
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    raw_zone.write_run_manifest(table, manifest, root)


def reject_remote_full_refresh(root: Path | str | None) -> None:
    """`--full-refresh` needs an atomic swap, and object storage has no rename.

    Checked before the first request rather than before the swap, because the
    alternative is discovering it after refetching 8.8 million rows.

    Locally `_swap_in` renames a finished tree into place, so at every instant
    the zone holds the whole old copy or the whole new one, which is the one
    exception ADR-4 grants to the append-only rule. GCS has neither a rename nor
    a multi-object transaction: the same swap is a delete of every old object
    followed by a copy of every new one, and any failure inside that window
    leaves a raw zone that is neither copy and that nothing can reconstruct.
    Doing that and calling it a swap would make the exception unbounded, so this
    refuses instead.

    The honest way to do it is a refresh into a local zone followed by an upload,
    which is a copy rather than a swap: the new tree can be checked before
    anything is deleted, and the delete is then a separate deliberate act.
    """
    if not remote.is_remote(root if root is not None else raw_zone.raw_root()):
        return
    sys.exit(
        "--full-refresh cannot run against a remote raw zone.\n"
        "\n"
        "It replaces a dataset's whole tree, which is safe locally because a\n"
        "directory rename is atomic. GCS has no rename, so the same operation is\n"
        "a delete of every object followed by a re-upload, and an interruption\n"
        "anywhere in between leaves the zone holding neither the old data nor the\n"
        "new. The raw zone is append-only precisely so that cannot happen.\n"
        "\n"
        "Rebuild locally and upload the result instead:\n"
        "  RAW_ZONE_DIR=data/refresh python ingestion/ingest.py <dataset> --full-refresh\n"
        "  # check it, then replace the prefix by hand, deliberately\n"
        "\n"
        "An incremental run needs none of this and works against the bucket."
    )


def _swap_in(table: str, staged_table: str, run_id: str, root: Path | None) -> None:
    """Replace the dataset's tree with the freshly rebuilt one. Local only.

    Rename-then-delete rather than delete-then-rename: at every instant a
    complete tree exists at the final path, so an interruption costs disk
    space rather than the raw zone. That is the property a bucket cannot
    offer, and `reject_remote_full_refresh` is where it is turned away.
    """
    final = raw_zone.dataset_dir(table, root)
    staged = raw_zone.dataset_dir(staged_table, root)
    if not staged.exists():
        # Nothing came back. Leave the existing tree alone rather than
        # replacing it with an empty one.
        print(f"[{table}] full refresh returned no rows; existing files kept")
        return
    superseded = final.with_name(f"{table}.superseded-{run_id}")
    if final.exists():
        final.rename(superseded)
    staged.rename(final)
    shutil.rmtree(superseded, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest DataSF datasets into the Parquet raw zone (ADR-4)."
    )
    parser.add_argument(
        "datasets", nargs="*", help=f"dataset names to ingest: {', '.join(DATASETS)}"
    )
    parser.add_argument("--all", action="store_true", help="ingest every registered dataset")
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="refetch from start_date and atomically replace the dataset's Parquet tree",
    )
    parser.add_argument(
        "--since", default=None, help="override the watermark, e.g. 2023-01-01T00:00:00.000Z"
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="read records from <dir>/<dataset>.json instead of the API (no network)",
    )
    parser.add_argument(
        "--raw-root",
        type=remote.zone_root,
        default=None,
        help="root of the raw zone: a directory or a gs:// prefix "
        "(default: $RAW_ZONE_DIR, else $RAW_ZONE_URI, else data/raw)",
    )
    args = parser.parse_args()

    names = list(DATASETS) if args.all else args.datasets
    if not names:
        parser.error("pass one or more dataset names, or --all")
    unknown = [n for n in names if n not in DATASETS]
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(unknown)}. Valid: {', '.join(DATASETS)}")

    if args.full_refresh:
        reject_remote_full_refresh(args.raw_root)

    app_token = os.environ.get("SOCRATA_APP_TOKEN", "")
    root = args.raw_root if args.raw_root is not None else raw_zone.raw_root()
    print(f"Raw zone: {root}")

    for name in names:
        ingest_one(name, args, app_token)

    print("\nRaw zone updated. Load it into a warehouse with:")
    print("  python ingestion/load.py --all --target duckdb")


if __name__ == "__main__":
    main()
