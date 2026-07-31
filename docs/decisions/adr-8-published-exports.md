---
status: active
date: 2026-07-31
related: [adr-1-warehouse-targets, adr-4-raw-zone-layout]
---

# ADR-8. Published exports: local-first Parquet with a manifest

## Context

The marts are only useful to something that can read them, and today the only
way to read them is to have the DuckDB file, which means having the raw zone
and having run the pipeline. That is fine for developing and useless for
anything else: a notebook, a map, a language model answering questions about
the city, or the context pack in PLAN-2 all need an artifact they can fetch.

**The forcing constraint is that this must not become a fifth thing that only
works if a bucket exists.** ADR-1 made builds credential-free, ADR-4 made the
raw zone local, and ADR-5 kept the H3 precompute offline. A publish step that
requires cloud storage to run at all would be the first part of this pipeline
that a fresh clone cannot exercise, and it would be the part most likely to
rot unnoticed, because nobody would run it locally.

There is a related but separate gap: PLAN-1 step 5, off-machine durability for
the raw zone, is still open. This decision is not that one. Published exports
are derived, regenerable and disposable; the raw zone is the record.

## Options considered

**A. Publish straight to a bucket, no local path.** Simplest to describe.
Against: unrunnable without credentials, so it is never exercised in CI or on
a fresh clone, and the first time anyone runs it will be the first time it has
ever run. It also makes a failed upload leave the destination holding a
partial export with no local copy to retry from.

**B. Commit the exports to git.** Zero infrastructure, and the artifact
travels with the repo. Against: `mart_activity_by_h3` is 264,802 rows and 12
MB of Parquet, regenerated wholesale on every build, which is exactly the
shape of thing that should never be in git history. The existing `.gitignore`
already forbids `*.parquet` for this reason.

**C. Local-first Parquet with an optional remote destination.** Against: two
code paths, and the remote one is the one that will be under-tested. A
manifest that has to be kept in step with the files beside it, which is the
same second-source-of-truth problem ADR-4 accepted for run manifests.

## Decision

Option C. `publish/export.py`, run by `make publish`.

- **Local is the default and is never a fallback.** Files are written to
  `published/` first, then uploaded if `--destination` was passed. A failed
  upload leaves a complete local export rather than a half-filled bucket.
- **Marts only.** Staging models are views over raw, and publishing them would
  ship the same rows twice under two names.
- **Partitioned by month where a month exists**, hive-style, so a consumer
  reading one month scans one directory. The partition list is declared per
  mart in `PUBLISHED_MARTS` rather than inferred from the schema: a mart could
  gain a date column that is not the one anyone filters by, and silently
  repartitioning breaks every consumer's paths.
- **A manifest at `published/manifest.json`** recording, per dataset: path,
  row count, byte size, schema hash, partition key, and generated_at, plus a
  manifest version and a licence statement.
- **The schema hash covers column names, types and order, and nothing else.**
  Data churns daily and schema should not, so a consumer can detect the change
  that would break its casts without diffing rows.
- **The manifest uploads last.** Until it lands, a consumer reading the bucket
  sees the previous manifest and the previous data: stale but coherent. A
  manifest describing files that have not arrived is worse than one that
  arrives late.
- **R2 and GCS are both supported**, chosen by URI scheme. `boto3` is not a
  project dependency and is imported only on the R2 path, with an error that
  says so, because the local export does not need it.
- `published/` is gitignored.
- The export runs in CI on every pull request, against the fixture warehouse.

## Consequences

**Buys.** The marts become a thing that can be handed to something else. The
export is exercised on every PR rather than only when someone remembers, which
is the property option A cannot have. Schema drift is detectable by a consumer
without reading data, and the manifest is the natural anchor for PLAN-2's
context pack, which needs exactly these fields. And because it is local-first,
`make publish` is a one-second smoke test that every mart is serialisable.

**Costs.** The manifest is a second source of truth about the same files and
can disagree with them if one is edited, which is the tradeoff ADR-4 already
accepted for run manifests. The remote path is genuinely under-tested: CI
exercises the local export only, so an R2 or GCS regression would surface at
the worst moment. Publishing is a full rewrite each run, so it grows with the
warehouse rather than with the change, and each mart directory is deleted and
rewritten rather than merged, which means a publish interrupted mid-mart
leaves that mart missing rather than stale. And `published/` being gitignored
means the artifact is not reviewable in a pull request; only the code that
produces it is.

**Lock-in.** Consumers will key off the paths, so the directory layout and the
partition column of any published mart become a public interface: renaming
`event_month` is now a breaking change to something outside this repo.
`manifest_version` exists so that can be done deliberately. Choosing month
partitioning means a consumer filtering by neighborhood scans everything, and
repartitioning later means every consumer's paths change.

## Revisit if

- A consumer actually appears and wants something other than month
  partitioning, which is the point at which the partition key is worth
  arguing about with a real access pattern rather than a guessed one.
- The export outgrows a free-tier bucket, currently about 17 MB against R2's
  10 GB, which would take roughly a 500-fold increase and is not a near-term
  concern.
- The raw zone durability question (PLAN-1 step 5) gets its own ADR and lands
  on a storage provider. Sharing one bucket between the record and the derived
  exports would be a reasonable simplification, and this decision should not
  pre-empt it.
