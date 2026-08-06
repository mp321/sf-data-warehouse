---
status: active
date: 2026-08-05
related: [adr-8-published-exports, adr-10-narrowed-scope, plan-5-narrow-and-polish, plan-6-context-pack]
---

# ADR-12. Published marts are one file each, not partitioned by month

Amends ADR-8 rather than superseding it. Everything else ADR-8 decided still
holds unchanged: local-first with an optional remote destination, marts only, a
manifest recording path, rows, bytes and a schema hash, the manifest uploaded
last, R2 and GCS by URI scheme, `published/` gitignored, and the export
exercised in CI on every pull request. This reverses one bullet, the one that
said "partitioned by month where a month exists".

## Context

ADR-8 chose hive partitioning by `event_month` so that "a consumer reading one
month scans one directory". It chose it against a guessed access pattern, and
said so: its own revisit clause asks for "a consumer actually appears and wants
something other than month partitioning, which is the point at which the
partition key is worth arguing about with a real access pattern rather than a
guessed one".

No consumer has appeared. What appeared instead was a bill in operations.

**One publish is 2,280 objects.** Against Google Cloud Storage's always-free
tier of 5,000 Class A operations a month, a daily publish leaves the tier on day
three, which breaks the zero-cost claim in the first paragraph of CLAUDE.md.
That is why `make publish` has been run by hand since 2026-08-01 and why
CLAUDE.md records the publish intent as "manual until the upload is batched". A
17 MB export took 6 minutes 39 to upload, because the cost is per object rather
than per byte.

**The cause is neither the H3 resolution nor the data volume.**
`business_locations` carries `location_started_at` values from 1849 to 2028, and
`mart_activity_by_h3` and `mart_activity_by_neighborhood` partition by
`event_month` over that whole range. Between them, 180,320 rows spread over 874
and 868 monthly partitions. The median partition of the larger mart holds 40
rows.

## Options considered

Measured on the real warehouse on 2026-08-05, for the two partitioned marts
only. The `partitioned_by` field, and the argument for declaring it rather than
inferring it, is unchanged in either case.

| layout | objects | bytes |
|---|---|---|
| by month, as built | 2,275 | 11.0 MB |
| by year | 248 | 4.2 MB |
| one file per mart | 2 | 1.9 MB |

**A. Partition by year.** Keeps the hive layout, so the concept survives and a
consumer can still prune. A 9x cut, and it leaves 248 objects for 4.2 MB, which
is still 17 KB a file and still above the 200 the plan asked for.

**B. Floor the mart's date range.** Rejected outright, and it is worth saying
why rather than leaving it on the list. 65.6% of `mart_activity_by_h3`'s rows
are dated before 2020. Cutting the range is not a partitioning fix, it is a
silent deletion of most of the business-locations history, and it belongs in a
scope ADR with its own argument if anyone wants it.

**C. One file per mart, no hive partitioning.** 2 objects, and 1.9 MB.

## Decision

Option C. Every entry in `PUBLISHED_MARTS` has `partition_by: None`.

**The size column is what settles it.** Month partitioning was not buying query
pruning at the cost of bytes; it cost 5.8x the bytes as well. A 5 KB Parquet
file is mostly footer, schema and dictionary pages, and compression has nothing
to work across. A layout that is worse on every measured axis is not a tradeoff,
and the axis it was meant to win on has no consumer asking for it. Within a
single file, Parquet row-group statistics still let a reader skip on
`event_month`, so the pruning it was chosen for is not entirely gone either.

**The field and the code path stay.** Partitioning is right when a partition is
large. Deleting the mechanism would make re-adding it a code change plus a
decision, where keeping it makes it a one-line decision. Nothing exercises the
partitioned branch today, which is a real cost and is recorded here rather than
argued away; PLAN-5 step 13 is the right place to decide whether it earns its
keep.

**This is a breaking change to the published layout, and it is recorded as
one.** `MANIFEST_VERSION` goes to 2. Paths change from
`mart_activity_by_h3/event_month=YYYY-MM-01 00:00:00/data_0.parquet` to
`mart_activity_by_h3/mart_activity_by_h3.parquet`. `partitioned_by` is null for
every dataset in the manifest, so a consumer that reads the manifest before the
files sees the change rather than getting an empty listing. ADR-8's lock-in
section anticipated exactly this: consumers key off paths, and
`manifest_version` exists so the layout can be changed deliberately.

## Consequences

**Buys.** One publish is 7 objects and 3.0 MB, against 2,280 and 16 MB. A daily
publish is 210 Class A operations a month against a free tier of 5,000, so the
zero-cost claim survives a cron with 24x of headroom, and the reason CLAUDE.md
gives for publishing by hand is gone. The export is also easier to consume:
`read_parquet(url)` works on a single file, where a hive-partitioned remote
directory needs a filesystem layer and a listing. PLAN-6's context pack gets the
simpler artifact.

**Costs.** The layout is a public interface and this breaks it, for whatever
read the one hand-run upload of 2026-08-01. The partitioned code path is now
unexercised, including in CI. And a consumer that wants exactly one month now
reads a 1.3 MB file instead of a 5 KB one, which is the loss, such as it is.

**Not decided here.** Whether `make publish` goes on a cron. This removes the
quota objection to it, and leaves the question to whoever wants the schedule.

## Revisit if

- A mart grows to the point where one file is awkward to read, which for these
  is roughly a hundredfold and is not near.
- A real consumer appears with a real access pattern, which is the condition
  ADR-8 set and which is still the right condition.
- The activity marts' date range is deliberately floored, which would change the
  numbers above but not the conclusion: 74 months at 2,400 rows each is still
  better as one file.
