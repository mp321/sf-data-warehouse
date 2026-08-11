---
status: done
date: 2026-07-30
related: [plan-1-duckdb-parquet, plan-5-narrow-and-polish]
---

# PLAN-2. Remove the ruff exemptions on the ingestion module

**Closed 2026-08-05.** Mostly closed by side effect on 2026-07-31: the
exemptions went because the underlying findings were fixed rather than
silenced. The last item was the `datasets.py` module-shadowing hazard that
`known-first-party` silenced without fixing, and it closed as PLAN-5 step 7:
the module is `ingestion/dataset_registry.py` now, and the five files that
imported it import that. Its contents moved as well, in the same change, but
that was PLAN-5 step 4's business rather than this plan's.

Two things this plan got wrong are worth keeping, since both were about
scope rather than about the fix:

- The open question below asks "rename or package?" and answers that the
  rename touches the import in two files. It was five by the time it was done,
  because `ingestion/spatial.py` had been split. The count was never the
  argument, but a plan that estimates a change should be read as an estimate.
- The rename fixes one name. It does not fix the class, because
  `ingestion/` is still a directory of scripts on `sys.path` and every module
  in it is still a name a PyPI package could claim. That is deliberate:
  `ruff.toml` records which names were judged reachable and which were not,
  and `tests/conftest.py` records why packaging the directory is a bigger
  decision than a lint config. Neither is a loose end left by this plan.

## Goal

`ruff.toml` has no `[lint.per-file-ignores]` entries for `ingestion/` and no
`[format] exclude`, and `make lint` passes.

## Why now

Linting was introduced in the same session that was explicitly scoped to not
refactor ingestion. Three real findings in `ingestion/` were therefore
exempted rather than fixed, so that CI could be green on day one. Exemptions
added for a good reason still rot if nobody writes down when to remove them.
This is that note.

## Constraints

- `ingest.py`'s incremental watermark is subtle and easy to break silently.
  See the findings section of `docs/dev-notes/2026-07-30.md`, particularly
  the string-comparison watermark.
- Any change here must be verifiable against a real BigQuery run before it
  ships, because the failure mode of getting it wrong is a silent full
  backfill rather than an error.

## Steps

1. Fix `I001` and the sibling-import fragility together. `from datasets
   import DATASETS` resolves only because Python puts the script's directory
   on `sys.path`, which means it will shadow or be shadowed by the
   HuggingFace `datasets` package the moment anything pulls that in. Make
   `ingestion/` a real package with `__init__.py` and use an explicit
   relative import, or rename the module to something unambiguous such as
   `dataset_registry.py`. The rename is smaller and probably better.
2. Fix `PLR0917` on `ingest_one`, which takes 7 positional arguments. A small
   frozen dataclass carrying project, raw_dataset, app_token and the run
   flags is the obvious shape.
3. Rewrap the over-long description in `ingestion/datasets.py`. Do this after
   ADR-3's scope changes have settled, not before, or it will just move.
4. Run `ruff format ingestion/` and accept the diff in its own commit, with no
   logic changes mixed in, so the reformat is reviewable.
5. Delete the exemption blocks from `ruff.toml`, including the comments
   pointing at this plan.

## Out of scope

- The Parquet rewrite of `ingest.py`. That is PLAN-1 and it will touch the
  same file. Sequence this plan after it, or the reformat commit will conflict
  with the rewrite.
- `export_parquet.py`, which already lints clean and is scheduled for deletion
  by PLAN-1 anyway.

## Done when

- [x] `ruff.toml` contains no `ingestion/` exemptions. Done 2026-07-31.
- [x] `make lint` passes. Done 2026-07-31, and again on 2026-08-05 after the
      rename.
- [x] A real ingestion run against BigQuery produces the same row counts as
      before the change. Satisfied on 2026-08-03 by the `ingest.yml` run that
      closed PLAN-4, before the rename, which is the right way round: this box
      was written to protect the watermark logic in step 2, and neither the
      rename nor the registry move touched a line of it. The rename is
      import-only, and `make ci-build` reloads from fixtures and rebuilds from
      the zone twice with identical counts.

## Open questions

- Rename the module or make `ingestion/` a package? Renaming is a smaller
  change but touches the import in two files; packaging is more conventional
  but changes how the scripts are invoked, which means updating
  `.github/workflows/ingest.yml` and the Makefile.
