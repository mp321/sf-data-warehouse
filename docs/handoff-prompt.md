# Claude Code handoff prompts

Transient. Delete this file once PLAN-5, PLAN-6 and PLAN-7 are closed. The
durable instructions are the plans themselves; these prompts only point at
them, which is what makes them survive a context window running out
mid-session.

Run these in order, one per session, in the repo root with the venv active.
Do not merge two sessions into one: each ends at a verifiable state, and the
value of the sequence is that a failure is attributable.

Sessions are sized for Claude Code on Opus 5 at extra effort, which is roughly
one substantial refactor plus its verification, or two or three small
mechanical changes plus theirs. Where a session bundles several plan steps,
the ordering inside it is load bearing and the prompt says why.

---

## Sessions A through D are done. Start at E.

- **A (prove BigQuery)** done 2026-07-31. It found four cross-engine defects and
  all four are fixed; both targets build `PASS=196 ERROR=0` and
  `scripts/parity-check.py` compares models row for row on demand. See the
  fourth section of `docs/dev-notes/2026-07-31.md`.
- **B (zone to GCS)** mostly done 2026-08-01: steps 5, 7 and 9. Both zones are
  read from `gs://` by DuckDB through gcsfs and by BigQuery through external
  tables, BigQuery storage went 8.02 GB to 40.96 MB, and `make publish` has
  uploaded to the bucket once. ADR-9 is written and ADR-4 is superseded. See
  `docs/dev-notes/2026-08-01.md`.

- **B-remainder (move the writer)** done 2026-08-01: steps 6, 8 and 11.
  `ingest.py` and `spatial.py` now write the bucket, so nothing is synced by
  hand and the zone no longer depends on one laptop. The Actions cache step is
  deleted and PLAN-1 is closed. A run writes one zone and not two: with a URI
  set, `data/` is not updated at all, which is the decision that had to be made
  rather than assumed. See the second session in `docs/dev-notes/2026-08-01.md`.

- **C (cut the two datasets)** done 2026-08-04. PLAN-5 steps 1, 2 and 3, plus
  step 10 out of order. `city_budget` and `street_trees` are gone, H3 r9 is
  gone, `mart_activity_by_h3` moved to r8 on a measurement, and ADR-10 records
  all of it. Seven datasets, 19 models, 148 tests. See the second and third
  sections of `docs/dev-notes/2026-08-04.md`.

- **D (test the geometry, then split spatial.py)** done 2026-08-03. PLAN-5
  steps 5 and 6. `tests/test_geometry.py` is 95 cases over both point-in-polygon
  implementations, `make test-python` exists and gates the end-to-end dbt job in
  CI, and `spatial.py` is four files. The derived zone rebuilt to identical row
  counts and, for five of six tables, identical content. The sixth, which is
  `derived_h3_population`, differs by 4.5e-13 residents on one cell of 39,301
  and turns out to be non-deterministic run to run in the code as it already
  was; that matters for session F and is written up in the fourth section of
  `docs/dev-notes/2026-08-04.md`.

PLAN-4 closed on 2026-08-03 when `ingest.yml` went green on a runner.

---

## Session B-remainder. Done 2026-08-01. Kept for the record only

Do not run this again. The prompt is left here because the constraints in it are
the reason the session went the way it did, and because the plan annotations
refer back to it.

```
Read CLAUDE.md, docs/decisions/adr-9-cloud-raw-zone.md,
docs/plans/plan-4-cloud-first-storage.md, and
docs/dev-notes/2026-08-01.md.

Execute PLAN-4 steps 6, 8 and 11. Steps 1 to 5, 7 and 9 are done; do not
redo them.

Step 6 is the substance: ingest.py writes to the raw zone URI when it is
set, and spatial.py writes the derived zone the same way. The read side is
already built, so use it: ingestion/remote.py owns the local-or-bucket
question and the authentication, and the write paths in raw_zone.py and
derived_zone.py currently raise NotImplementedError on a remote root,
which is where your change goes. Do not add a second way to talk to GCS.

Constraints that are not negotiable:
- ingest.py's incremental watermark is the subtlest code in the repo and
  its failure mode is a silent full backfill of 8.8 million rows, not an
  error. Change it deliberately, in its own commit-sized step, and verify
  a resumed run fetches zero rows before you touch anything else.
- The raw zone is append-only. A remote write adds objects; it never
  rewrites a prefix. The one exception is --full-refresh, and swapping a
  whole tree atomically is harder on object storage than on a filesystem,
  so if you cannot do it atomically, say so rather than doing it
  non-atomically.
- RAW_ZONE_DIR must keep beating RAW_ZONE_URI. `make ci-build` depends on
  it and so does every fork pull request.
- Run `make check` in a clean shell AND with `.env` sourced. Both must be
  green and both must stay local.

Then step 8: delete the cache step in .github/workflows/ingest.yml and
rewrite the long comment at the top of that file, which is mostly about
the cache. The cache existed to carry the local zone between runs, and
step 6 is what makes it unnecessary.

Then step 11: close PLAN-1. status: done, step 5 ticked, and point its
remaining open question at ADR-9.

One thing to decide and record rather than assume: after step 6, is
data/raw still written at all, or does a remote run write only to the
bucket? A local copy that is sometimes written and sometimes not is worse
than either. Whichever you choose, say it in the dev note and in CLAUDE.md.

Do not commit or push.
```

