---
status: active
date: 2026-08-07
related: [plan-6-context-pack, adr-13-context-pack-format, adr-15-bigquery-pack-declared-not-generated, adr-1-warehouse-targets, adr-6-polygon-membership, adr-8-published-exports, adr-10-narrowed-scope, adr-12-published-export-layout]
---

# Spec. The context pack

A versioned, model-agnostic description of this warehouse that lets a capable
LLM query it correctly and tells it what it must refuse to answer. This is the
contract; `tools/context_pack/` is verified against it and not the other way
round. Mutable, unlike an ADR: amend it when the artifact must change and say in
the dev note why. **`date` above is the pack's `spec_version`**, read from this
frontmatter by `generate.py`, so moving it makes every committed pack stale
until regenerated.

**Cut 2026-08-10, 737 lines to this.** Every enforced rule is kept; what went
was the prose arguing for them and a second copy of the refusal text, which
lives in `prose.yml` and had drifted from the copy here. No rule changed, so
`date` did not move. Arguments live in ADR-13, ADR-15 or an entry's own `why`.

## 1. What the pack is, and is not

**Is.** One file a consumer puts in front of a model, after which it writes
correct SQL here, knows what the numbers mean and how stale they are, and knows
which questions to decline. **The refusal boundaries are why it exists.** **Is
not a consumer**, and nothing here may be justified by "the consumer will need
it". **Is not a substitute for the repo**: the pack states what is true and
cites where the reasoning lives.

## 2. Targets

**A pack describes exactly one target and names it in its identity block.** A
pack that hedges is wrong about whichever surface the reader is holding.

| Target | Models | Freshness from | Examples verified against | Creds | Generated | Checked in CI |
|---|---|---|---|---|---|---|
| `duckdb` | all 19 | last `make build` | the DuckDB file | none | by hand, after `make build` | yes |
| `published` | the 6 marts in `PUBLISHED_MARTS` | `published/manifest.json` | DuckDB over `published/*.parquet` | none | by hand, after `make publish` | yes |
| `bigquery` | all 19 | last `make build-bigquery` | BigQuery | yes | not generated (ADR-15) | no, no schema hash |

- **Generation is by hand and the check is CI's** (section 8).
- **The published pack is not a shortened warehouse pack.** No staging models,
  so the bridge's three-flag trap must not be rendered into it; several joins do
  not exist there; questions answerable in the warehouse are refusals there.
- **`bigquery` stays declared in `pack_target.py`** with its model set,
  freshness source and schema-hash policy, so `applies_to: [bigquery]` stays
  meaningful; `open_target` raises for it. **`applies_to` is only checked
  against targets that get generated.**

**The schema hash** is `publish/export.py`'s, unmodified: column names, types
and ordinal position in DuckDB type names. It carries across `duckdb` and
`published`, not into `bigquery`, where `VARCHAR` is `STRING` and every hash
would differ for an identical schema; that pack states the hash is deliberately
absent and names `make parity-columns` in its place.

## 3. Artifact shape

`context-pack/context_pack.<target>.json` and `.md`. **The JSON is the artifact.
The markdown carries strictly less: nothing may appear in it that is not in the
JSON.**

Top-level keys: `spec_version`, `pack_version`, `target`, `generated_at`,
`prose_revision`, `identity`, `build`, `models`, `joins`, `freshness`, `traps`,
`refusals`, `disclosures`, `examples`, `integrity`. `target` is never absent and
never plural. `prose_revision` hashes `prose.yml`, identically across every pack
in one generation.

## 4. Content blocks

**4.1 identity.** `name`, `description`, `publisher`, `jurisdiction`, `licence`,
`source_urls`, `update_cadence` per source, `repo`. Derived, not written twice.
**4.2 build.** dbt manifest `invocation_id` and timestamp, dbt and adapter
versions, the registry from `vars.pipeline_sources`, the H3 resolutions, and per
target the warehouse path, the export's `manifest_version` and `generated_at`,
or the BigQuery project and dataset.

**4.3 models.** One entry per model the target holds, in dependency order:
`name`, `layer`, `materialisation`, `grain`, `row_count`, `columns` with name,
type, yml description and a profile.

**A model with no grain sentence fails generation.** `grain` is the first "one
row per ..." sentence in the description, survives folded YAML newlines, and
does not stop at an abbreviation. Profiles are rules, not a schema, and H3 cells
are tested before numbers because a cell id is a BIGINT and examples of one are
noise:

| Column shape | Profile carries |
|---|---|
| any | null rate |
| low cardinality (at or under 50 distinct) | every distinct value with its share |
| high cardinality string | distinct count, 5 example values |
| numeric | min, max, median |
| date or timestamp | min, max, count in the newest complete month |
| boolean | true share |
| H3 cell BIGINT | distinct count only |

**The newest complete month comes from the data, not the clock**: the month
before the month the maximum falls in, or the pack moves at midnight on the
first with no data having moved. An empty model is said once, not profiled.
Where a description does not say what a null means and the null rate is above
zero, the generator warns naming the column: a to-do for the yml.

**4.4 joins.** `from`, `to`, `on`, `cardinality`, `safe` with a reason where it
is not. The bridge's three flags are explained here, for targets that have one,
and dirty joins are named as such. **A prose join restating one dbt's
`relationships` tests already declare fails the build**: `prose.yml` carries only
the joins no test declares.

