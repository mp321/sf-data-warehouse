---
status: active
date: 2026-08-09
related: [adr-4-raw-zone-layout, adr-9-cloud-raw-zone, adr-14-raw-zone-retention, adr-16-cut-datasets-leave-the-zone, plan-9-raw-zone-retention, plan-7-pipeline-assurance]
---

# ADR-17. The proof runs on a schedule and the deletion never does

Amends ADR-14 and reverses nothing in it. ADR-14 answered PLAN-9's first open
question, where does the prune run, with "by hand, not on a schedule", and named
the cost: "the zone is now bounded by someone remembering. Revisit when the zone
is bounded by something other than someone remembering." This is that revisit,
two days later, and it splits the question ADR-14 answered as one. **A cron may
not delete data here, and that half of ADR-14 is not reopened.** ADR-14 is left
`active` rather than superseded, for the reason `docs/README.md` gives about
ADR-5.

## Context

**The evidence arrived on its own and it is two days long.** ADR-14 was decided
on 2026-08-07 with the raw prefix pruned to 214.1 MB. Nobody ran the prune again.
On 2026-08-09 the raw prefix was 323.4 MB over 216 objects: the daily
`ingest.yml` cron of 08-08 and 08-09 had each written another full 49.7 MB copy
of `business_locations`, and both were already provably superseded by the time
anyone looked. 99.3 MB in 18 objects, sitting in a zone that had a working tool
for removing it and no one to type the command.

That is not a new failure mode. It is the cost ADR-14 wrote down, arriving on
schedule and rather faster than the prose implied. The dev note of 2026-08-07
put the 5 GB allowance at about 977 days with the prune running and about 87
without it; two days of unattended growth put the neglected rate at 54.6 MB/day,
which lands on 86 days. The estimate was right and the thing it was an estimate
of was happening.

**What ADR-14 got right and what it bundled.** Its case against a scheduled
delete is untouched by any of this: a cron that deletes data is a different risk
appetite from a cron that writes some, and the prune's design is that a human
reads a refusal. The bundling is the error. "Where does the prune run" is two
questions with different answers, because `prune_raw.py` does two things and only
one of them is destructive:

- **the proof**, a read-only query over two columns of two partitions per
  candidate, which reports and exits 3 when it cannot prove something; and
- **the deletion**, `--apply`, which is every risk ADR-14 described.

Scheduling the first is not a smaller version of scheduling the second. It is
the thing that makes the second get run.

## Options considered

**A. Leave it by hand, as ADR-14 decided.** The status quo, and it has the
virtue that the person who deletes has read the report. Rejected on the
measurement above: it is not a bound, it is a hope, and the two days since
ADR-14 are the experiment. A retention policy that depends on someone
remembering has the same failure mode as the derived zone before ADR-11 and the
published prefix before PLAN-9 step 6, both of which this project has already
fixed once by making something say so out loud.

**B. `--apply` in `ingest.yml`, after the ingest step.** Bounds the zone with
nobody remembering anything, and it is the answer ADR-14 spent a paragraph
refusing. Still refused, and the two days of evidence do not touch the refusal:
what they show is that nobody was watching, which is the worst condition under
which to have a scheduled job deleting data. The proof exits 3 precisely when
something is wrong, and an exit code nobody reads either wedges the job or gets
ignored. Adding a delete to a job whose green light already means "ingestion
worked" also overloads that signal.

**C. A bucket lifecycle rule.** Not reopened. ADR-14 option A, rejected because
it deletes by object age and would destroy 311 and building permit history.
Putting the *proof* on a schedule is not a step toward this and must not be read
as one; it is the reason only the proof is scheduled.

**D. Schedule the proof, keep the deletion by hand, and fail loudly when the
zone is past a threshold.** Chosen.

## Decision

`.github/workflows/retention.yml`, weekly at 11:29 UTC on Mondays, runs
`python ingestion/prune_raw.py --max-bytes 1000000000`. **There is no `--apply`
in that file and there must not be.** `make prune-raw-apply` stays a human's
command, run after reading the report, exactly as ADR-14 decided.