**Checkpoint:** a scheduled ingest run writes to the bucket, `ingest.yml` has no
cache step, PLAN-1 is closed, and `make check` is green both with and without
`.env` loaded.

---

## Session D. Done 2026-08-03. Kept for the record only

Do not run this again. PLAN-5 steps 5 and 6 are both done. The prompt is left
here because its two constraints are what shaped the result: writing the
edge-and-vertex contract down rather than asserting today's output is what
turned an awkward test into the useful part of the file, and the baseline row
counts it names are what caught the one number that moved.

These were one session because the ordering between them is the whole point:
the tests are the safety net for the refactor, so writing them in a separate
session invites someone to start with the split.

```
Read CLAUDE.md, docs/plans/plan-5-narrow-and-polish.md, and
docs/decisions/adr-6-polygon-membership.md.

Execute PLAN-5 steps 5 and 6, in that order, and do not start step 6 until
step 5 is green. Step 5 is pytest on ingestion/geometry.py. Step 6 splits
ingestion/spatial.py, which is 942 lines, into h3_points.py, boundaries.py
and population.py.

Why the order is not negotiable: geometry.py is 280 lines of hand-rolled
point-in-polygon and spherical area, it is the highest-risk untested code
in this repo, and it is covered today only indirectly by assertions inside
spatial.py. Splitting spatial.py first means refactoring the caller of
untested code with no way to tell whether you changed its behaviour.

Step 5 specifics. The cases the plan asks for are a point strictly inside,
one strictly outside, one on a vertex, one on an edge, one inside a hole, a
degenerate polygon, and a multipolygon. Two of those are genuinely
undefined rather than merely tricky: a point exactly on an edge and a point
exactly on a vertex have no single correct answer in a ray-casting
implementation, and the standard is that the result is consistent rather
than that it is true. Read point_in_ring before writing its test, decide
what the contract is, assert that, and write the reasoning into the test's
docstring rather than asserting whatever the code happens to do today.

Also assert ring_area_sq_km and geometry_area_sq_km against a shape with a
known answer. A one-degree spherical square near the equator is the usual
choice; whatever you pick, put the expected value and its source in a
comment, because a magic number in an area test is untestable in itself.

Add pytest to requirements-dev.txt. Do not float the version if the file's
existing comment about ruff pinning applies to it too; read that comment
first and follow whichever rule it establishes. Add a `make test-python`
target and wire it into `make check` and .github/workflows/ci.yml before
the dbt job, since it is the fastest gate in the set. Verify CI ordering by
reading ci.yml rather than assuming: the lint job and the dbt-duckdb job
are separate jobs, so "before the dbt job" is a question about the workflow
graph and not about step order in one job.

Step 6 specifics. spatial.py's module docstring explains three containment
modes, and that explanation must move to whichever file uses them rather
than being duplicated across all three or left behind in a file that no
longer contains the code. The existing section-comment banners in
spatial.py are close to the intended file boundaries; read them first.
build_point_h3 and classify_coordinate go to h3_points.py, build_boundaries
and build_point_boundary and the pip sample to boundaries.py,
build_h3_population to population.py. The CLI entry point has to stay
somewhere that `python ingestion/spatial.py --all` still works, because the
Makefile, ci.yml and ingest.yml all invoke it by that path.

Verification that means something: `make ci-build` twice, and
`make clean-derived && make spatial` on the real local zone, then compare
the derived zone row counts against the ones in the 2026-08-03 dev note
(506,632 point rows, 84,296 bridge rows, 39,301 population cells, 24,000
pip samples). A refactor that changes any of those numbers changed
behaviour.

Do not commit or push.
```

