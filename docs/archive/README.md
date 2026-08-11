# Archive

Superseded ADRs and closed plans. Nothing here is live and nothing here was
deleted: git history is not a browsing interface, and a decision that was
replaced is still the record of what was believed at the time.

**Read an archived ADR when the question is why, not what.** The live rule is
always in `docs/decisions/`. If the two disagree, the live one wins and the
archived one is doing its job.

## ADRs

| Was | Replaced by |
|---|---|
| ADR-3 dataset scope: four datasets around a headline question about city spending | ADR-7, then ADR-10. Two supersessions ago. Its one live line, why the budget-to-311 crosswalk was never built, is in README.md under "What it does not do" |
| ADR-4 raw zone layout, and loading as a separate step | ADR-9 on where the files are and how BigQuery reads them, and ADR-18 on everything else. ADR-18 restates the layout, the all-STRING contract, the run manifests, the watermark and the append-only rule, so ADR-9's "read ADR-4 first" now means read ADR-18 |

**Five superseded ADRs are deliberately not here, for two different reasons.**

**ADR-2 and ADR-7 cannot move, and the constraint is code rather than
judgement.** `tools/context_pack/pack_prose.py` resolves an `evidence` citation
of `kind: adr` by checking that `adr-<n>-*.md` exists in `docs/decisions/`, and
`prose.yml` cites both: ADR-2 in `refuse.no-distance-or-routing` and ADR-7 in
`refuse.no-parcel-or-street-mile-rates`. Moving them makes `make context-pack`
fail with "no ADR 'adr-2' in docs/decisions/". **`make context-pack-check` does
not catch it**, because the check compares the target name, `prose_revision`,
`spec_version` and schema hashes and never resolves a citation, so the break
would surface only when someone next regenerated. Archiving either of them is a
code change to the resolver, and that is the decision to take before moving
them.

**ADR-14, ADR-16 and ADR-17 could move and are held back on judgement.** They
were superseded on 2026-08-10 in the same pass that wrote ADR-18, and a reader
checking a one-day-old consolidation against its sources should not have to
change directory to do it. Move them here when ADR-18 has been read a few times
without anyone reaching for them.

## References into here from live documents

ADR-1 is `active` and cites `docs/plans/plan-1-duckdb-parquet.md` twice. That
file is here now. The path was not corrected, because an accepted ADR's text is
immutable and a broken link is a smaller cost than the precedent of editing one.
**A `docs/plans/plan-<n>-*.md` path in any live document resolves to
`docs/archive/`.**

## Plans

All nine are `status: done`. A plan is intent, and intent that has been executed
is history.

| Was | Outcome |
|---|---|
| PLAN-1 duckdb-parquet | Parquet raw zone and DuckDB default. Closed 2026-08-01 under PLAN-4 step 11. One Done-when box left unticked on purpose; the plan says why |
| PLAN-2 ingestion-lint | Ruff exemptions gone 2026-07-31. Closed 2026-08-05 under PLAN-5 step 7, when `ingestion/datasets.py` became `dataset_registry.py` |
| PLAN-3 geography-and-marts | H3, boundaries, marts, published exports. Delivered 2026-07-31. Recorded in ADR-5, ADR-6, ADR-7, ADR-8 |
| PLAN-4 cloud-first-storage | Zones in GCS, BigQuery on external tables proven row for row against DuckDB, the CI cache step gone. Closed 2026-08-03 when `ingest.yml` went green on a runner. Recorded in ADR-9 |
| PLAN-5 narrow-and-polish | Two datasets and one H3 resolution cut, one registry, pytest on the geometry code. Closed 2026-08-05 by step 13, the obsolescence sweep. Recorded in ADR-10, ADR-11 and ADR-12 |
| PLAN-6 context-pack | The versioned context artifact with explicit refusal boundaries. Closed 2026-08-07 by step 4 and ADR-13, with one target of three; the other two became PLAN-8. Its spec, `docs/specs/context-pack.md`, is live and is not archived |
| PLAN-7 pipeline-assurance | Reconcile run manifests against the data; assert the BigQuery column sets against DuckDB's. PLAN-4 residue carried forward three times. Closed 2026-08-05, both steps the same day |
| PLAN-8 remaining-context-packs | The `published` and `bigquery` packs. Closed 2026-08-07 at two packs rather than three; ADR-15 struck the third |
| PLAN-9 raw-zone-retention | Bound what the two buckets accumulate. Closed 2026-08-07, all eight steps, recorded in ADR-14. Its two loose ends closed 2026-08-09 as ADR-16 and ADR-17, and all three are now restated in ADR-18 |

## What is not here

Dev notes. They are append-only and their archive is
`docs/dev-notes/ARCHIVE-2026-07.md`, which is a fold rather than a move: the
findings are preserved verbatim under a heading per date and the chronology is
dropped. The two most recent notes stay in `docs/dev-notes/` as loose files.
