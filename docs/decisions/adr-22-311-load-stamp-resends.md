---
status: draft
date: 2026-11-13
related: [adr-18-the-raw-zone, adr-19-withdrawn-registrations, adr-21-withdrawn-keys-are-ledgered-then-prunable]
---

# ADR-22. The raw zone keeps a 311 version only when the case changed

> **In plain terms.** Every day the city re-sends a large block of 311 cases
> whose only change is two load stamps, `data_as_of` and `data_loaded_at`.
> Each re-send lands as a new row, and five in six rows in the zone are these.
> This ADR proposes not storing them. **Draft: the owner has not accepted it,
> and no code implements it.**

Would amend ADR-18 (the append-only rule and where the watermark comes from).
It would be the third amendment of the four `docs/README.md` allows.

> **Update 2026-11-16, before any decision.** The "three times the raw zone"
> multiplier below was gcsfs padding each read to 5 MiB, and it is fixed
> (`remote.exact_range`, dev note 2026-11-16): about 0.19 times the zone on a
> local copy. That is most of option C. Egress is no longer the forcing
> constraint; storage is, at about 110 days of daily runs. The egress figures
> under Context and Buys predate the fix.

## Context

**The forcing constraint is GCS internet egress, 100 GB a month free.** A
daily run reads about three times the raw zone over the network (dev note
2026-10-01), so egress follows zone size. October reached about 76 GB by the
22nd and all three scheduled workflows were disabled on 2026-10-23. Storage
is the second limit: 5 GB free, about 110 days away at the October growth
rate.

Measured 2026-11-13 on a copy of `raw_311_cases` as it stood at the pause:

| | |
|---|---|
| Size, of a 1026 MB raw zone | 907 MB |
| Partitions | 63, 2026-07-31 to 2026-10-23 |
| Rows | 5,715,979 |
| Distinct cases | 766,014 |
| Rows that are not the newest version of their case | 4,949,965 (86.6%) |
| Rows differing from the previous version only in `data_as_of` and `data_loaded_at` | 4,757,202 (83.2%) |

| Rewritten as one Parquet file | Size |
|---|---|
| Every row | 877 MB |
| Without stamp-only re-sends | 149 MB |
| Newest version per case only | 135 MB |

- **The re-sends are not new.** They were 75.6% of rows before 2026-10-04 and
  about 86% after. What changed on 10-04 was the volume, from about 31k rows a
  run to 121k and more.
- **They are a rolling daily re-stamp.** `data_as_of` takes one value a day,
  and most cases in the zone have seven or eight versions.
- **Real change is a small share.** From 10-16 to 10-23, 975,962 rows arrived
  and `updated_datetime` moved on 29,070 of them.
- **Nothing reads either stamp.** No model, macro, `prose.yml` entry or
  ingestion module names `data_as_of` or `data_loaded_at`.

Not measured: bytes read per step. By code reading, the whole-table passes
are `read_watermark` in `ingest.py`, then `raw_input_state` and `read_points`
in `spatial.py`, both of which deduplicate over the entire table.
`building_permits` (15 MB) was not examined.

## Options considered

**A. Do not store stamp-only re-sends.** Skip them at ingest and remove the
existing ones once. Against: raw stops being everything the API sent, and a
skipped row cannot be fetched again as it was.

**B. Compact to the newest version per case, by hand.** The prune's proof
applied per row. Against: growth returns at about 36 MB a day between
applies, so it needs a person every week or two. ADR-17 recorded what happens
to a by-hand step nobody is watching, and this repo is paused. It also
discards the real change history, to save 14 MB over option A.

**C. Read less on each run.** Take the watermark from Parquet footer
statistics or the run manifest, and stop deduplicating the whole table to
decide what moved. Against: it lowers the multiplier and leaves growth
unbounded, so storage still passes 5 GB. Worth doing regardless; not a bound.

**D. Fetch 311 weekly.** A case re-stamped daily would arrive once a week,
not seven times, for a one-line cron change. Against: still unbounded, and it
breaks the 48-hour freshness target. This belongs to the cadence ADR the
2026-09-30 note asked for.

**E. Accept the growth and pay.** The 2026-10-23 forecast for November was
over 200 GB, about $12 a month and rising. Against: the project is sized for
free tiers.

## Decision

Proposed, not accepted.

We keep a version of a 311 case in the raw zone only when it differs from the
stored newest version in a column other than the two load stamps.

- The ignored columns are declared per dataset in the registry
  (`vars.pipeline_sources`), not in code. Only `311_cases` declares any.
- `ingest.py` compares each fetched row with the stored newest version of its
  case and does not write a match. The run manifest counts what it skipped,
  so fetched equals written plus skipped and `check-runs` still reconciles.
- The versions already in the zone are removed once, by hand, with a report
  mode first. A row goes only when a surviving row of the same case is equal
  on every other column.
- Acceptance test: `make rebuild` before and after the removal returns the
  same `stg_datasf__311_cases` rows on every column except
  `socrata_updated_at` and `ingested_at`.

## Consequences

**Buys.**

- 311 at about 149 MB, not 907 MB. Growth follows real changes: about 16k
  rows a day in late October, not 121k.
- No recurring human step.
- At three reads of the zone per run, a raw zone near 270 MB costs about
  24 GB of egress a month on a daily schedule. This is an estimate, and the
  comparison adds a read.

**Costs.**

- The raw zone is no longer a full record of what the API returned. The
  manifest keeps the count of skipped rows and nothing else.
- `socrata_updated_at` in staging, and the dbt source freshness field built on
  it, go stale for a case whose latest touch was stamp-only. Freshness has to
  read `_ingested_at` or the run manifest.
- The newest `:updated_at` in the zone stops being the last row fetched. The
  watermark either moves to the run manifest, which changes ADR-18's rule
  that the position comes from the zone alone, or each run re-fetches the
  stamp-only tail from DataSF.
- The one-off removal rewrites files in a delta table. That is a fourth
  exception to append-only and the first deletion from a delta dataset.
  `check-runs` reconciles manifests against rows by run id, so the removal
  has to amend the manifest of every run it thins.
- If the city starts carrying meaning in either stamp, it is lost from then
  on.

**Lock-in.** The ignore list defines what "changed" means for a dataset, and
versions removed under it are gone. One amendment of ADR-18 remains before it
must be consolidated.

## Revisit if

- A model or a context pack entry needs `data_as_of` or `data_loaded_at`.
- Stamp-only rows fall under 10% of what a month of runs fetches, so the
  comparison no longer pays for its read.
- The raw zone passes 1 GB again.
