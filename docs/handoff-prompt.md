# Claude Code handoff prompts

Transient. Delete this file once PLAN-4 and PLAN-5 are closed. The durable
instructions are the plans themselves; these prompts only point at them, which
is what makes them survive a context window running out mid-session.

Run these in order, one per session, in the repo root with the venv active.
Do not merge two sessions into one: each ends at a verifiable state, and the
value of the sequence is that a failure is attributable.

---

## Sessions A, B and B-remainder are done. Start at C.

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

The one thing left in PLAN-4 is outside a session's control: commit, add the
`GCS_BUCKET` repository secret, and let one scheduled `ingest` run go green.
Do that before starting C, because C changes the dataset registry and a red
nightly job is much harder to attribute afterwards.

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
