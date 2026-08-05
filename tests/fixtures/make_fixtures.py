"""Regenerate the committed Socrata fixtures in tests/fixtures/socrata/.

Run from the repo root, with network access:

    python tests/fixtures/make_fixtures.py

The fixtures let CI run the whole pipeline with no credentials and no
network: `ingest.py --fixtures` reads them instead of calling the API, and
everything downstream is the production code path.

Two invariants, both of which have already broken a build:

1. Every column the staging models reference must appear in the fixture.
   Socrata omits null fields per record, so a column present in 2% of rows
   (street_number_suffix, first_construction_document_date) is simply absent
   from a 25-row sample, and the Parquet file then has no such column at all.
   The staging model fails with "Referenced column not found", which is the
   right behaviour in production, because a column vanishing upstream should
   stop the build rather than quietly become NULL. It just makes a naive
   sample useless as a fixture. Hence the coverage record below: one
   synthetic row carrying every field seen in a large sample, with real
   values borrowed from the rows that had them.

2. The fixtures must pass the tests in _datasf__models.yml. Adversarial
   values go only on columns with no not_null test. Breaking a test to prove
   a point makes CI useless at proving anything else.

3. **Boundary fixtures have to be real polygons, not placeholders.** The
   spatial step (ADR-5, ADR-6) is the largest piece of machinery in this
   project and a fixture run that skipped it would leave the H3 bridge, the
   population interpolation and both activity marts untested in CI. So all
   41 neighborhoods, all 11 supervisor districts and all 681 block groups are
   kept, complete, and only their vertices are thinned. See simplify_geometry
   for the tradeoff that makes them small enough to commit.
"""

import copy
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ingestion"))

# Imported after the sys.path insert above, which is what makes ingestion/
# importable from here without turning it into a package.
from census import FIELDS, VINTAGE, census_pages
from dataset_registry import point_datasets, polygon_datasets

OUT_DIR = Path("tests/fixtures/socrata")
SAMPLE_ROWS = 24  # real records kept per dataset, before the coverage record
SCHEMA_SCAN_ROWS = 400  # rows scanned to find every column the dataset publishes

# Derived from the registry rather than listed, since PLAN-5 step 4 made that
# possible. This file used to carry its own copy of the names and Socrata ids,
# which is a worse duplicate than the dbt one that step removed: a fixture
# built from the wrong id is a green CI run against a dataset the pipeline
# does not ingest, and nothing downstream can tell.
#
# Sampled datasets are the point ones. Boundary sets are the polygon ones and
# are fetched whole and thinned rather than sampled: a sample of a boundary set
# is not a smaller boundary set, it is a map with holes in it, and points in
# the missing neighborhoods would come out unassigned and fail the population
# reconciliation test on an artefact of the fixture.
#
# `socrata_id` is absent on the tigerweb entry, which is why census_block_groups
# drops out of this comprehension and is fetched by fetch_census() below.
DATASETS = {
    name: cfg["socrata_id"] for name, cfg in point_datasets().items() if cfg.get("socrata_id")
}

BOUNDARY_DATASETS = {
    name: cfg["socrata_id"] for name, cfg in polygon_datasets().items() if cfg.get("socrata_id")
}

GEOMETRY_COLUMNS = ("the_geom", "polygon")

# Vertex thinning for the boundary fixtures. Keeping every 4th vertex and
# rounding to 5 decimal places takes 65,000 vertices down to about 16,000 and
# the committed fixtures from roughly 3 MB to a few hundred KB.
#
# 5 decimal places is about one metre, which is far finer than the 65 m H3
# cells the fixtures exercise. Thinning is the lossy part: it moves a boundary
# by tens of metres in places, so the cell counts a fixture run produces are
# not the cell counts real data produces. That is fine and is the point of the
# distinction: CI proves the machinery runs end to end and the tests hold,
# and the numbers in the ADRs come from `make spatial` on the real zone.
KEEP_EVERY_NTH_VERTEX = 4
COORDINATE_PRECISION = 5
MINIMUM_RING_VERTICES = 4