**4.5 freshness.** Per source: `last_load_at`, `last_run_finished_at`,
`stale_after_hours`, `is_stale`, `row_count`, tier. `mart_pipeline_freshness`
projected, not a second calculation. For `published` it is the publish time with
the build time beside it and the pack says which; **a published pack with no
publish time fails rather than reporting the build time**. An export with no
manifest is not an export; an export missing a mart is refused, not described.

**4.6 traps.** Unconditional disclosures: section 6's object without the `when`,
in their own block so the refusal list stays short enough to be read.
**4.7 examples.** A question paired with executed SQL, plus `demonstrates` and a
`verified` block of target, timestamp, rows and `sql_sha256`. **An example is
verified against the target whose pack it appears in and nowhere else.**
**`sql_sha256` covers the SQL as executed**, so editing without re-executing
fails the build, which is the only thing that stops an example rotting quietly.
**Every class 3 refusal has at least one example demonstrating its substitute**
and generation fails without one; an `instead.example` pointing at an example
this pack lacks is rewritten to one it carries, or dropped, and a pointer it
carries is left alone. An example query that errors fails the build.

## 5. Refusal boundaries

**The entries live in `prose.yml` and nowhere else** (section 7). This document
fixes their shape.

| Class | The situation | What is refused |
|---|---|---|
| `absent` | the data is not here | the question |
| `mismeasured` | a query returns a number and it does not mean what the question assumes | the question |
| `misnormalised` | the measure is right and the arithmetic invites a wrong conclusion | the form of the answer |

An answerable question carrying a bounded, measured error is not a refusal; it
is section 6.

**5.2 The object.** Required and enforced: `id`, `class`, `applies_to`,
`question_shapes`, `rule`, `why`, `evidence`, `instead`, and ids are unique
across every block.

- **`question_shapes` are literal paraphrases, several of them.**
- **`rule` is an imperative addressed to the answering model**, not prose about
  the data. **`instead` is mandatory.**
- **`evidence` must resolve against the target being generated**, and an entry
  claiming a target where it does not **fails generation rather than warning**.
  Citations name a model, column, ADR, registry value or dated measurement, and
  a registry citation fails when the value it asserts has moved. An entry not
  claiming the target is simply not rendered. Evidence is measured or it says it
  is not.

Two sentences the preamble must carry, asserted by the test suite:

> Every dataset here is an administrative record of an interaction with the
> city, not a survey or a measurement of a condition. **This warehouse contains
> NO GROUND-TRUTH MEASURE of the underlying state of anything.** Stated beside
> it: `census_block_groups` is a census, April 2020, a denominator, never a
> subject.

> **The closed-world rule.** If answering needs a column not in this pack, a
> join not in the join map, or a dataset not in the identity block, refuse and
> name what is missing. Do not infer a column from a table name.

The closed-world rule renders immediately after the list, and is the only part
of section 5 that stays correct when the warehouse changes and the prose does
not.

## 6. Mandatory disclosures

Answerable, with a bounded measured error the consumer must be told: section
5.2's object with `question_shapes` replaced by `when` and `instead` by `state`.
Required: `id`, `applies_to`, `when`, `state`, `why`, `evidence`.

## 7. The hand-maintained source

Sections 5 and 6, the traps, the undeclared joins and the candidate examples
live in **one** file, `tools/context_pack/prose.yml`. **Nothing derivable from
the dbt manifest, the catalog, the registry or the published manifest may appear
in it**; anything derivable found there is a bug.

## 8. Integrity and staleness

`integrity` carries, per model: schema hash, row count, and the dbt manifest
identity from 4.2. For `published`, also the `manifest_version` and per-dataset
`schema_hash` copied from `published/manifest.json`, which is the authority on
the export rather than something the pack recomputes. **Generation fails if any
example query errors, and fails if a schema hash does not match the live
target.** **A pack refuses on its own behalf**: it tells the consumer to compare
its `integrity` block against the target and refuse everything if they disagree.

**CI checks the committed pack against the fixture warehouse and generates
nothing.** It compares four things and no others: target name, `prose_revision`,
`spec_version`, per-model schema hash. Only the last needs a warehouse and a
schema hash does not depend on row counts, which is the whole of why this works;
row counts, profiles, freshness and example results are not compared. A pack for
another target is rejected outright rather than diffed. **A fixture hash that
disagrees is a real failure**: a model's shape depends on its data.

## 9. Rendering the compact markdown

Order, a design decision and not a formatting one: identity and target; how to
read this pack including the closed-world rule; **refusals**; **disclosures**;
**traps**; models and columns; join map; examples; freshness; integrity.
Refusals and traps precede the schema because a model that has read the schema
has begun composing SQL.

The markdown is budgeted, the budget is configurable, and the generator reports
the estimated tokens and what it dropped. **Under pressure it drops, in this
order:** surplus examples beyond the one required per class 3 refusal,
undocumented-column markers, profile statistics, low-signal columns.
**Refusals, disclosures, traps and grain sentences are never dropped.** If the
pack does not fit with all four present, generation fails rather than emitting a
truncated pack: one missing a refusal reads complete.

## 10. Left out on purpose

Argued in ADR-13 and ADR-15: no consumer and no evaluation of whether models
honour the refusals; no arrival-lag measurement, so
`refuse.newest-month-is-partial` is the one unmeasured warning; no per-dataset
licence; no metrics layer; no cross-target diff beyond `prose_revision`.