**Checkpoint, and what actually held.** `make test-python` exists and fails
when a geometry test fails, verified by mutating `geometry.py` nine ways: eight
were caught, and the ninth was an equivalent mutant rather than a gap. The
derived zone rebuilds to identical row counts on every table. `spatial.py` is
four files rather than three, since the entry point stays where the Makefile and
both workflows invoke it, and one of them is over the size the plan asked for:
`boundaries.py` is 470 lines against "about 350", because the prompt above
assigns it four of the six derived tables. Splitting the oracle and the pip
sample out again would fix the number and is left for step 13 to judge rather
than decided here.

---

## Session E. One registry, one rename, one retention window

PLAN-5 steps 4, 7 and 8. Bundled because steps 4 and 7 both rewrite the dataset
registry and its callers, and doing them in separate sessions means touching
the same five files twice. Step 8 is a small independent rider that fits in the
remaining budget.

**Session D grew this step's surface.** When step 7 was written, three files
imported the registry. The `spatial.py` split makes it five: `ingest.py`,
`load.py`, `spatial.py`, `h3_points.py` and `boundaries.py`. Confirm with
`grep -rn "from datasets import" ingestion/` before starting rather than
trusting this paragraph, since Session E may move things again.

Paste this:

```
Read CLAUDE.md, docs/plans/plan-5-narrow-and-polish.md, and
docs/plans/plan-2-ingestion-lint.md.

Execute PLAN-5 steps 4, 7 and 8. Do 4 and 7 together as one change to the
registry and its callers, rather than sequentially, because both rewrite
the same import sites and the second would otherwise re-edit the first.

Step 4 is the substance. vars.pipeline_sources in dbt/dbt_project.yml
duplicates ingestion/datasets.py, and today the duplication is documented
rather than prevented: datasets.py's own docstring says "The dbt side keeps
its own copy of this list ... Adding a dataset means adding it in both
places." The plan offers two designs, generating the dbt vars from the
Python registry at build time or moving the shared fields into a YAML both
read, and says to pick whichever is smaller.

The acceptance test is not that the result is tidier. It is that the two
cannot disagree silently. If your design still permits someone to add a
dataset in one place and have `make check` pass, it has not worked. State
plainly in the dev note which failure mode now catches it and at what
moment: parse time, build time, or a test.

Note what the dbt side needs that the Python side does not currently
carry: `tier` and `stale_after_hours` per source, which mart_pipeline_
freshness reads. Whatever design you choose has to carry those, and the
Python registry is the natural home if there is to be one registry.

Step 7 closes PLAN-2. Rename ingestion/datasets.py to dataset_registry.py.
Find the importers with grep rather than from a list; as of Session D there
are five, ingest.py, load.py, spatial.py, h3_points.py and boundaries.py,
where the plan text still says three. Also update the Makefile,
.github/workflows/ingest.yml, ruff.toml's known-first-party list and
CLAUDE.md's directory conventions.

The hazard being fixed is real and not stylistic: `from datasets import
DATASETS` resolves only because Python puts the script's directory on
sys.path, and it collides with the HuggingFace `datasets` package the
moment anything pulls that in. ruff.toml's known-first-party entry is the
workaround being removed, and note that the split added `boundaries`,
`h3_points` and `population` to that same list, which are three more
generic names on sys.path. Whether they deserve the same treatment is worth
one paragraph of judgement, not a reflex rename: `boundaries` and
`population` are considerably more collidable than `derived_zone`. Say what
you decided and why. Then set PLAN-2 to status: done.

Step 8 is small and separate. meta_dbt_run_results grows by one row per
node per run forever. Add a rolling retention window in
dbt/macros/audit_run_results.sql, in the on-run-start hook where the
existing header comment already says the fix belongs, and write the chosen
window into that header. Note the constraint the macro's header states: the
mart reports the previous completed run, so a window that keeps fewer than
two runs breaks mart_pipeline_freshness rather than merely pruning it.

Finish with `make check` green and `make ci-build` green twice.

Do not commit or push.
```