**`--max-bytes` is the new surface and it deletes nothing.** It weighs the whole
raw zone, prints it against the threshold and against what an apply of the plan
just reported would leave, and exits 4 if the zone is over. Off unless given,
because the threshold is a property of one bucket's allowance and not of the
tool: a local zone has no allowance to be measured against.

**The threshold is 1 GB against a 5 GB allowance, and the number is chosen so a
breach means one thing.** With the prune being run the zone sits near a 170 MB
floor, set by the keep window of two partitions of the largest snapshot dataset,
and grows at the delta rate of 4.86 MB/day; delta growth alone needs over 170
days to reach 1 GB. Without the prune the snapshot churn is about 50 MB/day and
reaches it in roughly 15. **So this cannot fire because the project grew. It
fires because the apply stopped being run**, which is the only sentence it is
trying to say. It still leaves about 70 days of runway to the allowance at the
neglected rate, which is three more Mondays.

**Exit 3 outranks exit 4 when both hold**, and that ordering is load bearing
rather than tidy. Over budget asks the reader to run the apply. Unproven says a
snapshot dataset failed its supersession proof, which ADR-14's revisit clause
reads as `refresh` having become a lie and answers with "stop pruning until it
is understood". Reporting the cheaper verdict as the headline would invite
exactly the action the more serious one forbids. The workflow prints which
verdict it got and what to do about it, so the reader does not have to open the
log to find out.

**Weekly and not daily.** The proof costs a scan of two partitions per candidate,
about 34 seconds against the bucket, and daily would put a job that can go red
in front of a human six times more often than it has anything new to say. Three
checks inside the runway window is enough, and a check that cries wolf is the
failure this whole design is shaped around avoiding.

**What this does not claim.** It does not bound the zone. It bounds how long the
zone can be unbounded without someone being told, which is a weaker and honest
thing. If nobody reads the red X, the zone still fills; the difference is that
the record then shows three ignored failures rather than nothing at all.

## Consequences

**Buys.** The cost ADR-14 named and accepted is paid down: the zone is bounded
by a check with about 70 days of runway rather than by someone remembering. The
proof also now runs weekly whether or not anything is prunable, so a snapshot
dataset that stops being superseded is found by a job rather than by whoever
next happens to type `make prune-raw` - and ADR-14's first revisit trigger is
exactly that condition.

**Costs.** A scheduled workflow that can go red is an operational surface, and a
red X that means "housekeeping is due" is the kind that gets muted. That risk is
priced in the threshold rather than argued away: at 1 GB it fires roughly once
per three weeks of neglect and not weekly. It also spends credentials on a
schedule for a read-only job, which is a third workflow holding `GCP_SA_KEY`.
And the tool now has a mode that is nothing to do with pruning, which is the
usual way a focused script stops being one; `--max-bytes` earns it by being the
number the whole of PLAN-9 was actually about.

**Lock-in.** The threshold is a constant in a workflow file rather than derived
from the allowance, so a bucket with a different allowance or a project with a
second large snapshot dataset needs it re-derived. The arithmetic for doing that
is in this ADR and in the workflow's own comment, which is where it belongs
rather than in a config file nothing reads.

## Revisit if

- The check fires and is ignored twice. That means the red X is not the signal
  it was meant to be, and the answer is either a notification that reaches a
  person or a reconsideration of option B with the risk stated plainly, not a
  higher threshold.
- The 170 MB floor moves materially, by a snapshot dataset joining the registry
  or `business_locations` growing. The threshold's whole claim is that delta
  growth cannot reach it, and that claim is arithmetic over the floor.
- Anyone proposes `--apply` in a workflow. That is ADR-14's decision and this
  ADR's, twice refused, and it should be reopened by an ADR rather than by a
  line added to a YAML file.
- The zone is over the threshold with nothing prunable. The prune is not the
  lever in that case and neither is this check; the tool says so and points at
  ADR-14's revisit clause.
