---
status: superseded
date: 2026-08-09
related: [adr-4-raw-zone-layout, adr-9-cloud-raw-zone, adr-10-narrowed-scope, adr-14-raw-zone-retention, adr-17-scheduled-retention-proof, adr-18-the-raw-zone, plan-9-raw-zone-retention]
---

# ADR-16. A dataset cut from the registry is deleted from the zone, and the prune is not the tool

Amends ADR-4's append-only rule with a third exception, in the sense
`docs/README.md` gives that word, and it is a different exception from ADR-14's
rather than a widening of it. ADR-4 says files are added to the raw zone and
never edited or deleted; `ingest.py --full-refresh` is the first exception and
`prune_raw.py` is the second. This is the third and it is smaller than either:
one act, by hand, on a prefix the registry no longer names. **Nothing edits a
file in the zone**, which is the part of ADR-4's rule that still has no
exceptions at all.

## Context

ADR-10 cut `city_budget` and `street_trees` on 2026-08-04. It removed them from
`vars.pipeline_sources`, from the dbt sources, from the staging models and from
the fixtures. It did not remove their bytes, because on 2026-08-04 the zone was
still local and the bytes cost nothing.

They cost something now. Measured on the real bucket on 2026-08-09:
`raw_city_budget` was 30,146,019 bytes over 27 objects and `raw_street_trees`
was 25,318,997 over 15, together 55,465,016 bytes and 42 objects. That was 17.2
percent of a 323.4 MB raw prefix, against a 5 GB always-free allowance, and it
was rows that no query in this project can reach and no model has been able to
reference since ADR-10 cut them five days earlier. PLAN-9 measured them at 25.9
percent of the pruned prefix on 2026-08-07 and left them alone, naming this as
the one thing it did not do.

**Two things already knew.** `check_runs.py` warned on every run: "raw_city_budget
is in the zone and not in the registry, so nothing reads it. Reconciled anyway."
And `prune_raw.py` refused to be pointed at them, with `unknown dataset(s):
city_budget, street_trees`. The zone had been telling anyone who ran either
tool, for five days.

## Options considered

**A. Extend `prune_raw.py` to handle a dataset that left the registry.** The
obvious answer, since it is the tool that deletes from this zone, and it is why
this ADR exists rather than a line in a dev note. Rejected, and the reason is
not effort: **the proof does not exist to extend.** `prune_raw.py` proves that a
surviving later partition holds every `grain_key` of the candidate at values no
older. A dataset leaving the project has no surviving partition, so the proof is
not merely unavailable, it is false by construction: every key is unreachable
afterwards, which is the exact condition ADR-14 makes the tool refuse on. The
tool would have to be taught that its central check does not apply here, and a
destructive tool with a flag that disables its proof is a worse tool than one
without the flag.

It also has no inputs. The registry is where `refresh` and `grain_key` come
from, and a cut dataset is not in the registry. `selected()` exits on an unknown
name at `ingestion/prune_raw.py:256` for that reason, and that refusal is
correct and should stay.

**B. A bucket lifecycle rule scoped to the two prefixes.** Rejected on ADR-14's
grounds and not re-argued here. It is also the wrong shape for a one-time act:
a rule persists and this does not.

**C. Leave them.** 55.5 MB is not the binding constraint and deleting nothing is
always safe. Rejected because the cost is not only bytes. A prefix in the zone
that nothing reads is a standing question for every future reader, and
`check_runs.py` answers it with a warning on every run, which is how a warning
becomes furniture. ADR-10 decided these datasets are out of the project; a zone
that still holds them makes that decision half-true.

**D. Delete the two prefixes by hand, and record that this is a different act
from a prune.** Chosen.

## Decision

`gcloud storage rm --recursive` on the two prefixes, once, by hand, executed
2026-08-09. **This is a scope deletion and not a prune, and the difference is
the proof.**