**Checkpoint:** the dataset list exists once, `ingestion/datasets.py` no longer
exists under that name, PLAN-2 is closed, and the run-results table has a
documented retention window.

---

## Session F. Make spatial.py incremental, and cut the publish object count

PLAN-5 steps 9 and 12. Bundled because both are about work the pipeline repeats
without needing to, and both are measured rather than argued.

Paste this:

```
Read CLAUDE.md, docs/plans/plan-5-narrow-and-polish.md,
docs/decisions/adr-5-h3-computation.md, docs/decisions/adr-8-published-
exports.md and docs/decisions/adr-9-cloud-raw-zone.md.

Execute PLAN-5 steps 9 and 12.

Step 9 makes ingestion/spatial.py incremental, keyed on unprocessed
ingest_date partitions. Today it recomputes every point on every run, about
40 seconds per 700k points, linear, on every scheduled build.

The non-negotiable constraint is in CLAUDE.md and in ADR-5: the derived
zone is a pure function of the raw zone plus spatial.py. So the code-version
stamp is not optional and is not a nicety. If spatial.py changes and the
stamp does not force a full recompute, the derived zone becomes a cache of
a function that no longer exists, and nothing in the project will detect
it. Decide what the stamp covers, hash of the source file or an explicit
version constant, and write the tradeoff into the module header: a file
hash is automatic and fires on a comment change, a constant is precise and
someone will forget to bump it.

Verify with the plan's own done-when: a second `make spatial` on an
unchanged zone does substantially less work than the first. Then verify
the harder half, which is that it is still correct: `make clean-derived &&
make spatial` and compare row counts against an incremental run's output.
They must be identical, not close.

Row counts, and not a byte comparison of the Parquet, and session D found
out why. derived_h3_population is not reproducible bit for bit: it sums
each block group's share into a per-cell float, in an order that comes off
a Python set, so two runs of identical code over an identical zone differ
by about 5e-13 residents on a couple of the 39,301 cells. Totals are exact
and every other derived table is stable. If you want a byte comparison as
the incrementality check, make that sum order-independent first, in its own
step, and say so; otherwise compare counts and sums with a tolerance and
do not report the difference as a regression.

Note that check_derived.py already compares a recorded per-dataset raw row
count against the raw zone as it is now. Read it before designing the
manifest for step 9; you may be extending that file rather than writing a
new mechanism.

Step 12 is the published object count, and it is a measurement problem
before it is a code problem. One publish is 2,885 objects against a free
tier of 5,000 Class A operations a month, and 17 MB takes 6 minutes 39
because the cost is per object. The cause has been measured and is not the
H3 resolution and not the data volume: mart_activity_by_h3 writes 879
monthly partitions, because business_locations carries location_started_at
values back to 1967 and PUBLISHED_MARTS partitions that mart by
event_month over the whole range. mart_activity_by_neighborhood does the
same and accounts for 873 more.

Options in publish/export.py, in the order they look sensible: partition by
year rather than month, which takes 879 objects to about 73; floor the
mart's date range; or drop hive partitioning for a single file per mart.
Read the comment above PUBLISHED_MARTS before choosing, because it argues
that partitioning is deliberately not inferred and that silently
repartitioning a published dataset breaks every consumer's paths. That
argument applies to this change too, so whatever you pick is a breaking
change to the published layout and needs to be recorded as one.

ADR-8 is what governs published exports. It needs either a note or a
successor ADR recording the outcome, including the case where the decision
is to live with the count. "We measured it and chose to accept it" is a
legitimate result here and is much better than an undocumented 2,885.

Do not commit or push.
```

**Checkpoint:** a second `make spatial` is substantially faster and provably
identical, one publish is under 200 objects or ADR-8 says why not.

---

## Session G. The obsolescence sweep

PLAN-5 step 13, and it closes PLAN-5. Deliberately its own session: it is a
reading task, and bundling it with a code change guarantees it gets the
leftover attention.

Paste this:

