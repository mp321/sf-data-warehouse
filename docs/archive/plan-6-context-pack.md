---
status: done
date: 2026-07-31
related: [adr-8-published-exports, adr-13-context-pack-format, plan-4-cloud-first-storage, plan-5-narrow-and-polish, plan-8-remaining-context-packs]
---

# PLAN-6. Emit a versioned context pack that tells a model what it must refuse

Migrated from `PLAN.md` Goal2, which is deleted. Step 1 done 2026-08-05; the
spec is `docs/specs/context-pack.md`. **Steps 2 and 3 done 2026-08-06**:
`tools/context_pack/` generates the DuckDB pack, and every rule the spec says
must fail the build fails it. **Step 4 done 2026-08-07 and ADR-13 closes this
plan**: CI checks the committed pack against the fixture warehouse on every pull
request, and both open questions are settled in the ADR.

**Closed with one target of three built.** The `published` and `bigquery` packs
are PLAN-8. Every Done-when box here is ticked, because this plan asked for the
artifact, the gate and the decision rather than for three artifacts; carrying it
open for the second pack would make it a plan about targets rather than about
the thing that was actually being proven.

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
2. ~~**Build the generator**, `tools/context_pack/`.~~ **Done 2026-08-06.**
   Reads dbt artifacts, runs
   profiling queries (cardinality, null rate, min and max, top values for low
   cardinality columns), merges the hand-maintained traps and refusals YAML,
   executes every candidate example query to verify it, and emits
   `context_pack.json` (complete) and `context_pack.md` (compact,
   token-budgeted, suitable for direct prompt injection). Make the markdown
   budget configurable and report the token estimate.

   **What was built.** Seven files under `tools/context_pack/`, plus
   `make context-pack` and `make context-pack-check`, plus
   `tests/test_context_pack.py`, 33 tests that need neither a warehouse nor a
   dbt manifest. The artifacts are `context-pack/context_pack.duckdb.json` and
   `.md`, and both are committed, which is what makes step 4 a diff rather than
   a rebuild. On the real warehouse: 19 models, 315 columns, 20 refusals, 6
   disclosures, 4 traps, 6 verified examples, 13 joins, and a markdown
   rendering of about 24,600 estimated tokens against a default budget of
   26,000.

   `generate.py` is the CLI and the assembly; `pack_inputs.py` is everything
   derived, `pack_target.py` is the three targets and what it means to open
   one, `pack_profile.py` is the per-shape column rules, `pack_prose.py` is the
   hand-maintained source and the citation resolver, `pack_render.py` is the
   markdown and the budget ladder, and `prose.yml` is the one copy of the
   prose. The modules are prefixed `pack_` for the reason ruff.toml gives about
   `ingestion/datasets.py`: the directory goes on `sys.path`, and `profile` is
   in the standard library.

   **Five places it departs from the step, or from the spec.**

   - **Only the duckdb target is generated.** The step does not say otherwise,
     and the spec's section 2 wants three. duckdb is the one that needs no
     credentials, so it is the one CI can gate on, and building it first was
     the session's instruction. `published` and `bigquery` are declared in
     `pack_target.py` with their model sets, freshness sources and schema-hash
     policies, so `applies_to: [published]` in `prose.yml` is already
     meaningful; what is missing for each is a connection factory and its
     prose entries. `open_target` raises for both rather than pretending.
   - **The joins block is half derived.** The spec puts joins in section 4.4
     and the prose file in section 7, and does not say which owns them. dbt's
     `relationships` tests already declare seven of the thirteen, so those are
     read from the manifest and `prose.yml` carries only the six no test
     declares, the dirty ones included. A prose join that restates a derived
     one fails generation, on section 7's rule about derivable content.
   - **The traps block is in the JSON and not in the markdown.** Section 9's
     rendering order lists nine sections and traps is not among them, so the
     markdown carries less, which is the direction section 3 allows. Worth a
     decision in the closing ADR rather than a silent reading: traps are the
     prose that does not make a question unanswerable, so a prompt-sized
     rendering can do without them, but nothing in the spec argues that
     explicitly.
   - **Section 9's second drop stage needed an interpretation.** "Column
     descriptions for columns with no yml description" has nothing to drop as
     written. What the renderer does is emit an explicit `(no description in
     the yml)` marker, which is a real signal under the closed-world rule, and
     drop that marker at stage 2.
   - **The newest complete month is derived from the data, not the clock.** It
     is the month before the month a column's maximum falls in. Reading it off
     today's date would change the pack at midnight on the first of the month
     with no data having moved, which would make step 4's drift check a monthly
     false alarm.

   **One defect found and fixed on the way.** `mart_activity_by_h3.category`
   was in the SQL and in the model's `unique_combination` test and had no entry
   in `_marts__models.yml`, so it was part of the grain and undocumented at the
   same time. Four more columns in the same model and five in
   `mart_activity_by_neighborhood` were in the same state. All ten are
   described now, and `category` gained the `not_null` test the rest of the
   grain already had, which is why `make build` is `PASS=172` where it was 171.
   The generator reports every undescribed column as a warning rather than
   special-casing any of them; 158 remain, almost all on staging models, and
   that list is a to-do for the yml rather than a defect in the pack.

