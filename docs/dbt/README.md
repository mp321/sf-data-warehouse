# Committed dbt artifacts

`manifest.json` and `catalog.json` as of the last `make docs`. They are here
so the project graph, model descriptions, column types and test coverage are
readable from a fresh clone without installing anything or building the
warehouse.

Refresh them with:

```
make docs
```

`make docs-serve` additionally opens the browsable docs site.

## What is and is not here

- `manifest.json` is the compiled project: every model, source, test,
  description and dependency edge.
- `catalog.json` is the warehouse-side view: the columns and types that
  actually exist, gathered by querying the built warehouse.
- `index.html` is deliberately absent. dbt's viewer is a 1.7 MB single-file
  JavaScript bundle that is rewritten wholesale on every dbt upgrade, so
  committing it would dominate the repository's history while telling a
  reader nothing the two JSON files do not. `make docs-serve` builds it on
  demand.

## Caveats

They are a snapshot, not a build output: nothing regenerates them
automatically, so they are as current as whoever last ran `make docs`. Treat a
disagreement with the models as the artifacts being stale.

`catalog.json` reflects whichever target generated it, which is DuckDB by
default. Type names in it are DuckDB's, so `double` here is `float64` on
BigQuery. The mapping is in `dbt/macros/cross_engine.sql`.
