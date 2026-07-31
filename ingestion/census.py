"""Census block groups, the population denominator, from the Census TIGERweb service.

This is the one source in the project that is not DataSF. It exists because
every count mart needs a denominator: 311 volume per neighborhood without a
population underneath it is a map of where people live, drawn slowly.

**Why TIGERweb and not the ACS API.** The plan asked for ACS 5-year block
group population. `api.census.gov` now rejects unauthenticated requests with
"Missing Key" on every endpoint, including the county-level ones that used to
be keyless, so an ACS 5-year fetch needs an API key. That would put a
credential on the critical path of `make ingest`, which ADR-1 spent a whole
decision making credential-free, and it would mean a fresh clone could not
build the marts. TIGERweb's Census 2020 block group layer carries POP100 and
HU100 alongside the geometry, needs no key, and returns both in one request.

The substitution is not free and ADR-7 records it: this is the 2020 Decennial
enumeration, not a rolling 5-year estimate. It is an actual count rather than
a sample with a margin of error, which for a denominator is an improvement,
but it is fixed at April 2020 and drifts further out of date every year.
`CENSUS_API_KEY` is honoured if set, and switches the population columns to
ACS 5-year; see `fetch_acs_population`.

Transport only. Like `socrata_pages` in ingest.py, this yields pages of
records with values left exactly as the API sent them, and everything
downstream is the same code path.

Usage: this is not a command. `ingest.py` dispatches to it on the registry's
`api: tigerweb`, so:

    python ingestion/ingest.py census_block_groups
    python ingestion/ingest.py census_block_groups --full-refresh
"""

import os
import time

import requests

# The Census 2020 block group layer. The service is versioned by vintage and
# the layer number is not stable across vintages, so both are pinned. Layer 8
# is "Census Block Groups" in tigerWMS_Census2020; the same number is a Tribal
# layer in some other services, which is the sort of thing that silently
# returns zero rows rather than erroring.
TIGERWEB_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
    "/tigerWMS_Census2020/MapServer/8/query"
)

# California, San Francisco County. San Francisco is both a city and a county,
# so one county code covers the whole jurisdiction with nothing to clip.
STATE_FIPS = "06"
COUNTY_FIPS = "075"

# The vintage, used as the synthetic watermark. The 2020 Decennial block
# groups do not change, so a second `make ingest` fetches nothing and says so
# rather than re-downloading a megabyte to discover it is identical. Bumping
# this string, or passing --full-refresh, is what forces a refetch.
VINTAGE = "2020-04-01T00:00:00.000Z"

FIELDS = [
    "GEOID",
    "STATE",
    "COUNTY",
    "TRACT",
    "BLKGRP",
    "NAME",
    "POP100",
    "HU100",
    "AREALAND",
    "AREAWATER",
    "INTPTLAT",
    "INTPTLON",
]

PAGE_SIZE = 1000
TIMEOUT = 120
MAX_RETRIES = 4

# TIGERweb sits behind a WAF that rejects bursts, and it does it with HTTP 200
# and an HTML body rather than a status code or a Retry-After. Observed while
# building the fixtures: a dozen requests in a few minutes and every
# subsequent one came back as "Request Rejected" HTML, which json() then
# failed to parse with an error naming neither the service nor the reason.
# Hence the identifying User-Agent, the backoff, and the explicit HTML check
# in _get_page.
HEADERS = {"User-Agent": "sf-data-warehouse/1.0 (open data pipeline; contact via repository)"}