| | prune (ADR-14) | scope deletion (this) |
|---|---|---|
| what goes | one partition of a live dataset | every partition of a dataset the project cut |
| the proof | a surviving partition holds every `grain_key` at values no older | nothing reads it: absent from the registry, so absent from dbt |
| where the rows survive | elsewhere in the zone | nowhere in the zone; upstream at DataSF |
| who checks | `prune_raw.py`, per candidate, per run | `test_dataset_registry.py`, on every PR |
| how often | whenever the zone grows | once per ADR that cuts a dataset |

**The proof is a repo proof rather than a data proof, and it is stronger than
the prune's rather than weaker.** The registry has one copy,
`vars.pipeline_sources` in `dbt/dbt_project.yml`, and
`tests/test_dataset_registry.py` asserts in both directions that it agrees with
the dbt sources, the staging models and the fixtures. So a dataset absent from
the registry is absent from all four, and dbt cannot read a table that is not a
declared source. Neither `raw_city_budget` nor `raw_street_trees` is one.
`load.py` never looked at them either: `names = list(DATASETS)` is the only
thing that decides what it loads. The prune's proof has to be re-established for
every candidate partition because the zone changes under it; this one is a
property of the repository that CI checks on every pull request.

**What is genuinely lost, stated rather than argued away.** These bytes were the
only copy in this project, and unlike a superseded snapshot partition there is
no later one holding the same rows. What makes that acceptable is not that the
rows are duplicated but that they are **re-fetchable**: both datasets are still
published at data.sfgov.org, and the registry entries that fetched them are in
git history at ADR-10's commit. That is the opposite of a delta partition, whose
rows an API serving current state cannot return, which is why ADR-14 refuses
those absolutely and this ADR does not need to.

**The one warehouse-visible effect, and it is not a model.** `raw_ingest_runs`
is built by globbing `*/_runs/*.json` across the whole zone rather than from the
registry, so it loses the 22 manifests of the two datasets: 136 to 114.
`mart_pipeline_freshness` reads that table but its grain is one row per
*registered* source, so its rows were never among them. No model's row count
moves, and the reason is structural rather than measured: no model can reference
a table that is not a declared source.

**By hand and with no tool written.** A tool would be built for a one-time act
whose next occurrence is the next ADR that cuts a dataset, which is once a
quarter at most and may be never. The check that this act is needed already
exists and is `check_runs.py`'s warning; what did not exist was anyone acting on
it. Writing a `delete_cut_datasets.py` would be the second thing in this zone
that deletes, to save one command a year.

## Consequences

**Buys.** 55,465,016 bytes and 42 objects, 17.2 percent of the raw prefix, and
the raw prefix goes 323,379,850 bytes to 267,914,834. `check_runs.py` runs clean
with no warnings for the first time since 2026-08-04, which restores it to a
check whose silence means something. ADR-10's scope cut is now true of the zone
and not only of the code.

**Costs.** ADR-4's append-only rule now has three exceptions rather than two,
and the sentence a new reader has to hold is correspondingly longer. The two
datasets would have to be re-fetched from DataSF to be studied again, and the
`_socrata_updated_at` values in those 42 objects were a record of when the city
had republished them, which is not re-fetchable at all: an API serving current
state cannot say what it served in July. Nobody was reading it and nothing
depended on it, which is why this is a cost and not a blocker.

**Lock-in.** Cutting a dataset now has a zone step, and an ADR that cuts one
without saying what happens to its prefix leaves the same residue ADR-10 left
for five days. The `check_runs.py` warning is what catches it, so that warning
must not be softened into silence for datasets the registry does not name; its
whole value is that it fires.

## Revisit if

- A dataset is cut and then wanted back inside the window where re-fetching
  would lose history. That argues for a hold period before the delete rather
  than against the delete, and the answer is to move the bytes out of the zone
  and not to leave them in it.
- More than one dataset is cut at once, or a cut becomes routine. The
  by-hand-and-no-tool judgement above is priced on this happening rarely.
- `check_runs.py` ever stops warning about a prefix the registry does not name.
  That is the only thing standing between this residue and nobody noticing it,
  and it is load bearing for that reason rather than a nicety.
