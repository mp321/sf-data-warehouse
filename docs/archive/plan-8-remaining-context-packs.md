---
status: done
date: 2026-08-07
related: [plan-6-context-pack, adr-13-context-pack-format, adr-15-bigquery-pack-declared-not-generated, adr-8-published-exports, adr-12-published-export-layout, adr-1-warehouse-targets]
---

# PLAN-8. The other two context packs

PLAN-6's residue, homed rather than carried. The spec fixes three targets and
one of them is built; this plan is the other two. The contract is
`docs/specs/context-pack.md` section 2 and it is not reopened here.

## Goal

`context-pack/context_pack.published.json` and `.md`, generated and committed,
and the same for `bigquery` when someone runs it with credentials. The published
one is the point of the plan; the BigQuery one is a smaller job that follows for
free once the generator has been proven target-agnostic by a second target.

## Why now

**Because one pack does not test the argument the spec makes.** Spec section 2
commits to three self-contained artifacts with one hand-maintained prose file,
on the reasoning that a pack hedging across surfaces is wrong about whichever
one the reader is holding. Everything in the generator that serves that
argument, the per-target model set, `applies_to`, and the rule that an entry
whose citations do not resolve is not rendered, has been exercised against one
target only. A second pack is the first real test of it, and it is the cheapest
one available: the published target needs no credentials.

It is also where the spec's sharpest claim gets checked. The published export is
six marts and no staging models, so questions that are answerable in the
warehouse are refusals there. Nothing in the repo has ever produced that
artifact, so nobody has read one.

## What is already done

Measured 2026-08-07, so this plan starts with the audit rather than with a
survey.

- `pack_target.py` declares the published target's model set
  (`PUBLISHED_MARTS`, six marts), freshness source (`published/manifest.json`)
  and schema-hash policy (present, since the export is written from a DuckDB
  build by `publish/export.py` and the hash renders DuckDB type names).
- The prose is already target-aware. Of 20 refusals, 19 carry `published` in
  `applies_to`; 5 of 6 disclosures and 3 of 4 traps do; 2 of 6 joins do. Those
  were written and validated when the duckdb pack was built, so they are
  claims, not guesses: an entry claiming a target it cannot resolve against
  already fails generation.
- `published/` exists locally with all six marts and a `manifest_version` 2
  manifest.

**Steps 1 to 4 done 2026-08-07, and the published pack exists.** Both artifacts
are in the working tree, CI checks the second one beside the first, and the four
examples were written over the export, executed and read. Step 5 followed later
the same day and step 6 was struck by ADR-15, which closes this plan at two packs
rather than three. What the audit below got wrong is worth
keeping: the prose was target-aware and its `published` claims had never been
checked, because a claim is only checked when the target it names is generated.
Twelve of the entries claiming `published` could not resolve against the six
marts, and fixing them was most of step 3 rather than a preliminary to it.

## Steps

1. **A connection factory for the published target** in `pack_target.py`. An
   in-memory DuckDB with one view per mart over `published/<mart>/*.parquet`,
   read from `PUBLISH_DIR`. It is the only new machinery: `schema_hash`,
   `columns` and `row_count` all work through the same `Target` once views
   exist.
2. **The two blocks that read the warehouse rather than the target.**
   `build_freshness_block` queries `mart_pipeline_freshness` directly and must
   branch: for `published`, freshness is the publish time from
   `published/manifest.json` and the pack says which, because the gap between
   publish time and build time has been days (spec 4.5). `build_integrity_block`
   already has its published branch and needs checking against a real run.
3. **The published-only refusals.** The class the spec commissions and that does
   not exist yet: "this is in the warehouse and not in this export", for the
   staging models, `int_point_activity`, the H3 bridge and per-point detail.
   These are the entries that make the published pack a different document
   rather than a shorter one.
4. **Examples, and this is the expensive step.** An example is verified against
   the target whose pack it appears in and nowhere else (spec 4.7 rule 1), so
   the six duckdb examples cannot be inherited. Four class 3 refusals apply to
   published and each needs one, or generation fails, which is the rule working.
   Each means writing the SQL over the Parquet views, executing it, reading the
   result, agreeing it answers the question, and only then stamping its
   `sql_sha256`. Do not batch this: the attestation is the whole value.