def census_pages(cfg: dict, watermark: str):
    """Yield pages of block group features, shaped like Socrata records.

    Ingestion's contract is a generator of lists of dicts whose values are
    JSON-native, so `normalize_record` can flatten them. The geometry is
    yielded as a dict and JSON-encoded there, exactly as a Socrata polygon
    would be, which is what lets one staging model shape read both.
    """
    if watermark >= VINTAGE:
        # Nothing to do. Said out loud rather than returning quietly, because
        # "0 rows" from a boundary source is otherwise indistinguishable from
        # a broken request.
        print(f"  block groups are vintage {VINTAGE}, already at or past the watermark")
        return

    session = requests.Session()
    offset = 0
    while True:
        params = {
            "where": f"STATE='{STATE_FIPS}' AND COUNTY='{COUNTY_FIPS}'",
            "outFields": ",".join(FIELDS),
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
        }
        payload = _get_page(session, params)
        features = payload["features"]
        if not features:
            return
        yield [_to_record(feature) for feature in features]

        if not payload.get("exceededTransferLimit") and len(features) < PAGE_SIZE:
            return
        offset += len(features)


def _get_page(session: requests.Session, params: dict) -> dict:
    """One TIGERweb page, with backoff and errors that name what went wrong.

    Three distinct failures hide behind HTTP 200 here, and none of them raises
    on its own:

      - the WAF returning an HTML rejection page
      - ArcGIS returning {"error": {...}} for a bad query
      - a valid response with no `features` key

    Each becomes a specific message rather than a JSONDecodeError pointing at
    character zero, which is what the first version of this did and which says
    nothing about the Census Bureau at all.
    """
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        response = session.get(TIGERWEB_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "html" in content_type:
            last_error = (
                "TIGERweb rejected the request at its WAF (HTTP 200 with an HTML body). "
                "This is rate limiting, not a bad query; it clears on its own."
            )
        else:
            try:
                payload = response.json()
            except ValueError:
                last_error = f"TIGERweb returned non-JSON: {response.text[:200]}"
            else:
                if "error" in payload:
                    # A bad query never becomes valid by retrying.
                    raise RuntimeError(f"TIGERweb rejected the query: {payload['error']}")
                if "features" not in payload:
                    raise RuntimeError(f"TIGERweb returned no features key: {payload}")
                return payload

        if attempt < MAX_RETRIES:
            wait = 15 * attempt
            print(f"  {last_error} Retrying in {wait}s ({attempt}/{MAX_RETRIES - 1}).")
            time.sleep(wait)

    raise RuntimeError(f"TIGERweb failed after {MAX_RETRIES} attempts. {last_error}")


def _to_record(feature: dict) -> dict:
    """One GeoJSON feature as a flat record with the geometry kept whole."""
    properties = feature.get("properties") or {}
    geoid = properties.get("GEOID")
    record = {key.lower(): value for key, value in properties.items()}
    record["the_geom"] = feature.get("geometry")
    # Socrata system fields, synthesised. ingest.py's watermark, dedup and
    # manifest logic all key off these, and a source that omitted them would
    # need a second code path through every one of them.
    record[":id"] = f"blockgroup-{geoid}"
    record[":updated_at"] = VINTAGE
    record[":created_at"] = VINTAGE
    return record


def fetch_acs_population(year: int = 2023) -> dict:
    """Block group population from the ACS 5-year API. Needs CENSUS_API_KEY.

    Not on the ingestion path and not called by anything today. It is here so
    that the ACS numbers are one function call away for whoever has a key and
    wants to compare the 2020 enumeration against a current estimate, which is
    the check that would tell us whether the substitution in ADR-7 still
    holds. Returns {geoid: population}.
    """
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY is not set. api.census.gov rejects unauthenticated "
            "requests; get a free key at https://api.census.gov/data/key_signup.html"
        )
    response = requests.get(
        f"https://api.census.gov/data/{year}/acs/acs5",
        params={
            "get": "B01003_001E",
            "for": "block group:*",
            "in": f"state:{STATE_FIPS} county:{COUNTY_FIPS}",
            "key": key,
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    rows = response.json()
    header = rows[0]
    index = {name: position for position, name in enumerate(header)}
    return {
        "".join(row[index[part]] for part in ("state", "county", "tract", "block group")): int(
            row[index["B01003_001E"]]
        )
        for row in rows[1:]
    }
