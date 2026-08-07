# Claude Code handoff prompts

Transient. PLAN-7 closed on 2026-08-05, so delete this file once PLAN-6 is
closed too and one session is all that is left in it. The durable
instructions are the plans themselves; these prompts only point at them, which
is what makes them survive a context window running out mid-session.

Run these one per session, in the repo root with the venv active. Do not merge
two sessions into one: each ends at a verifiable state, and the value of the
sequence is that a failure is attributable.

Sessions are sized for Claude Code on Opus 5 at extra effort, which is roughly
one substantial refactor plus its verification, or two or three small
mechanical changes plus theirs.

---

## Where things stand, 2026-08-05, third revision

**PLAN-5 and PLAN-7 are both closed.** Sessions A through H are done. One
session is left:

**I. The context pack.** PLAN-6, `active`. **Step 1 is done**, on 2026-08-05:
`docs/specs/context-pack.md`, and it answered the plan's open question as one
pack per target rather than one pack with a `distributions` block. What is left
is steps 2 to 4, the generator, the build gate and CI. Session I below is the
generator and not the spec.

Session H closed PLAN-7 later the same day: `ingestion/check_runs.py` and
`make check-runs` reconcile the raw zone's run manifests against the Parquet
they describe, in CI on the fixture zone and credential-free. Its open question
went against the PLAN-5 step 9 precedent, a separate file rather than a fourth
verdict in `check_derived.py`, and the dev note says why.

**Committed and pushed as `e6281c8`, 2026-08-06.** PLAN-5 step 13, PLAN-6 step
1 and PLAN-7 step 1 went in as one commit, `origin/main` is level with it, and
CI is green on it. So Session I starts from a clean tree and its diff will be
its own, which is the arrangement the rest of this file assumes.

The prompts for the sessions that have already run are deleted rather than
archived. What each one did is in `docs/dev-notes/` under the date it ran, and
what it decided is in the plan step or the ADR it produced. Anything from them
worth carrying into a future session is in "Standing notes" at the bottom of
this file.

Plan status lives in `docs/README.md` and is the thing to read first, not this
file.

---

## Session H. Pipeline assurance. Done 2026-08-05.

Deleted rather than archived, per the rule at the top of this file. PLAN-7 step
1 is `ingestion/check_runs.py`, `make check-runs` and a step in `ci-build` and
`ci.yml`; the plan step records what was built and where it departed from what
the step asked for, and the dev note records how it was demonstrated.

---

## Session I. The context pack generator

PLAN-6 **steps 2 onward**. Step 1 is done: `docs/specs/context-pack.md`, on
2026-08-05. The only session left, and the largest remaining piece of work in
the project.

```
Read CLAUDE.md, docs/plans/plan-6-context-pack.md, and all of
docs/specs/context-pack.md, which is the contract this session builds
against and was deliberately written before any generator.

Execute PLAN-6 step 2: the generator, at tools/context_pack/.

Read the spec in this order. Section 3 is the artifact shape. Section 7 is
the one hand-maintained YAML that holds the prose behind all three packs.
Sections 8 and 9 are the two rules that are easy to implement as warnings
by mistake, and both must fail the build:

- Every refusal and disclosure cites something that resolves against the
  target's model set, and generation fails when a citation names a model,
  column or measurement that target does not have.
- Refusals are never trimmed to fit the token budget. The generator drops
  examples, then column descriptions, then profile statistics, in that
  order, and fails rather than emitting a pack with a refusal missing.

The open question is settled and does not need reopening: one pack per
target, three self-contained artifacts, one YAML behind them. Section 2 of
the spec has the argument. The premise that killed the single-pack version
is that the three surfaces do not hold the same models, not freshness.

The DuckDB pack is the one to build first and the only one this session
needs to produce: it needs no credentials, so it is the one CI can gate on.

One known defect to expect rather than diagnose: mart_activity_by_h3 has a
category column in the SQL and in its unique_combination test and no entry
in _marts__models.yml, so the generator will surface a column with no
description. Fix the yml, do not special-case it in the generator.

Do not commit or push.
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

Not a plan step, and not an agent's call. One item, down from three: the
scheduled run on 2026-08-06 closed the other two, and they are kept below
struck through because the next reader will otherwise wonder where they went.

**Two orphaned BigQuery external tables.** `raw_datasf.raw_city_budget` and
`raw_datasf.raw_street_trees`, created 2026-08-04 11:40 UTC, from before ADR-10
cut both datasets. Nothing references them and they cost no storage, since
external tables hold no bytes, but `make parity-columns` warns about them by
name every run. Dropping them is two `bq rm` calls. Deleting the Parquet
underneath them is a separate decision about the bucket, and both belong to a
human rather than to an agent.

~~**The bucket's derived zone still carries no code stamp.**~~ **Closed
2026-08-06 by the cron, not by hand.** The scheduled run's `spatial.py` printed
"the zone records no code version, so it was built before the stamp existed"
and rebuilt the whole derived zone against the bucket: 523,339 point cells,
84,296 polygon cells, 1,332,896 point-boundary rows, 39,301 population cells,
24,000 pip-sample answers. The bucket's zone now carries a stamp, so
`make check-derived` against it no longer exits 4 on sight. Nothing was owed to
a human after all; the item existed for one day.

~~**`main` now carries the current pipeline, and no cron has run from it yet.**~~
**Closed 2026-08-06: it fired and it was green.** Run 31097820662, `event:
schedule`, on `e6281c8`, 11:34 to 11:41 UTC. It ingested into the bucket's raw
zone (30,731 new 311 cases, 1,708 permits, 415,006 business locations across
eight files) and then ran the spatial step above. That is the first end-to-end
verification of the scheduled path against the bucket, which every prior
session's note said was still owed. The bucket's zones are current as of that
run, and the local `data/` zones are not the same zone and did not move.
