# Socrata fixtures

`socrata/<dataset>.json` holds real DataSF records, saved as the API returned
them. `ingestion/ingest.py --fixtures tests/fixtures/socrata` reads these
instead of calling the API, which is how CI runs the entire pipeline with no
network and no credentials:

```
make ci-build     # ingest-fixtures -> spatial -> load -> dbt build -> publish
```

Everything downstream of the fetch is the production code path. Only the
transport changes, so a fixture run exercises the real normalisation, the real
Parquet writer, the real loader and the real models.

Regenerate with `python tests/fixtures/make_fixtures.py`, which needs network.

## Why these are JSON and not Parquet

The fixtures are API responses, not raw-zone files. Committing Parquet would
skip the ingestion half of the pipeline, hide the writer from CI, put binaries
in git, and need an exception to the `*.parquet` ignore rule. Starting from
JSON means CI builds the Parquet zone the same way production does, and a
change to the fixtures is reviewable in a diff.

## Three rules these files have to satisfy

**Every column the staging models reference must appear somewhere in the
file.** Socrata omits null fields per record rather than sending nulls, so a
column present in 2% of rows is simply absent from a small sample, and the
Parquet file then has no such column at all. The model fails with "Referenced
column not found". That failure is correct in production, where a column
disappearing upstream should stop the build rather than quietly become NULL,
but it makes a naively sampled fixture useless. `make_fixtures.py` therefore
appends one synthetic coverage record carrying every field the dataset
publishes, with values borrowed from real rows.

That record is built from the dataset's metadata rather than from the scan,
which matters because the scan is ordered by `:updated_at`: a column populated
only on recently-touched rows is invisible in the oldest 400. So the generator
reads the column list from `/api/views/<id>.json` and fetches a real value for
anything the scan missed.

**The fixtures must pass the tests.** Adversarial values go only on columns
with no `not_null` test. A fixture that breaks a test to make a point stops CI
being able to prove anything else.

**Boundary fixtures are real polygons, not placeholders.** The spatial step is
the largest piece of machinery in the project, and a fixture run that stubbed
it would leave the H3 bridge, the exact refinement and the population
interpolation untested. So all 41 neighborhoods, all 11 supervisor districts
and all 681 block groups are here, complete, with their vertices thinned to
every fourth and rounded to five decimal places. That takes them from about
3 MB to a few hundred KB.

Thinning is lossy: it moves a boundary by tens of metres in places, so the
cell counts a fixture run produces are not the cell counts real data produces.
That is the intended split. CI proves the machinery runs and the tests hold;
the numbers quoted in ADR-6 come from `make spatial` on the real zone.

## The awkward cases these files carry on purpose

- A 311 case ingested twice, opened then closed, so deduplication has to keep
  the later version.
- Unparseable coordinates and an unparseable permit cost, so `x_safe_cast` has
  to null them rather than error.
- A permit with no `location`, so the JSON coordinate extraction has to cope
  with a missing document.
- A film with no release year and one with no coordinates, both of which exist
  upstream.
- A business located in Georgia, which is correct data and must come out
  `out_of_bounds` rather than being dropped or accepted as a San Francisco
  address.
- A business whose coordinates are State Plane feet in a degree column, which
  must come out `impossible`. This is what the Earth-bounds `accepted_range`
  test on latitude and longitude exists to catch.

Both coordinate shapes are represented, and the awkward cases are split across
them on purpose: the unparseable pair sits on `311_cases`, which carries flat
`latitude` and `longitude`, and the out-of-bounds and impossible pair on
`business_locations`, which carries a `geojson_point`.
