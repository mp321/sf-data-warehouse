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

## Where things stand, 2026-08-06, fourth revision

**PLAN-5 and PLAN-7 are closed, and PLAN-6 is two thirds done.** Sessions A
through I are done. One session is left:

**J. CI and the closing ADR.** PLAN-6, `active`. Steps 1 to 3 are done. Step 1
was the spec on 2026-08-05; steps 2 and 3 were the generator on 2026-08-06, and
`make context-pack` now writes a DuckDB pack that 20 refusals, 6 disclosures and
6 verified examples deep. What is left is step 4, wiring the drift check into
CI, and the ADR that records the format and what was left out.

Session I built the generator against a spec written before it, which is worth
knowing because the next session inherits both. Where the code had to interpret
the contract it says so in the module header, and the plan step lists all five
departures. Two of them are open questions on the plan rather than settled
readings, and they are Session J's first decisions.

**Session I is uncommitted.** `e6281c8` is still the tip: PLAN-5 step 13,
PLAN-6 step 1 and PLAN-7 step 1 went in there and `origin/main` is level with
it. Everything Session I produced is in the working tree, which is the
arrangement CLAUDE.md's working agreement asks for. Session J should expect a
dirty tree and not a clean one.

The prompts for the sessions that have already run are deleted rather than
archived. What each one did is in `docs/dev-notes/` under the date it ran, and
what it decided is in the plan step or the ADR it produced. Anything from them
worth carrying into a future session is in "Standing notes" at the bottom of
this file.

Plan status lives in `docs/README.md` and is the thing to read first, not this
file.

---

## Session I. The context pack generator. Done 2026-08-06.

Deleted rather than archived, per the rule at the top of this file. PLAN-6 steps
2 and 3 are `tools/context_pack/`, `make context-pack`,
`make context-pack-check`, `tests/test_context_pack.py` and the committed
`context-pack/` artifacts. The plan step records what was built and its five
departures from the spec; the dev note records what the defect it was sent after
turned out to be, which was ten columns and not one.

---

## Session J. CI, and the ADR that closes PLAN-6

PLAN-6 **step 4 and the closing ADR**, plus two decisions the generator left
open. The last session in this file, and the one that deletes it.

```
Read CLAUDE.md, docs/plans/plan-6-context-pack.md including its two new
open questions, docs/specs/context-pack.md sections 2, 8 and 9, and the
second half of docs/dev-notes/2026-08-06.md, which is what the generator
session found.

Execute PLAN-6 step 4 and write the ADR that closes the plan.

Start by answering the plan's first open question, because step 4 is the
wrong shape until it is answered: which warehouse does CI check the pack
against? `make ci-build` builds from fixtures, so a pack generated there
carries fixture row counts and fixture example results, and committing
that would make the artifact describe something no consumer will read.
The plan sets out the two honest options and favours one; check the
reasoning rather than inheriting it.

Then wire the gate. `make context-pack-check` exits 3 on drift and
compares schema hashes and the prose revision, deliberately not row
counts. Both artifacts are committed, so the diff is the other half of
the signal.

The ADR records the pack format and, specifically, what was left out.
Section 10 of the spec is the list to start from. Add the five departures
in the plan's step 2, and settle the second open question in the ADR
rather than in code: section 9's rendering order omits the traps block, so
the markdown does not carry it. Confirm that reading or amend the spec and
say so in the dev note.

Two things not to reopen. The three-target decision is settled in spec
section 2. The generator's four build failures are settled and tested; do
not soften one into a warning to make CI pass.

If time is left, the published target is the cheap piece of remaining
work: pack_target.py declares its model set and freshness source already,
and what it needs is a connection factory over published/*.parquet and its
own entries in prose.yml. It is also the only way to find out whether the
three-pack argument survives contact with a second pack.

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
- **Naming a known defect in the prompt keeps the session from diagnosing it,
  and it does not stop the session finding the rest of it.** Session I was told
  that `mart_activity_by_h3.category` had no yml entry and to fix the yml rather
  than special-case the generator. It cost no diagnosis time, and the generator
  it produced then reported nine more columns in the same state, which is the
  outcome the instruction was aiming at: the tool finds the class, the prompt
  saves the session from rediscovering the instance.
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
