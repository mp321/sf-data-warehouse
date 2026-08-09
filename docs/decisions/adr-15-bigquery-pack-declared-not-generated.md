---
status: active
date: 2026-08-07
related: [adr-13-context-pack-format, adr-1-warehouse-targets, plan-8-remaining-context-packs]
---

# ADR-15. The BigQuery context pack is declared and not generated

## Context

PLAN-8 left one open question: is the bigquery pack worth generating at all. It
was deliberately not answerable until a second pack existed, and now it is.

Two packs exist, generated 2026-08-07 from one prose file. The published one
earned its cost: its model set is six marts against nineteen models, so twelve
prose entries could not resolve against it, five refusals and one disclosure
exist only because the substitute differs there, and four examples had to be
written and executed because spec 4.7 rule 1 forbids inheriting the warehouse
ones.

**None of that transfers to bigquery, because its model set is identical to
duckdb's.** `pack_target.py` declares `models: "all"` for both. Every prose entry
that resolves against duckdb resolves against bigquery, so the two packs would
carry the same 20 refusals, 6 disclosures, 4 traps and 13 joins, word for word.
There is no "this is in the warehouse and not in this export" class to write,
because nothing is missing. What would differ is type names, row counts,
freshness, and six examples that have to be re-executed with credentials.

The forcing constraint is that the differing parts are exactly the parts nothing
can check. ADR-13 gives the bigquery target no schema hash, because
`publish/export.py` renders DuckDB type names and every hash would differ for a
schema that is identical. No schema hash means no `make context-pack-check`,
which means CI cannot gate it, which means the one committed artifact in this
repo that nothing can prove current would be this one.

## Options considered

**A. Generate and commit it, as PLAN-8 step 6 was written.** A consumer reading
BigQuery gets a pack whose types are BigQuery's. The case against: it is the
duckdb pack's prose with `STRING` for `VARCHAR`, it costs six examples executed
and read by hand with credentials on every regeneration, and nothing detects a
regeneration that did not happen. ADR-13 already records that regeneration is a
human step that can be forgotten and that the only thing catching a forgotten one
is the schema-hash gate. This pack has no gate, so a forgotten one is
undetectable, and a pack that reads complete while describing a build from three
months ago is the failure mode the refusal format exists to prevent.

**B. Declare the target and do not generate the pack.** What is already true,
made a decision and written down instead of sitting in a plan as an open step.

**C. Delete the bigquery target from `pack_target.py`.** Tidier, and wrong.
`applies_to: [bigquery]` in the prose would stop being meaningful, `open_target`
would fail with `Unknown target` rather than with the paragraph saying why this
one is not built, and spec section 2's three-target commitment is not reopened
here.

## Decision

**We do not generate a bigquery context pack. The target stays declared in
`pack_target.py`, with its model set, freshness source and schema-hash policy,
and `open_target` continues to raise for it with the reason.**

Spec section 2 still fixes three targets and a pack still describes exactly one;
this decides only that nobody produces the third artifact. `PLAN-8` step 6 is
struck rather than deferred, so no plan carries it as work someone might pick up.

What stands in its place for a consumer pointed at BigQuery:

- **`make parity-columns` and `scripts/parity-check.py`.** The column sets and
  the staging rows are compared across the two engines by tools that need the
  credentials the pack would have needed, and they answer the question a pack
  would only have asserted. The zone-versus-BigQuery column drift of 2026-08-05
  was found by the first of those and could not have been found by a pack.
- **The pack names its target in its identity block, and the self-refusal tells
  a consumer to compare the integrity block against what they are holding**
  (spec section 8). A duckdb pack handed to a BigQuery reader is a pack that says
  it is a duckdb pack.

## Consequences

**Buys.** No hand-generated artifact that nothing can gate. The prose file keeps
one target set that is actually generated, so an entry claiming `bigquery` is
still validated for shape and still never rendered, which is the same position it
has been in since ADR-13. `make check` stays credential-free.

**Costs.** A consumer querying BigQuery has no pack for it, and the nearest one
renders DuckDB type names: they will read `VARCHAR` where the column is `STRING`.
That is the whole case for option A and it is accepted, on the grounds that no
such consumer exists and ADR-13 already names the appearance of one as the point
at which the format is worth arguing about with evidence.

**Lock-in.** None new. The declaration stays, so reversing this is writing the
connection factory and the examples, which is what PLAN-8 step 6 was.

## Revisit if

- **A consumer reads BigQuery.** Same trigger as ADR-13's first, and this
  decision is wrong the day one appears.
- **The two engines stop being node for node identical on one zone.** They were
  on 2026-08-05, `PASS=171 ERROR=0` matching DuckDB. If they diverge, the
  bigquery pack stops being the duckdb pack with different type names and the
  argument above stops holding.
- **A second surface with the same model set and no schema hash is proposed.**
  The reasoning here is about that shape, not about BigQuery.
