"""Incremental ingestion from DataSF (Socrata) into BigQuery.

How it works, in plain terms:
  1. Look in BigQuery for the newest _socrata_updated_at we already have.
     If the table does not exist yet, fall back to the dataset's start_date.
  2. Ask the Socrata API for rows updated after that watermark, ordered by
     update time, 5000 rows per page.
  3. Land every value as a STRING in a raw table. No cleaning here on
     purpose: raw stays raw, and all typing/renaming happens in dbt staging.
  4. Appends can create multiple versions of the same record over time.
     That is expected. Staging models deduplicate to the latest version.

Usage:
    python ingestion/ingest.py 311_cases
    python ingestion/ingest.py 311_cases film_locations
    python ingestion/ingest.py --all
    python ingestion/ingest.py 311_cases --full-refresh
    python ingestion/ingest.py 311_cases --since 2023-01-01T00:00:00.000Z

Required environment variables:
    GCP_PROJECT_ID                   your Google Cloud project id
    GOOGLE_APPLICATION_CREDENTIALS   path to a service account key JSON
Optional:
    BQ_RAW_DATASET   BigQuery dataset for raw tables (default: raw_datasf)
    BQ_LOCATION      BigQuery location (default: US)
    SOCRATA_APP_TOKEN  free token from data.sfgov.org, raises rate limits
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from datasets import DATASETS

SOCRATA_DOMAIN = "https://data.sfgov.org"
PAGE_SIZE = 5000          # rows per API request
ROWS_PER_LOAD = 50000     # buffer this many rows before each BigQuery load job
MAX_RETRIES = 3


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required environment variable: {name}. See SETUP.md.")
    return value


def sanitize_column(name: str) -> str:
    """Make a Socrata field name safe for BigQuery.

    Socrata system fields start with ':' (e.g. :updated_at); we rename
    them to _socrata_updated_at etc. so they are valid column names.
    """
    if name.startswith(":"):
        name = "_socrata_" + name[1:]
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name)
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = "_" + cleaned
    return cleaned.lower()


def normalize_record(record: dict, ingested_at: str) -> dict:
    """Flatten one API record into an all-STRING row for the raw table."""
    row = {}
    for key, value in record.items():
        col = sanitize_column(key)
        if value is None:
            row[col] = None
        elif isinstance(value, (dict, list)):
            row[col] = json.dumps(value)  # nested objects (e.g. point geometries) kept as JSON text
        else:
            row[col] = str(value)
    row["_ingested_at"] = ingested_at
    return row


def get_watermark(client: bigquery.Client, table_ref: str, start_date: str) -> str:
    """Return the newest _socrata_updated_at already loaded, or start_date."""
    try:
        client.get_table(table_ref)
    except NotFound:
        return start_date
    query = f"select max(_socrata_updated_at) as w from `{table_ref}`"
    rows = list(client.query(query).result())
    watermark = rows[0]["w"]
    return watermark or start_date


def fetch_page(session: requests.Session, socrata_id: str, watermark: str,
               offset: int, app_token: str) -> list:
    params = {
        "$select": ":id,:created_at,:updated_at,*",
        "$where": f":updated_at > '{watermark}'",
        "$order": ":updated_at",
        "$limit": PAGE_SIZE,
        "$offset": offset,
    }
    headers = {"X-App-Token": app_token} if app_token else {}
    url = f"{SOCRATA_DOMAIN}/resource/{socrata_id}.json"
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


def load_rows(client: bigquery.Client, table_ref: str, rows: list, truncate: bool) -> None:
    """Load a batch of rows. All columns are STRING; new columns are allowed."""
    columns = sorted({col for row in rows for col in row})
    schema = [bigquery.SchemaField(col, "STRING") for col in columns]
    if truncate:
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
    else:
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        )
    job = client.load_table_from_json(rows, table_ref, job_config=job_config)
    job.result()  # wait for completion; raises on failure


def ensure_dataset(client: bigquery.Client, project: str, dataset_id: str) -> None:
    dataset = bigquery.Dataset(f"{project}.{dataset_id}")
    dataset.location = os.environ.get("BQ_LOCATION", "US")
    client.create_dataset(dataset, exists_ok=True)


def ingest_one(name: str, client: bigquery.Client, project: str, raw_dataset: str,
               app_token: str, full_refresh: bool, since: str) -> None:
    cfg = DATASETS[name]
    table_ref = f"{project}.{raw_dataset}.{cfg['table']}"

    if since:
        watermark = since
    elif full_refresh:
        watermark = cfg["start_date"]
    else:
        watermark = get_watermark(client, table_ref, cfg["start_date"])

    print(f"[{name}] fetching rows with :updated_at > {watermark}")

    session = requests.Session()
    offset = 0
    total = 0
    buffer = []
    truncate_next_load = full_refresh

    while True:
        page = fetch_page(session, cfg["socrata_id"], watermark, offset, app_token)
        if page:
            ingested_at = datetime.now(timezone.utc).isoformat()
            buffer.extend(normalize_record(r, ingested_at) for r in page)

        last_page = len(page) < PAGE_SIZE
        if buffer and (len(buffer) >= ROWS_PER_LOAD or last_page):
            load_rows(client, table_ref, buffer, truncate_next_load)
            truncate_next_load = False
            total += len(buffer)
            print(f"[{name}] loaded {total} rows so far")
            buffer = []

        if last_page:
            break
        offset += PAGE_SIZE
        time.sleep(0.3)  # be polite to the API

    if total == 0:
        print(f"[{name}] already up to date, nothing new to load")
    else:
        print(f"[{name}] done: {total} rows loaded into {table_ref}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest DataSF datasets into BigQuery.")
    parser.add_argument("datasets", nargs="*",
                        help=f"dataset names to ingest: {', '.join(DATASETS)}")
    parser.add_argument("--all", action="store_true", help="ingest every registered dataset")
    parser.add_argument("--full-refresh", action="store_true",
                        help="wipe the raw table and reload from start_date")
    parser.add_argument("--since", default=None,
                        help="override the watermark, e.g. 2023-01-01T00:00:00.000Z")
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
    app_token = os.environ.get("SOCRATA_APP_TOKEN", "")

    client = bigquery.Client(project=project)
    ensure_dataset(client, project, raw_dataset)

    for name in names:
        ingest_one(name, client, project, raw_dataset, app_token,
                   args.full_refresh, args.since)


if __name__ == "__main__":
    main()
