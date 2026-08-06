---
status: active
date: 2026-07-31
related: [adr-8-published-exports, plan-4-cloud-first-storage, plan-5-narrow-and-polish]
---

# PLAN-6. Emit a versioned context pack that tells a model what it must refuse

Migrated from `PLAN.md` Goal2, which is deleted. Step 1 done 2026-08-05; the
spec is `docs/specs/context-pack.md` and steps 2 to 4 are the remainder.

## Goal

A versioned, model-agnostic context artifact that lets any capable LLM query
this warehouse correctly, and that tells it what it must refuse to answer.
`make context-pack` produces it and CI catches drift.

## Why now

Not yet. This is the most distinctive idea in the project and it should be
built on a foundation that has been proven, not one that has not. A pack that
documents refusal boundaries for a BigQuery target nobody has ever executed
would be describing a warehouse rather than reporting on one.

Sequenced after PLAN-4 and PLAN-5 for a second reason: the pack enumerates
models, grains and traps, so every dataset cut in PLAN-5 is a pack that has to
be regenerated and re-verified.

Read first: CLAUDE.md, `dbt/target/manifest.json` and `catalog.json`,
`mart_pipeline_freshness`, and the manifest that `publish/export.py` already
writes, which carries most of the identity and integrity fields this needs.

## Constraints

- The pack is generated, not hand-written, except for the parts that cannot be
  derived from schema. Those live in one hand-maintained YAML and nowhere
  else.
- Every example query in the pack must have been executed successfully against
  the current warehouse. An unverified example is worse than no example.
- No credentials required to generate it against DuckDB.

## Steps

1. **Write the spec first**, at `docs/specs/context-pack.md`, before any code.
   A pack contains:
   - identity: what this warehouse is, publisher, licence, jurisdiction,
     update cadence, pack version, generated_at
   - per model: grain statement, column semantics (units, enum meanings, what
     null means, where two similar columns differ), row count
   - join map: keys, cardinality, known dirty joins
   - freshness: per source last load, staleness flag, expected cadence
   - known traps: prose warnings that cannot be derived from schema. Late
     arriving records making recent periods undercount. The membership error
     at the chosen H3 resolution, which ADR-6 measures rather than assumes.
     The fact that a raw count per neighborhood is mostly a map of where
     people live.
   - verified examples: a natural-language question paired with SQL that has
     been executed against the current warehouse
   - refusal boundaries: an explicit list of question shapes this data cannot
     answer, with the reason. This is the part that makes it a trust artifact
     rather than a README. Give it real thought: "which neighborhood is most
     dangerous" is not answerable from 311 volume, and the pack should say so
     in a form a model will actually honour.
   - integrity: a schema hash so a consumer can detect pack and warehouse
     drift. `publish/export.py` already computes one over column names, types
     and order; reuse it rather than inventing a second.
2. **Build the generator**, `tools/context_pack/`. Reads dbt artifacts, runs
   profiling queries (cardinality, null rate, min and max, top values for low
   cardinality columns), merges the hand-maintained traps and refusals YAML,
   executes every candidate example query to verify it, and emits
   `context_pack.json` (complete) and `context_pack.md` (compact,
   token-budgeted, suitable for direct prompt injection). Make the markdown
   budget configurable and report the token estimate.
3. **Fail the build** if any example query errors, or if the schema hash does
   not match the live warehouse.
4. **Wire generation into CI** so the pack regenerates on every model change
   and drift shows up as a diff in the pull request.

## Testing

A round-trip test on the JSON, a test that the compact markdown stays under
budget, and a test that a deliberately stale pack is rejected.

## Out of scope

- Any consumer of the pack. Building the artifact and building something that
  reads it are different projects, and doing both at once means neither
  constrains the other honestly.

## Done when

- [x] `docs/specs/context-pack.md` exists and was written before the code.
      Done 2026-08-05, step 1. It fixes the three targets, the artifact shape,
      the refusal classes and the rules the generator is verified against.
- [ ] `make context-pack` produces both artifacts.
- [ ] CI fails on a stale pack or an unverified example.
- [ ] An ADR records the pack format and, specifically, what was left out.

## Open questions

- ~~Does the pack describe the DuckDB warehouse, the BigQuery one, or the
  published Parquet?~~ **Answered 2026-08-05 in `docs/specs/context-pack.md`
  section 2: one pack per target, three self-contained artifacts, one
  hand-maintained source.** A pack that hedges across surfaces is wrong about
  whichever one the reader is holding, and the surfaces differ by more than
  freshness: the published export is six marts and no staging models, so
  questions that are answerable in the warehouse are refusals there. Two of the
  three generate in CI with no credentials, because DuckDB reads the published
  Parquet directly. The BigQuery pack is hand-generated and carries a staleness
  guard instead of a schema hash, since `publish/export.py`'s hash renders
  DuckDB type names.
