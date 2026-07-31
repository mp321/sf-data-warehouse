"""Registry of DataSF datasets this project ingests.

Each entry maps a friendly name to:
  socrata_id: the dataset id on data.sfgov.org (visible in the dataset URL)
  table:      the raw table name. It is both the directory under data/raw/
              and the table ingestion/load.py creates in DuckDB and BigQuery,
              so one name identifies the dataset everywhere downstream.
  start_date: how far back the first load reaches, using Socrata's
              system field :updated_at. Override per run with --since.

The dbt side keeps its own copy of this list, as var('pipeline_sources') in
dbt/dbt_project.yml, because YAML cannot read Python. Adding a dataset means
adding it in both places.

Note on start dates: the 311 and permits datasets are large, so the
default backfill starts at 2024. You can widen it later with, e.g.
    python ingestion/ingest.py 311_cases --since 2020-01-01T00:00:00.000Z
The budget and film locations datasets are small, so they backfill fully.
"""

DATASETS = {
    "311_cases": {
        "socrata_id": "vw6y-z8j6",
        "table": "raw_311_cases",
        "start_date": "2024-01-01T00:00:00.000Z",
        "description": "SF311 service requests: potholes, graffiti, street cleaning, etc.",
    },
    "building_permits": {
        "socrata_id": "i98e-djp9",
        "table": "raw_building_permits",
        "start_date": "2024-01-01T00:00:00.000Z",
        "description": "Building permit applications filed with the city.",
    },
    "city_budget": {
        "socrata_id": "xdgd-c79v",
        "table": "raw_city_budget",
        "start_date": "1970-01-01T00:00:00.000Z",
        "description": "City and County of San Francisco budget line items by year.",
    },
    "film_locations": {
        "socrata_id": "yitu-d5am",
        "table": "raw_film_locations",
        "start_date": "1970-01-01T00:00:00.000Z",
        "description": (
            "Movies and TV filmed in San Francisco, with locations and fun facts. "
            "The fun one, and the pipeline canary: small enough to ingest end to end "
            "in seconds."
        ),
    },
}
