"""Registry of datasets this project ingests.

Each entry maps a friendly name to:
  socrata_id: the dataset id on data.sfgov.org (visible in the dataset URL).
              Absent for datasets that are not Socrata; see `api` below.
  table:      the raw table name. It is both the directory under data/raw/
              and the table ingestion/load.py creates in DuckDB and BigQuery,
              so one name identifies the dataset everywhere downstream.
  start_date: how far back the first load reaches, using Socrata's
              system field :updated_at. Override per run with --since.
  api:        which transport ingest.py uses. "socrata" (the default) or
              "tigerweb" for the Census block groups, which come from the
              Census Bureau's ArcGIS service rather than from DataSF.
  kind:       "point", "polygon" or "nonspatial". This is what
              ingestion/spatial.py dispatches on, and it is the difference
              between a dataset that gets H3 cells and one that gets a
              covering set of them.
  grain_key:  the raw column that identifies a row, before staging renames
              it. spatial.py writes this into derived_point_h3 as row_key,
              and the staging model joins back on it, so it must match the
              column the staging model's dedup partitions by.
  geometry:   where the coordinates or the polygon live in the raw row.
              Points use either {"latitude": ..., "longitude": ...} for flat
              columns or {"geojson_point": ...} for a GeoJSON object that
              normalize_record stored as JSON text. Polygons use
              {"geojson": ..., "boundary_id": ..., "boundary_name": ...}.

The dbt side keeps its own copy of this list, as var('pipeline_sources') in
dbt/dbt_project.yml, because YAML cannot read Python. Adding a dataset means
adding it in both places.

Note on start dates: the 311 and permits datasets are large, so the
default backfill starts at 2024. You can widen it later with, e.g.
    python ingestion/ingest.py 311_cases --since 2020-01-01T00:00:00.000Z
The budget, film locations, boundary and census datasets are small, so they
backfill fully.

Business locations and street trees also backfill fully despite their size
(365k and 198k rows). They are current-state registries rather than event
logs: a partial backfill by :updated_at gives you the businesses that
happened to be edited recently, which is not a subset anyone can reason
about. See ADR-7.
"""

# Bounding box for validating point coordinates, in degrees. Deliberately
# loose: it is a rejection filter for null-island rows, coordinates that
# arrive swapped, and the Web Mercator metres that DataSF occasionally leaks
# into a lat/long column, not a claim about the city limits. Points on the
# Farallon Islands (part of District 1) sit at about -123.00, so the western
# edge has to reach past them.
SF_BOUNDING_BOX = {
    "min_latitude": 37.60,
    "max_latitude": 37.93,
    "min_longitude": -123.20,
    "max_longitude": -122.28,
}