# Identity fields to overwrite on the synthetic coverage record so it cannot
# collide with a real one and break a unique test.
IDENTITY_FIELDS = {
    "311_cases": {":id": "row-fixture~coverage", "service_request_id": "999000001"},
    "building_permits": {":id": "row-fixture~coverage", "record_id": "999000000000001"},
    "business_locations": {":id": "row-fixture~coverage", "uniqueid": "9990000-99-999-9990000"},
    "film_locations": {":id": "row-fixture~coverage"},
}


def fetch(socrata_id: str, limit: int) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "$select": "*,:id,:created_at,:updated_at",
            "$order": ":updated_at,:id",
            "$limit": limit,
        }
    )
    url = f"https://data.sfgov.org/resource/{socrata_id}.json?{query}"
    with urllib.request.urlopen(url, timeout=120) as response:
        return json.load(response)


def dataset_fields(socrata_id: str) -> list[str]:
    """Every field name the dataset publishes, from its metadata.

    The scan cannot be trusted to reveal the column list. Socrata omits null
    fields per record, and the scan is ordered by :updated_at, so a column
    that is only populated on recently-touched rows is invisible in the oldest
    400. That is not hypothetical, and the worked example is worth keeping
    even though the dataset is not: street_trees published latitude, longitude,
    location, xcoord and ycoord, and not one of its 400 oldest rows carried any
    of them, so the first fixture built from a scan alone produced a dataset
    with no coordinates and broke `make spatial`. The dataset was cut in
    ADR-10; the failure mode is a property of Socrata and is still live for
    every dataset here.

    :@computed_region_* fields are skipped. They are Socrata's own spatial
    joins against curated region datasets, no model references them, and they
    change when DataSF adds a region.
    """
    with urllib.request.urlopen(
        f"https://data.sfgov.org/api/views/{socrata_id}.json", timeout=120
    ) as response:
        metadata = json.load(response)
    return [
        column["fieldName"]
        for column in metadata.get("columns", [])
        if not column["fieldName"].startswith(":@")
    ]


def fetch_field_value(socrata_id: str, field: str):
    """One real value for a field, from any row that has it.

    Used only for fields the scan never saw. One request per missing field,
    which is a handful per dataset, and it keeps the coverage record made of
    real values rather than invented ones: a fabricated latitude would pass
    the schema check and then fail the accepted_range test, or worse, pass it.
    """
    query = urllib.parse.urlencode(
        {"$select": field, "$where": f"{field} IS NOT NULL", "$limit": 1}
    )
    url = f"https://data.sfgov.org/resource/{socrata_id}.json?{query}"
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            rows = json.load(response)
    except OSError:
        return None
    return rows[0].get(field) if rows else None


def coverage_record(name: str, socrata_id: str, scanned: list[dict]) -> dict:
    """One record carrying every field the dataset publishes.

    Values are borrowed from the first scanned row that had a non-empty value
    for each field, so types and formats stay realistic rather than invented.
    Fields the scan never saw are fetched individually; see dataset_fields for
    why the scan alone is not enough.
    """
    record: dict = {}
    for row in scanned:
        for key, value in row.items():
            if key not in record and value not in (None, ""):
                record[key] = value

    missing = [field for field in dataset_fields(socrata_id) if field not in record]
    for field in missing:
        value = fetch_field_value(socrata_id, field)
        if value is not None:
            record[field] = value
    if missing:
        print(
            f"  [{name}] filled {len(missing)} field(s) absent from the scan: {', '.join(missing)}"
        )

    record.update(IDENTITY_FIELDS[name])
    # Newest instant in the file, so deduplication keeps whichever row the
    # adversarial edits below intend, not this one.
    record[":updated_at"] = "2026-07-27T00:00:00.000Z"
    return record


