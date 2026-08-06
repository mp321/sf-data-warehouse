# Claude Code handoff prompts

Transient. Delete this file once PLAN-6 and PLAN-7 are closed. The durable
instructions are the plans themselves; these prompts only point at them, which
is what makes them survive a context window running out mid-session.

Run these one per session, in the repo root with the venv active. Do not merge
two sessions into one: each ends at a verifiable state, and the value of the
sequence is that a failure is attributable.

Sessions are sized for Claude Code on Opus 5 at extra effort, which is roughly
one substantial refactor plus its verification, or two or three small
mechanical changes plus theirs.

---

## Where things stand, 2026-08-05

**PLAN-5 is closed.** Sessions A through G are done. What is left is two
sessions, and they are independent of each other:

1. **H. Pipeline assurance.** PLAN-7, and it is **step 1 only**: step 2 was
   done on 2026-08-05 against a live defect and is
   `scripts/parity-check.py --columns` / `make parity-columns`.
2. **I. The context pack.** PLAN-6, which is `draft` and unstarted. The
   largest remaining piece of work in the project and the most distinctive.

The prompts for the sessions that have already run are deleted rather than
archived. What each one did is in `docs/dev-notes/` under the date it ran, and
what it decided is in the plan step or the ADR it produced. Anything from them
worth carrying into a future session is in "Standing notes" at the bottom of
this file.

Plan status lives in `docs/README.md` and is the thing to read first, not this
file.

---

## Session H. Pipeline assurance, step 1 only

PLAN-7 **step 1 alone**. Step 2 is done; do not re-do it. This is roughly half
the session it was, and it is the last unchecked claim in that plan.

```
Read CLAUDE.md, docs/plans/plan-7-pipeline-assurance.md,
ingestion/check_derived.py, and the "PLAN-7 step 2" part of the second
section of docs/dev-notes/2026-08-05.md, which is the sibling check and set
the pattern this one should follow.

Execute PLAN-7 step 1 only. Step 2 is done; the plan says so and says where.

Step 1's open question is the first thing to settle and it is a design
question, not a preference: does the manifest reconciliation belong in
check_derived.py, which already exists to assert one zone is not behind
another, or in a new script? Look at that file before writing anything.
Two scripts asserting neighbouring invariants may well be one script.

Note that check_derived.py has moved since PLAN-7 was written. It now grades
three verdicts rather than two, STALE, DRIFT and RECODED, and reads three
records out of the derived manifest through ingestion/derived_state.py. The
third invariant PLAN-7 step 1 describes as unchecked, a zone built by code
that no longer exists, was closed on 2026-08-05 by ADR-11. Read the plan
against the code before taking its framing.

Unlike step 2, this check must run in CI on the fixture zone, so it needs no
credentials and must not reach for a bucket. That is the constraint that
separates the two halves of this plan.

The check must fail loudly and name what disagreed. A check that reports
"mismatch" without saying which dataset and which number is a check nobody
will trust at 2am. Step 2's output is the standard to match: it names the
dataset, the table and every column on either side.

Do not commit or push.
```

---

## Session I. The context pack

PLAN-6. Independent of session H, and the more interesting of the two.

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

The plan's own open question is the one to answer first, because it is most
of the format decision: does the pack describe the DuckDB warehouse, the
BigQuery one, or the published Parquet? They have the same models and
different freshness, and a consumer reading the bucket is reading none of
the three.

Show me the spec before writing a single line of the generator.
```

---

## Standing notes for every session

**Committing.** CLAUDE.md forbids an agent from running `git commit`, `git
push`, `git add` or anything that writes history, and that rule is doing real
work here: it is why the whole 2026-07-31 session survived as a reviewable
diff. If you want Claude Code to commit itself, say so explicitly in that
session and amend CLAUDE.md to match, rather than leaving the file saying one
thing and the session doing another.

**When a session runs long.** Stop at the last completed plan step, append the
dev note, and start a fresh session. The plans are written so a new context
window can pick up from a step number.

**`make check` cannot see half of this project, and that is by design.** It is
DuckDB-only and local-zone-only, which is what keeps it credential-free for a
fork pull request (ADR-1). The consequence, learned on 2026-08-05: a green
`make check` says nothing about the bucket zones or about BigQuery, and both
errors that session found were invisible to it. If a session touches the
registry, the zone layout, a staging model's column list or `load.py`, add
`make build-bigquery` to its checkpoint.

Two cheaper credentialed checks come before that one, and it is worth knowing
which answers which question:

- `make parity-columns` compares the zone's column sets against the BigQuery
  tables. Needs no local build at all, so it runs straight after
  `make load-bigquery`, and it is the fast way to know an external table is
  still a view of the whole zone.
- `make parity-check` compares staging models row for row. **Both engines have
  to have been built from the same zone or it reports a configuration
  difference as a defect.** There is one zone at a time (CLAUDE.md), and a
  DuckDB file loaded from `data/raw` against BigQuery reading the bucket will
  differ on every count. Use `DUCKDB_PATH=/tmp/whatever make load build` in the
  shell that has sourced `.env`, which puts both sides on the bucket zone and
  leaves your local warehouse alone.

**Two things about writing these prompts, both of which paid for themselves.**

- **Make the session confirm the defect before it fixes it.** Session
  E-remainder was told to confirm a specific bad value before rebuilding a
  zone, and the value was gone: the zone had already been fixed and the note
  recording the diagnosis had not been amended. One query saved a pointless
  multi-minute rewrite of a correct zone. Worth writing into any prompt whose
  fix is destructive or slow.
- **Point a session at the specific docstring that constrains the change.**
  The same session was told to read `_external_table`'s docstring first. Three
  details in it were load bearing and the fix had to preserve all three while
  adding a fourth. Naming the constraint is cheaper than letting a session
  rediscover it by breaking it.

**If a step turns out to be wrong.** These plans are intent, not law. If
something in them does not survive contact with the code, say so, write the
disagreement into the dev note, and change the plan. That is what
`docs/README.md` means by a plan being mutable until it is done.

---

## Open operational items

Neither is a plan step, and neither is an agent's call.

**Two orphaned BigQuery external tables.** `raw_datasf.raw_city_budget` and
`raw_datasf.raw_street_trees`, created 2026-08-04 11:40 UTC, from before ADR-10
cut both datasets. Nothing references them and they cost no storage, since
external tables hold no bytes, but `make parity-columns` warns about them by
name every run. Dropping them is two `bq rm` calls. Deleting the Parquet
underneath them is a separate decision about the bucket, and both belong to a
human rather than to an agent.

**`main` now carries the current pipeline, and no cron has run from it yet.**
As of 2026-08-05 `origin/main` is level with the work: `dataset_registry.py`,
the four-file `spatial.py`, the derived zone code stamp, and an `ingest.yml`
that sets `RAW_ZONE_URI` and `DERIVED_ZONE_URI` from the `GCS_BUCKET` secret
and runs the spatial step. This was not true before that merge, and the
practical consequence has flipped: the daily 09:17 UTC cron will now do what
`ingest.yml` says rather than writing to a runner's disk and evaporating. It
has not fired since the merge, so the first scheduled run is still unobserved.
Check it before treating the bucket's zones as current.