3. ~~**Fail the build** if any example query errors, or if the schema hash does
   not match the live warehouse.~~ **Done 2026-08-06**, as four failures rather
   than two, since the spec asks for two more in the same voice. Generation
   fails on a model with no grain sentence, on a `prose.yml` entry that claims a
   target while citing something that target does not have, on an example query
   that errors or whose SQL no longer matches the hash a human attested to, and
   on a markdown rendering that cannot fit the budget with every refusal,
   disclosure and grain sentence present. The schema-hash half is
   `--check`, wired as `make context-pack-check`: it compares the committed
   pack against the live warehouse and exits 3 on disagreement. Row counts are
   deliberately not compared, because they move on every ingest and a gate that
   fires daily is a gate someone switches off.

4. ~~**Wire generation into CI** so the pack regenerates on every model change
   and drift shows up as a diff in the pull request.~~ **Done 2026-08-07, as a
   check and not a generation.** ADR-13 has the argument; the short version is
   that CI cannot generate an honest pack, because the only warehouse a
   credential-free runner can build is the fixture one, and a pack built there
   would carry seven-row counts and fixture example results.

   One step in the `dbt-duckdb` job, after `dbt build`:
   `python tools/context_pack/generate.py --target duckdb --check`. It exits 3
   on a moved schema hash, a moved prose revision, a moved spec version or a
   model set that has changed, and it writes nothing.

   **The measurement that decided it.** `--check` needs a warehouse for exactly
   one of the four things it compares, the per-model schema hash, and a schema
   hash is over column names, types and ordinal position. All 19 hashes from the
   fixture warehouse are identical to the real warehouse's, measured 2026-08-07.
   Both failure modes were then fired against the fixture warehouse rather than
   argued: a tampered hash and this session's own spec amendment each exit 3.

   **What it cannot see, and that is the trade.** Row counts, profiles,
   freshness and example results are not compared, so a pack whose numbers are a
   month old passes. The alternative is a gate that fires on every ingest, which
   is a gate someone switches off.

## Testing

A round-trip test on the JSON, a test that the compact markdown stays under
budget, and a test that a deliberately stale pack is rejected.

**Done 2026-08-06**, as `tests/test_context_pack.py`, 33 tests in the same
pytest job as the geometry ones, and 34 as of 2026-08-07, when the traps block
joined the never-dropped rule and gained a test for its position in the
rendering as well as its presence. All three asked for are there, plus the two
rules the spec says are easy to implement as warnings by mistake: an entry
citing something the target does not have raises, and no rendering at any
budget that succeeds is missing a refusal, a disclosure or a grain sentence.
None of it touches the real warehouse or the dbt manifest, both gitignored: the
profile tests build a four-row DuckDB in memory, and the drift tests exercise a
pure comparison function that `--check` feeds from the live target.

## Out of scope

- Any consumer of the pack. Building the artifact and building something that
  reads it are different projects, and doing both at once means neither
  constrains the other honestly.

## Done when

- [x] `docs/specs/context-pack.md` exists and was written before the code.
      Done 2026-08-05, step 1. It fixes the three targets, the artifact shape,
      the refusal classes and the rules the generator is verified against.
- [x] `make context-pack` produces both artifacts. Done 2026-08-06, for the
      duckdb target: `context-pack/context_pack.duckdb.json` and `.md`. The
      other two targets are declared and not generated; see step 2.
- [x] CI fails on a stale pack or an unverified example. **Done 2026-08-07.**
      An unverified example fails generation, and the `dbt-duckdb` job now runs
      `--check` against the warehouse it just built from fixtures. Demonstrated
      there, not only in pytest: a tampered schema hash and a moved spec version
      both exit 3 against that warehouse.
- [x] An ADR records the pack format and, specifically, what was left out.
      **ADR-13, 2026-08-07.** Section 10's six omissions, the five departures
      from step 2, and the traps question, which it settles by amending the spec
      rather than by confirming the reading.

## Open questions

- ~~**Which warehouse does CI check the pack against?**~~ **Answered 2026-08-07
  in ADR-13: the fixture warehouse, and CI generates nothing.** The plan's first
  option, with its reasoning checked rather than inherited and one thing added
  that had not been noticed. Regenerating in CI fails for a second and
  independent reason: generation is not deterministic against an unchanged
  warehouse, because `generated_at`, the dbt invocation id and the clock-derived
  columns of `mart_pipeline_freshness` move on every run. An idempotent
  regeneration on 2026-08-07 produced a 40-line diff, every line a clock, so
  `git diff --exit-code` was never available as a gate at all.
- ~~**Should the traps block be in the compact markdown?**~~ **Answered
  2026-08-07 in ADR-13: yes, and the spec was amended.** Section 4.6 defines a
  trap as a disclosure object without the trigger condition, so rendering the
  conditional warning and withholding the unconditional one is the wrong way
  round. It costs 585 estimated tokens of 25,219, and it is never dropped: the
  budget ladder sheds detail from the models block and a trap is not detail.
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