DATASETS = {
    "311_cases": {
        "socrata_id": "vw6y-z8j6",
        "table": "raw_311_cases",
        "start_date": "2024-01-01T00:00:00.000Z",
        "kind": "point",
        "grain_key": "service_request_id",
        "geometry": {"latitude": "lat", "longitude": "long"},
        "description": "SF311 service requests: potholes, graffiti, street cleaning, etc.",
    },
    "building_permits": {
        "socrata_id": "i98e-djp9",
        "table": "raw_building_permits",
        "start_date": "2024-01-01T00:00:00.000Z",
        "kind": "point",
        "grain_key": "record_id",
        "geometry": {"geojson_point": "location"},
        "description": "Building permit applications filed with the city.",
    },
    "business_locations": {
        "socrata_id": "g8m3-pdis",
        "table": "raw_business_locations",
        "start_date": "1970-01-01T00:00:00.000Z",
        "kind": "point",
        "grain_key": "uniqueid",
        "geometry": {"geojson_point": "location"},
        "description": (
            "Registered business locations: every business with a San Francisco tax "
            "certificate, open or closed. About 365k rows, and the denominator behind "
            "'per 1000 businesses' rates."
        ),
    },
    "street_trees": {
        "socrata_id": "tkzw-k3nq",
        "table": "raw_street_trees",
        "start_date": "1970-01-01T00:00:00.000Z",
        "kind": "point",
        "grain_key": "treeid",
        "geometry": {"latitude": "latitude", "longitude": "longitude"},
        "description": (
            "Street Tree List: every tree DPW knows about, with species, plant date and "
            "coordinates. About 198k rows. Dense, evenly spread and stable, which makes "
            "it the best available stress test for the H3 machinery."
        ),
    },
    "analysis_neighborhoods": {
        "socrata_id": "ajp5-b2md",
        "table": "raw_analysis_neighborhoods",
        "start_date": "1970-01-01T00:00:00.000Z",
        "kind": "polygon",
        "grain_key": "nhood",
        "geometry": {
            "boundary_set": "analysis_neighborhood",
            "geojson": "the_geom",
            "boundary_id": "nhood",
            "boundary_name": "nhood",
        },
        "description": (
            "The 41 analysis neighborhoods, as MultiPolygons. The city's standard "
            "reporting geography, and the one most DataSF datasets already stamp onto "
            "rows as a string."
        ),
    },
    "supervisor_districts": {
        "socrata_id": "f2zs-jevy",
        "table": "raw_supervisor_districts",
        "start_date": "1970-01-01T00:00:00.000Z",
        "kind": "polygon",
        "grain_key": "sup_dist_num",
        "geometry": {
            "boundary_set": "supervisor_district",
            "geojson": "polygon",
            "boundary_id": "sup_dist_num",
            "boundary_name": "sup_dist_name",
        },
        "description": (
            "The 11 supervisor districts as drawn in the 2022 redistricting, as "
            "MultiPolygons. The 2012 boundaries (keex-zmn4) are a different dataset; "
            "rows stamped with a district before 2022 were assigned under those."
        ),
    },
    "census_block_groups": {
        "table": "raw_census_block_groups",
        "start_date": "1970-01-01T00:00:00.000Z",
        "api": "tigerweb",
        "kind": "polygon",
        "grain_key": "geoid",
        "geometry": {
            "boundary_set": "census_block_group",
            "geojson": "the_geom",
            "boundary_id": "geoid",
            "boundary_name": "name",
            # The only boundary set carrying measures rather than just a
            # shape. These are what every "per 1000 residents" rate in the
            # marts is ultimately divided by.
            "population": "pop100",
            "housing_units": "hu100",
        },
        "description": (
            "The 681 San Francisco census block groups from the 2020 Census, as "
            "polygons carrying POP100 and HU100. The population denominator, and the "
            "only source here that is not DataSF. See ingestion/census.py and ADR-7 "
            "for why this is the Decennial count rather than an ACS estimate."
        ),
    },
    "city_budget": {
        "socrata_id": "xdgd-c79v",
        "table": "raw_city_budget",
        "start_date": "1970-01-01T00:00:00.000Z",
        "kind": "nonspatial",
        "grain_key": "_socrata_id",
        "geometry": None,
        "description": "City and County of San Francisco budget line items by year.",
    },
    "film_locations": {
        "socrata_id": "yitu-d5am",
        "table": "raw_film_locations",
        "start_date": "1970-01-01T00:00:00.000Z",
        "kind": "point",
        "grain_key": "_socrata_id",
        "geometry": {"latitude": "latitude", "longitude": "longitude"},
        "description": (
            "Movies and TV filmed in San Francisco, with locations and fun facts. "
            "The fun one, and the pipeline canary: small enough to ingest end to end "
            "in seconds. It does carry usable coordinates on about 19 rows in 20, "
            "which ADR-3 got wrong and ADR-7 corrects."
        ),
    },
}


def point_datasets() -> dict:
    """Registry entries that carry a single point per row."""
    return {name: cfg for name, cfg in DATASETS.items() if cfg["kind"] == "point"}


def polygon_datasets() -> dict:
    """Registry entries that carry a polygon per row."""
    return {name: cfg for name, cfg in DATASETS.items() if cfg["kind"] == "polygon"}
