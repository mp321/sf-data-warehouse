{#
    restrict_to_newest_snapshot: keep only the grain_keys the newest
    ingest_date holds.

    ADR-19 step 4, and the reason it exists is that a snapshot and a delta
    source want opposite reading rules from the same zone. Staging
    deduplicates by grain_key to the newest _socrata_updated_at, which is
    correct for an event log: a row that changed is republished, a row that
    did not is still valid, and the union of every partition is the dataset.
    A refresh: snapshot source is a current-state registry, where upstream
    republishes wholesale and ABSENCE IS INFORMATION. The city withdraws
    business locations with no tombstone row, so a key missing from the newest
    partition has left the registry, and a union over every partition ever
    written reads that withdrawal as "no update since" and keeps the record
    alive for as long as any partition holding it survives in the zone.

    Applied to every refresh: snapshot staging model rather than to
    business_locations alone, which is the one that forced it. The other four
    hold one partition each today, so this is a no-op on them, and it is a
    no-op that stops being one the first time any of them is ingested twice.
    A rule applied only where it currently bites is a rule that silently stops
    being applied.

    Arguments:
      upstream_cte     the CTE to filter, which is `deduplicated` and not
                       `source`. See the measurement below.
      grain_key        the registry's grain_key for this dataset: the raw
                       column name before staging renames it, and the same
                       column the dedup partitions by.
      source_relation  the source() relation itself, NOT the CTE that selects
                       from it. Also the measurement below.

    WHERE THIS GOES AND WHAT IT REFERENCES ARE BOTH PERFORMANCE DECISIONS, and
    both were measured on the bucket zone on 2026-09-12, against
    raw_business_locations at 8,414,422 rows over 22 partitions. The obvious
    way to write this is the slow way by a factor of twelve:

      restriction on `source`, before the dedup, subqueries on the `source`
      CTE                                                            9.08 s
      on `source`, before the dedup, subqueries on the relation      1.51 s
      on `deduplicated`, subqueries on the relation                  0.74 s
      no restriction at all, which is what this model was before     1.09 s

    Two independent effects. Naming the `source` CTE in all three places makes
    DuckDB materialise it once at full width, about forty columns of 8.4M
    rows, because a CTE referenced three times stops being a candidate for
    projection pushdown; naming the relation lets each subquery read the two
    columns it actually needs. And filtering after the dedup applies the
    semi-join to 366k rows rather than 8.4M, which is why the final form is
    CHEAPER than having no restriction at all.

    That mattered more than it looks. Staging models are views and
    int_point_activity is a view over them, so the chain is re-evaluated by
    every dependent: stg_datasf__business_locations alone has fifteen direct
    children, and a `dbt build` in the slow form took 9m18s against 34s.

    Filtering after the dedup is safe because the predicate is on the grain
    key and the dedup keeps exactly one row per grain key, so the two commute.
    The max() must still come from the relation and not from `deduplicated`:
    a key whose newest value lives in an older partition survives the dedup
    carrying that older ingest_date, so the newest partition's date is not
    guaranteed to appear in the deduplicated set at all.

    KEYS AND NOT ROWS, which is a distinction worth holding on to. This
    restricts which grain_keys survive; it does not restrict which partition a
    surviving key's values come from. A key present in the newest partition
    still takes its newest _socrata_updated_at across the whole zone, so if an
    older partition somehow holds a fresher version of a surviving record,
    that version still wins. That is ADR-19's Decision text read literally
    ("restricting snapshot staging models to the grain_keys present in the
    newest ingest_date") and it is the conservative half of the two readings
    available.

    THE OTHER READING IS NOT IMPLEMENTED HERE, AND SOMEONE WILL WANT IT. ADR-19
    also describes step 4 as making "an older partition genuinely unreachable",
    which is a stronger claim than this macro makes: reading ONLY the newest
    partition rather than restricting the key set to it. The two return the
    same rows whenever no surviving key is fresher in an older partition,
    which is the prune's second limb, measured 0 on every partition of every
    dataset to date. So they agree on the zone as it is, and they differ in
    what a later decision may rest on: with this macro an older partition is
    still read for values and is therefore NOT unreachable, so the deferred
    question of whether prune_raw.py may treat it as deletable does not follow
    from this change alone. ADR-19 declines that decision explicitly and asks
    for its own acceptance test. Do not read this macro as having taken it.

    ingest_date is a hive partition key in the zone layout and a queryable
    STRING column on both engines: DuckDB recovers it from the directory names
    with hive_types_autocast=0, and load.py gives BigQuery's external tables
    hive partitioning in STRINGS mode with ingest_date in the explicit schema.
    It sorts lexically because it is ISO-8601, which is what makes max() the
    newest partition rather than merely the last one alphabetically. After
    this macro ingest_date stops being partition bookkeeping and becomes a
    semantic column the models depend on, which is the lock-in ADR-19 names:
    the raw zone layout can no longer change without changing what these
    models return.
#}

{%- macro restrict_to_newest_snapshot(upstream_cte, grain_key, source_relation) -%}
select *
from {{ upstream_cte }}
where {{ grain_key }} in (
    select {{ grain_key }}
    from {{ source_relation }}
    where ingest_date = (select max(ingest_date) from {{ source_relation }})
)
{%- endmacro -%}
