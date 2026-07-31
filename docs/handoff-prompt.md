# Claude Code handoff prompts

Transient. Delete this file once PLAN-4 and PLAN-5 are closed. The durable
instructions are the plans themselves; these prompts only point at them, which
is what makes them survive a context window running out mid-session.

Run these in order, one per session, in the repo root with the venv active.
Do not merge two sessions into one: each ends at a verifiable state, and the
value of the sequence is that a failure is attributable.

---

## Before session A: two things only a human can do

1. **Confirm billing is attached to the GCP project.** Console, Billing. If it
   is not attached, the project is a sandbox and every table in `raw_datasf`
   expires 60 days after creation. Attaching billing removes the expiry and
   leaves the free tier intact (10 GiB storage, 1 TiB scanned per month).
2. **Create a GCS bucket in `us-central1`, `us-west1` or `us-east1`.**
   Always-free storage applies only to those three regions: 5 GB-month
   Standard, 5,000 Class A and 50,000 Class B operations. Uniform bucket-level
   access. Grant the existing service account object admin on that bucket and
   nothing wider. Put the name in `.env` as `GCS_BUCKET`.

Session A does not need the bucket. Session B does.

---

## Session A. Prove the BigQuery target

Paste this:

```
Read CLAUDE.md, then docs/README.md, then docs/plans/plan-4-cloud-first-storage.md.

Execute PLAN-4 steps 3 and 4 only. Stop after step 4 and report.

Step 3 is the whole point of this session: run `make load-bigquery` then
`make build-bigquery` against the local zone exactly as it stands, change
no scope and no models first, and then compare stg_datasf__311_cases row
for row between DuckDB and BigQuery. Use an order-independent content hash
of the full model on both engines, the same technique the 2026-07-31 dev
note used to verify the rebuild. If the hashes disagree, find the column
that differs, fix it with a dispatch macro in dbt/macros/cross_engine.sql
per ADR-1, and re-verify. Do not tolerate a diff and do not narrow the
comparison to make it pass.

Then step 4: drop the four orphaned raw_ tables in raw_datasf. They came
from the pre-ADR-4 code path, are not reproducible from the Parquet zone,
and are 6.44 GB against a 10 GiB free-tier ceiling. Confirm with me before
dropping anything.

Load credentials with `set -a; source .env; set +a`.

Append a dev note to docs/dev-notes/2026-07-31.md (or today's file if the
date has rolled) recording: whether billing is attached, the two hashes,
what disagreed if anything, and the BigQuery storage before and after.
Record it whether it passes or fails. A failure here is the most useful
result this repo has produced in weeks.

Do not commit or push. Leave everything in the working tree and tell me
what is there.
```

**Checkpoint before session B:** the dev note says the two engines agree, or
says exactly how they disagree and what fixed it. `raw_datasf` is empty of
materialized raw tables. This closes PLAN-1 step 4, which has been open since
the plan was written.

---

## Session B. Move the raw zone to GCS and make BigQuery read it

Paste this:

```
Read CLAUDE.md, docs/plans/plan-4-cloud-first-storage.md, and the most
recent file in docs/dev-notes/.

Execute PLAN-4 steps 5 through 11.

Answer the plan's first open question before writing any code: does
DuckDB's httpfs read gs:// with the service account directly, or does it
need HMAC interoperability keys? Test it in a scratch script and tell me
the answer, because it changes what .env.example has to document.

Constraints that are not negotiable here:
- read_sql() in ingestion/raw_zone.py stays the single reader. Add the
  remote case to it; do not add a second reader.
- `make ci-build` must still pass with no credentials and no bucket. Run
  it before you finish. If the change makes CI need a bucket, the change
  is wrong and should be redesigned, not worked around.
- Models do not change. source('raw_datasf', 'raw_311_cases') resolves the
  same on both engines whether the underlying table is materialized or
  external.

Step 10 is a real ADR, not a note. ADR-9 supersedes ADR-4, because ADR-4's
"loading replaces rather than appends" stops being true on BigQuery and
docs/README.md has no vocabulary for a partial supersede. Restate what
carries forward: the directory layout, the run manifests, the watermark
coming only from the zone, and the (:updated_at, :id) ordering. Set ADR-4
to status: superseded with a related pointer and change nothing else in
it. Follow docs/decisions/TEMPLATE.md, and argue the options you rejected
as seriously as the existing ADRs do.

Then close PLAN-1: status done, steps 4 and 5 ticked.

Update CLAUDE.md, the Makefile header and ingest.py's docstring, all three
of which currently describe data/raw as the durable zone. It is a cache
now.

Do not commit or push.
```

**Checkpoint:** a clone with no `data/` directory runs `make load && make
build`. `ingest.yml` has no cache step. One scheduled run has gone green.

---

## Session C. Cut the two datasets

Paste this:

```
Read CLAUDE.md and docs/plans/plan-5-narrow-and-polish.md, including the
"Scope decisions to record in ADR-10" section, which contains the
reasoning and should not be re-litigated.

Execute PLAN-5 steps 1, 2 and 3: cut city_budget, cut street_trees, and
drop H3 resolution 9. Keep film_locations.

Each cut is end to end. The plan lists the files. Miss one and either
`make ci-build` fails on a dangling ref, or worse, it passes with a source
nothing reads.

Two things to check rather than assume:
- After cutting street_trees, is the flat lat/lon geometry path still
  exercised? film_locations is the only remaining dataset using
  {"latitude": ..., "longitude": ...} rather than a GeoJSON point. If its
  fixture does not carry the adversarial coordinate cases that trees
  supplied, move them across before deleting the trees fixture.
- mart_activity_by_h3 is documented as resolution 9 and has to move. Count
  the rows at r8 and at r10 before choosing, and put the numbers in the
  dev note. It is 264,802 rows at r9 and is the largest published artifact.

Delete data/derived entirely and re-run `make spatial` rather than trying
to migrate the stored cells.

Finish with `make check` green and a dev note recording the row counts
before and after, and the derived zone size before and after.

Do not commit or push.
```

---

## Session D. Cover the Python and collapse the duplication

Paste this:

```
Read CLAUDE.md and docs/plans/plan-5-narrow-and-polish.md.

Execute PLAN-5 steps 4 through 11.

Do step 5 before step 6. The pytest suite on ingestion/geometry.py is what
tells you the spatial.py split preserved behaviour; splitting first means
refactoring 883 lines with no safety net. geometry.py is 280 lines of
hand-rolled point-in-polygon and area and is the highest-risk untested
code in the repo.

For step 4, the one-registry change: the test is not that the duplication
is tidier, it is that dbt_project.yml and the Python registry cannot
disagree silently. If your design still allows that, it has not worked.

Step 10 is one ADR covering both the scope cut and the resolution cut,
superseding ADR-7 and ADR-5. Not two ADRs. This plan exists partly to
reduce the document count and answering it with four new records defeats
it.

Finish with `make check` green, including the new make test-python, and
close PLAN-2 and PLAN-5.

Do not commit or push.
```

---

## Session E. The context pack

Only after PLAN-4 and PLAN-5 are both closed.

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