```
Read CLAUDE.md, docs/README.md and docs/plans/plan-5-narrow-and-polish.md
step 13.

Execute PLAN-5 step 13, then close PLAN-5.

This is a sweep for things the last several plans made obsolete. Read every
README, module docstring, function header comment, Makefile target comment
and ADR pointer in the repo, and check each against the code as it is now.
Three outcomes per item: correct it, delete it, or shorten it.

What survives, and this is the actual judgement call rather than a
formality. Keep a finding, a measurement or a tradeoff that a human or an
LLM would plausibly look up again: why compile-bigquery needs both the fake
key and --no-populate-cache, why H3 is computed in Python, why the flat
lat/lon path is still covered, the r8/r9/r10 row counts. Delete anything
whose only content is that work happened, which is what dev notes are for.
When something is worth keeping, put it at the top of the file or function
it concerns rather than in a document about that file.

Known candidates, none of them a foregone conclusion:
- docs/review-2026-07-31-scope-and-cloud.md. docs/README.md says it can be
  deleted once PLAN-4, PLAN-5 and PLAN-6 are done. Two of three by then.
- docs/handoff-prompt.md, this file. It says to delete itself once PLAN-5,
  PLAN-6 and PLAN-7 are closed.
- USER-NOTES.md. Check whether anything in it is still true.
- SETUP.md's length against CLAUDE.md's. SETUP.md is the human onboarding
  path and is allowed to be longer, but it is not allowed to disagree.
- The dbt/models/marts/README.md "Review workflow with Claude" section.
- ADR-2 and ADR-3, superseded, and whether their reasoning is still worth
  the read-first order pointing at them.

Two rules. CLAUDE.md wins any disagreement with README.md or SETUP.md, and
the other file is what gets corrected. ADRs are immutable: an inaccuracy in
an accepted ADR is corrected by a new ADR or by a note in the superseding
one, never by editing the decision text.

Finish by verifying the "Done when" list in PLAN-5 line by line against the
repo, ticking what holds, and setting PLAN-5 to status: done only if every
box is genuinely ticked. If one is not, say which and why rather than
closing the plan around it.

Do not commit or push.
```

---

## Session H. Pipeline assurance

PLAN-7, both steps. Independent of PLAN-5 and can be run before session G if
you would rather have the checks than the tidy.

```
Read CLAUDE.md, docs/plans/plan-7-pipeline-assurance.md, and
ingestion/check_derived.py.

Execute PLAN-7 steps 1 and 2.

Step 1's open question is the first thing to settle and it is a design
question, not a preference: does the manifest reconciliation belong in
check_derived.py, which already exists to assert one zone is not behind
another, or in a new script? Look at that file before writing anything.
Two scripts asserting neighbouring invariants may well be one script.

Step 2 extends scripts/parity-check.py rather than adding a second script.
It already connects to both engines and already has an --all-staging mode.

Both checks must fail loudly and name what disagreed. A check that reports
"mismatch" without saying which dataset and which number is a check nobody
will trust at 2am.

Do not commit or push.
```

---

## Session I. The context pack

Only after PLAN-5 is closed.

```
Read CLAUDE.md and docs/plans/plan-6-context-pack.md.

Execute PLAN-6 step 1 only: write docs/specs/context-pack.md. No code this
session.

The refusal boundaries section is the reason this artifact is interesting
and it is the part most likely to be written as filler. Spend most of the
session on it. Concrete examples to work from: 311 volume does not measure
where problems are, it measures where people report them, so "which
neighborhood has the most problems" is not answerable. A raw count per
boundary is close to a map of where people live. Boundary membership at
the chosen H3 resolution has a measured error, in ADR-6, that a consumer
must be told rather than left to discover.

Note that the project is now seven datasets and every one is spatial
(ADR-10), which makes the scope of what the pack must describe smaller and
the refusal boundaries sharper than they would have been three sessions
ago. There is no budget data to refuse questions about any more.

Show me the spec before writing a single line of the generator.
```

---

## Standing notes for every session

**Committing.** CLAUDE.md forbids an agent from running `git commit`, `git
push`, `git add` or anything that writes history, and that rule is doing real
work here: it is why the whole 2026-07-31 session survived as a reviewable
diff. If you want Claude Code to commit to `branch1` itself, say so explicitly
in that session and amend CLAUDE.md to match, rather than leaving the file
saying one thing and the session doing another. Otherwise commit by hand
between sessions, which also gives you a natural review point.

**When a session runs long.** Stop at the last completed plan step, append the
dev note, and start a fresh session. The plans are written so a new context
window can pick up from a step number.

**If a step turns out to be wrong.** These plans are intent, not law. If
something in them does not survive contact with the code, say so, write the
disagreement into the dev note, and change the plan. That is what
`docs/README.md` means by a plan being mutable until it is done.