def simplify_ring(ring: list) -> list:
    """Thin one linear ring and round its coordinates.

    Keeps the first and last vertex whatever happens, so the ring stays
    closed, and never drops below four vertices, so it stays a polygon rather
    than becoming a line. A ring that is already short is returned rounded
    and otherwise untouched.
    """
    if len(ring) <= MINIMUM_RING_VERTICES:
        thinned = list(ring)
    else:
        thinned = ring[:-1:KEEP_EVERY_NTH_VERTEX]
        if len(thinned) < MINIMUM_RING_VERTICES - 1:
            thinned = ring[:-1][: MINIMUM_RING_VERTICES - 1]
        thinned.append(thinned[0])
    return [
        [round(point[0], COORDINATE_PRECISION), round(point[1], COORDINATE_PRECISION)]
        for point in thinned
    ]


def simplify_geometry(geometry: dict) -> dict:
    """Thin every ring of a Polygon or MultiPolygon."""
    if not geometry or "coordinates" not in geometry:
        return geometry
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon":
        return {
            "type": geometry_type,
            "coordinates": [simplify_ring(r) for r in geometry["coordinates"]],
        }
    if geometry_type == "MultiPolygon":
        return {
            "type": geometry_type,
            "coordinates": [
                [simplify_ring(ring) for ring in polygon] for polygon in geometry["coordinates"]
            ],
        }
    return geometry


def simplify_record(record: dict) -> dict:
    for column in GEOMETRY_COLUMNS:
        if isinstance(record.get(column), dict):
            record[column] = simplify_geometry(record[column])
    return record


def fetch_census() -> list[dict]:
    """Block groups from TIGERweb, or from the local raw zone if it is blocked.

    Calling census_pages rather than reimplementing the request means the
    fixture cannot drift from what a real ingest produces: if the field list
    or the synthetic system fields change, they change here too.

    The fallback is not defensive padding. TIGERweb sits behind a WAF that
    starts rejecting after a burst of requests, and regenerating fixtures is
    exactly the burst that trips it, so the first version of this reliably
    failed on the one task it was written for. The raw zone holds the output
    of a real ingest of the same endpoint, so reading it produces the same
    records; it is a cache, not a substitute.
    """
    try:
        records: list[dict] = []
        for page in census_pages({}, "1970-01-01T00:00:00.000Z"):
            records.extend(page)
        if records:
            return [simplify_record(record) for record in records]
        print("  TIGERweb returned nothing; falling back to the local raw zone")
    except Exception as exc:
        print(f"  TIGERweb unavailable ({exc}); falling back to the local raw zone")

    return _census_from_raw_zone()


def _census_from_raw_zone() -> list[dict]:
    """Reconstruct census records from data/raw, undoing normalize_record."""
    import duckdb  # noqa: PLC0415  (only needed on the fallback path)

    import raw_zone  # noqa: PLC0415

    table = "raw_census_block_groups"
    if not raw_zone.has_data(table):
        raise SystemExit(
            "TIGERweb is unreachable and data/raw/raw_census_block_groups is empty, so there "
            "is nothing to build the census fixture from. Run `python ingestion/ingest.py "
            "census_block_groups` when TIGERweb is responding, then retry."
        )

    columns = [field.lower() for field in FIELDS] + ["the_geom"]
    with duckdb.connect() as con:
        rows = con.execute(
            f"select {', '.join(columns)} from {raw_zone.read_sql(table)}"
        ).fetchall()

    records = []
    for row in rows:
        record = dict(zip(columns, row, strict=True))
        # normalize_record stored the geometry as JSON text and every other
        # value as a string. Undo the first, leave the second: the fixture is
        # an API response, and ingest.py will re-normalise it.
        record["the_geom"] = json.loads(record["the_geom"]) if record["the_geom"] else None
        record[":id"] = f"blockgroup-{record['geoid']}"
        record[":updated_at"] = VINTAGE
        record[":created_at"] = VINTAGE
        records.append(simplify_record(record))
    return records


