{% raw %}
-- assert_snapshot_staging_holds_only_the_newest_partition
--
-- ADR-19's decision, as an invariant the build can hold: the newest
-- ingest_date of a refresh: snapshot dataset IS the dataset, so its staging
-- model returns the grain_keys that partition holds and nothing older.
--
-- **What it catches.** The city withdraws records from business_locations: a
-- location that closes leaves the registry with no tombstone row and no
-- status transition, so it is simply absent from the next bulk refresh.
-- Staging used to union every partition and deduplicate to the newest
-- _socrata_updated_at, which is the right rule for a delta source and the
-- wrong one for a snapshot, and the effect was that a withdrawn record
-- survived in the marts for as long as any partition holding it survived in
-- the zone. Measured 2026-09-07 and again 2026-09-12: 27 records the city's
-- registry no longer held.
--
-- **Why counts are enough, which is not obvious.** This compares two numbers
-- per dataset rather than two sets, and equal cardinality does not usually
-- prove two sets are equal. It does here, because the subset direction is
-- guaranteed by construction on the other side: restrict_to_newest_snapshot
-- puts a WHERE on the staging model that cannot admit a key the newest
-- partition lacks, and every staging model below is one row per grain_key
-- under its own uniqueness test. Subset plus equal size is equality. Before
-- the macro the subset direction was the thing that was false, and the count
-- caught it in the only direction it could go: staging held MORE keys than
-- the newest partition, never fewer.
--
-- So this is a test of the macro being applied, not of set arithmetic. Delete
-- the macro call from one model and this fails on that model alone.
--
-- **Driven off the dataset registry** (vars.pipeline_sources in
-- dbt_project.yml), so a sixth snapshot dataset is covered the day it is
-- registered rather than the day someone remembers this file. Delta datasets
-- are excluded because a delta partition holds only what changed since the
-- watermark: its newest partition is meant to be a fraction of the dataset,
-- and asserting otherwise would fail every build.
{% endraw %}

{% set snapshots = var('pipeline_sources')
    | selectattr('refresh', 'equalto', 'snapshot')
    | sort(attribute='name')
    | list %}

with counted as (

    {% for dataset in snapshots %}
    select
        '{{ dataset.name }}' as dataset,
        (
            select count(*) from {{ ref(dataset.staging_model) }}
        ) as staging_rows,
        (
            select count(distinct {{ dataset.grain_key }})
            from {{ source('raw_datasf', dataset.table) }}
            where ingest_date = (
                select max(ingest_date) from {{ source('raw_datasf', dataset.table) }}
            )
        ) as newest_partition_keys
    {%- if not loop.last %}

    union all
    {% endif %}
    {% endfor %}

)

select
    dataset,
    staging_rows,
    newest_partition_keys,
    staging_rows - newest_partition_keys as extra_rows
from counted
where staging_rows != newest_partition_keys
