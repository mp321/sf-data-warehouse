"""Registry of DataSF datasets this project ingests.

Each entry maps a friendly name to:
  socrata_id: the dataset id on data.sfgov.org (visible in the dataset URL)
  table:      the BigQuery table it lands in (inside the raw dataset)
  start_date: how far back the first load reaches, using Socrata's
              system field :updated_at. Override per run with --since.

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
        "description": "Movies and TV filmed in San Francisco, with locations and fun facts. The fun one.",
    },
}
