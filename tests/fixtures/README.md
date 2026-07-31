# Socrata fixtures

`socrata/<dataset>.json` holds real DataSF records, saved as the API returned
them. `ingestion/ingest.py --fixtures tests/fixtures/socrata` reads these
instead of calling the API, which is how CI runs the entire pipeline with no
network and no credentials:

```
make ci-build     # ingest-fixtures -> load -> dbt build
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

## Two invariants, both learned the hard way

**Every column the staging models reference must appear somewhere in the
file.** Socrata omits null fields per record rather than sending nulls, so a
column present in 2% of rows is simply absent from a small sample, and the
Parquet file then has no such column at all. The model fails with "Referenced
column not found". That failure is correct in production, where a column
disappearing upstream should stop the build rather than quietly become NULL,
but it makes a naively sampled fixture useless. `make_fixtures.py` therefore
appends one synthetic coverage record carrying every field seen in a 400-row
scan, with values borrowed from the rows that had them.

**The fixtures must pass the tests.** Adversarial values go only on columns
with no `not_null` test. A fixture that breaks a test to make a point stops CI
being able to prove anything else.

## What is deliberately nasty in here

- A 311 case ingested twice, opened then closed, so deduplication has to keep
  the later version.
- Unparseable coordinates and an unparseable permit cost, so `x_safe_cast` has
  to null them rather than error.
- A permit with no `location`, so the JSON coordinate extraction has to cope
  with a missing document.
- A negative budget amount, which is legitimate and must survive.
- A film with no release year and one with no coordinates, both of which exist
  upstream.
