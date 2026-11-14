# Tour page

`docs/tour/index.html` walks a reader through the warehouse: the count versus
rate map, one 311 case traced through every layer, the model graph, the main
decisions and the checks. It is one self-contained file, generated from the dbt
manifest and the local warehouse, and committed so a change shows up in review.

## Build

| Step | Command |
|---|---|
| Refresh the warehouse and manifest | `make build` |
| Rebuild the page | `.venv/bin/python -I tools/tour/build.py` |
| Also write a body-only copy, for hosts that add their own `<html>` wrapper | add `--artifact <path>` |

Reads `data/sf.duckdb` read-only. No network, no credentials.

## Where to change things

| To change | Edit |
|---|---|
| Copy, layout, styles | `page.html` |
| The traced 311 case | `--case <id>`, default `CASE` in `build.py` |
| The map's month window | `month_window()`: drops the newest month as partial, starts at the first month above half the peak |
| The map's categories | the `limit 8` query in `neighborhoods()` |
| Hero numbers not read from data (datasets, Python tests, engines) | `page.html`, the `facts` list |
| Decision cards and ADR links | `ADR` and the decisions block in `page.html` |