5. **Tests**, in `tests/test_context_pack.py`, over an in-memory Parquet
   directory rather than the real export. The one worth writing first is that a
   refusal citing a staging model is not rendered into the published pack, since
   that is the assertion the whole three-pack argument rests on.
   **Done 2026-08-07.** Eleven tests over a tmp-directory export, plus six
   existing ones parameterised over both targets rather than duplicated: 210
   pytest against 194.
6. ~~**The bigquery pack**, by hand, beside `make build-bigquery`.~~ **Struck
   2026-08-07 by ADR-15**, which answers the second open question below rather
   than deferring it: the target stays declared in `pack_target.py` and the
   artifact is not generated.

## Constraints

- Nothing derivable goes into `prose.yml`. Same rule as PLAN-6 and the same
  reason.
- An unverified example is worse than no example.
- No new hard failures softened into warnings to make a pack generate.
- The published pack is generated by hand after `make publish`, and CI checks it
  the way ADR-13 has CI check the duckdb one. Do not generate a pack in CI.

## Out of scope

- Any consumer of any pack. Unchanged from PLAN-6.
- A cross-target diff tool. `prose_revision` makes disagreement detectable and
  ADR-13 records that nothing reconciles it.

## Done when

- [x] `make context-pack TARGET=published` writes both artifacts and they are
      committed. Written 2026-08-07; committing is the human's call, so they are
      in the working tree.
- [x] The published pack refuses a question the duckdb pack answers, and the
      refusal names the export rather than the data. Five of them do:
      `refuse.export-has-no-row-level-records`,
      `refuse.export-has-no-staging-or-intermediate-models`,
      `refuse.export-has-no-h3-bridge`,
      `refuse.export-counts-permit-records-at-filing` and
      `refuse.export-counts-registrations-not-businesses`.
- [x] Every example in it was executed against the Parquet and read by a human.
      Four, one per class 3 refusal, none of them the duckdb query.
- [x] `make context-pack-check TARGET=published` is in CI beside the duckdb one,
      against the export `make ci-build` writes from fixtures. Measured: all six
      hashes from the fixture export are identical to the real export's.
- [x] A dev note says whether the three-pack argument survived a second pack, in
      those words. `docs/dev-notes/2026-08-07.md`, third session.
- [x] The published target has tests, and the first of them is the one this plan
      names. `tests/test_context_pack.py`, 210 pytest against 194.
- [x] The bigquery question is decided rather than carried. ADR-15, no.

## Open questions

- **Does the Makefile grow a target per pack, or one target with a variable?**
  Two packs is the point at which `make context-pack` and
  `make context-pack-check` stop being one command each. A `TARGET=` variable
  keeps one code path; a target per pack is more discoverable in `make help`.
  Decide when the second pack exists rather than now.
  **Answered 2026-08-07, with the second pack built: one target and a `TARGET=`
  variable, defaulting to duckdb.** The deciding argument is not the code path,
  which is one line either way. It is that the difference between the two
  commands is which artifact has to exist first, `make build` against
  `make publish`, and `generate.py` already refuses each with the sentence that
  names the missing one. What a target per pack would have duplicated is the
  comment above them, which is the thing that must not drift: it is the one
  place that says why generating after `make check` produces a pack whose
  invocation id is a fixture run's. The cost is real and is accepted: a variable
  does not appear in `make help`, so the help text for both targets names it.
- **Is the bigquery pack worth generating at all?** It needs credentials, has no
  schema hash, and ADR-13 already records that it will be stale more often than
  not. The honest alternative is to build the published pack, then decide
  whether a pack nobody can gate is a pack worth committing.
  **Answered 2026-08-07, no, and written down as ADR-15.** The evidence the
  second pack provided is what decided it, and it decided it against the plan's
  own expectation. The published pack was worth its cost because its model set is
  six marts against nineteen models: twelve entries could not resolve, six exist
  only because the substitute differs, four examples could not be inherited.
  **The bigquery target's model set is `all`, identical to duckdb's**, so every
  entry that resolves there resolves here and the two packs would carry the same
  20 refusals, 6 disclosures, 4 traps and 13 joins word for word. What differs is
  type names, row counts, freshness and six examples that must be re-executed
  with credentials, and those are exactly the parts no schema hash can gate. So
  the third artifact is the first one nothing could prove current, and
  `make parity-columns` answers the cross-engine question a pack would only have
  asserted. The target stays declared; step 6 is struck rather than deferred.
