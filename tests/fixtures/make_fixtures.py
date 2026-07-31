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
"""

import copy
import json
import urllib.parse
import urllib.request
from pathlib import Path

OUT_DIR = Path("tests/fixtures/socrata")
SAMPLE_ROWS = 24  # real records kept per dataset, before the coverage record
SCHEMA_SCAN_ROWS = 400  # rows scanned to find every column the dataset publishes

DATASETS = {
    "311_cases": "vw6y-z8j6",
    "building_permits": "i98e-djp9",
    "city_budget": "xdgd-c79v",
    "film_locations": "yitu-d5am",
}

# Identity fields to overwrite on the synthetic coverage record so it cannot
# collide with a real one and break a unique test.
IDENTITY_FIELDS = {
    "311_cases": {":id": "row-fixture~coverage", "service_request_id": "999000001"},
    "building_permits": {":id": "row-fixture~coverage", "record_id": "999000000000001"},
    "city_budget": {":id": "row-fixture~coverage"},
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


def coverage_record(name: str, scanned: list[dict]) -> dict:
    """One record carrying every field the dataset publishes.

    Values are borrowed from the first scanned row that had a non-empty value
    for each field, so types and formats stay realistic rather than invented.
    """
    record: dict = {}
    for row in scanned:
        for key, value in row.items():
            if key not in record and value not in (None, ""):
                record[key] = value
    record.update(IDENTITY_FIELDS[name])
    # Newest instant in the file, so deduplication keeps whichever row the
    # adversarial edits below intend, not this one.
    record[":updated_at"] = "2026-07-27T00:00:00.000Z"
    return record


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

    # Budget: an unparseable amount and a legitimate negative one.
    data["city_budget"][1]["budget"] = ""
    data["city_budget"][2]["budget"] = "-1340493935"

    # Film: a title with no release year, and one with no coordinates. Both
    # exist upstream and both must survive without a not_null test firing.
    data["film_locations"][1].pop("release_year", None)
    for field in ("latitude", "longitude", "point"):
        data["film_locations"][2].pop(field, None)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, list[dict]] = {}

    for name, socrata_id in DATASETS.items():
        scanned = fetch(socrata_id, SCHEMA_SCAN_ROWS)
        records = copy.deepcopy(scanned[:SAMPLE_ROWS])
        records.append(coverage_record(name, scanned))
        data[name] = records

    add_adversarial_rows(data)

    for name, records in data.items():
        fields = {key for record in records for key in record}
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
        size_kb = path.stat().st_size // 1024
        print(f"{path}  {len(records)} records  {len(fields)} columns  {size_kb} KB")


if __name__ == "__main__":
    main()