def add_adversarial_rows(data: dict[str, list[dict]]) -> None:
    """Mutate the samples in place to cover what the models actually have to survive."""
    # 311: the same case ingested twice, opened then closed. Deduplication
    # must keep the Closed version.
    reopened = copy.deepcopy(data["311_cases"][0])
    data["311_cases"][0][":updated_at"] = "2026-07-28T12:00:00.000Z"
    data["311_cases"][0]["status_description"] = "Open"
    data["311_cases"][0].pop("closed_date", None)
    reopened[":updated_at"] = "2026-07-29T12:00:00.000Z"
    reopened["status_description"] = "Closed"
    reopened["closed_date"] = "2026-07-29T11:59:00.000"
    data["311_cases"].append(reopened)
    # Coordinates that do not parse. x_safe_cast must null them, not error.
    data["311_cases"][1]["lat"] = "not-a-number"
    data["311_cases"][1]["long"] = ""

    # Permits: a revised record under the same record_id, junk money, and a
    # row with no location so the JSON extraction has to cope with NULL.
    revised = copy.deepcopy(data["building_permits"][0])
    revised[":updated_at"] = "2026-07-29T12:00:00.000Z"
    revised["status"] = "complete"
    data["building_permits"].append(revised)
    data["building_permits"][1]["estimated_cost"] = "unknown"
    data["building_permits"][2].pop("location", None)

    # Film: a title with no release year, and one with no coordinates. Both
    # exist upstream and both must survive without a not_null test firing.
    data["film_locations"][1].pop("release_year", None)
    for field in ("latitude", "longitude", "point"):
        data["film_locations"][2].pop(field, None)

    # Businesses: a location outside San Francisco, which is the single most
    # common non-obvious thing in this dataset and the one the coordinate
    # classifier has to call out_of_bounds rather than dropping or accepting.
    data["business_locations"][3]["location"] = {
        "type": "Point",
        "coordinates": [-84.19922231, 33.954319593],
    }
    # And one whose coordinates are State Plane feet in a degree column, the
    # failure the Earth-bounds accepted_range test exists to catch. It must
    # come out `impossible`, not as a point somewhere past the moon.
    data["business_locations"][4]["location"] = {
        "type": "Point",
        "coordinates": [5999163.5213, 2110903.9816],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, list[dict]] = {}

    for name, socrata_id in DATASETS.items():
        scanned = fetch(socrata_id, SCHEMA_SCAN_ROWS)
        records = copy.deepcopy(scanned[:SAMPLE_ROWS])
        records.append(coverage_record(name, socrata_id, scanned))
        data[name] = records

    add_adversarial_rows(data)

    # Boundary sets are fetched whole and thinned rather than sampled. No
    # coverage record and no adversarial rows: these have a handful of columns
    # that are always populated, and a synthetic boundary would appear in
    # dim_neighborhood as a place that does not exist.
    boundaries: dict[str, list[dict]] = {}
    for name, socrata_id in BOUNDARY_DATASETS.items():
        boundaries[name] = [simplify_record(record) for record in fetch(socrata_id, 5000)]
    boundaries["census_block_groups"] = fetch_census()

    for name, records in data.items():
        _write(name, records, compact=False)
    for name, records in boundaries.items():
        # Compact rather than indented. At indent=2 a coordinate pair costs
        # four lines, so the three boundary fixtures would run to tens of
        # megabytes of whitespace. Nobody reads a polygon in a diff anyway.
        _write(name, records, compact=True)


def _write(name: str, records: list[dict], *, compact: bool) -> None:
    fields = {key for record in records for key in record}
    path = OUT_DIR / f"{name}.json"
    if compact:
        text = json.dumps(records, sort_keys=True, separators=(",", ":"))
    else:
        text = json.dumps(records, indent=2, sort_keys=True)
    path.write_text(text + "\n")
    size_kb = path.stat().st_size // 1024
    print(f"{path}  {len(records)} records  {len(fields)} columns  {size_kb} KB")


if __name__ == "__main__":
    main()
