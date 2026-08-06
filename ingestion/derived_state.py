"""What the derived zone was built from, and whether that is still true.

The derived zone is a pure function of the raw zone plus the code that computes
it (ADR-5, CLAUDE.md). That sentence is the project's strongest storage claim
and it was, until PLAN-5 step 9, unenforced in one direction: the zone recorded
what raw data it had seen and nothing at all about the code. This module holds
the three records that close it, because `spatial.py` writes all three and
`check_derived.py` reads all three, and a record with two implementations is
the failure PLAN-5 step 4 spent a session removing.

    raw_input_state   per raw table, the deduplicated row count and watermark.
                      Answers "has the raw zone moved". The oldest of the
                      three; STALE and DRIFT in check_derived.py are its two
                      verdicts.
    partition_state   per raw table, per ingest_date partition, rows and files.
                      The same question one level finer, and the level a
                      rebuild can act on: it says which partitions are new
                      rather than only that something is.
    code_version      a stamp over the source of every module that decides the
                      zone's contents, plus the resolutions and table lists in
                      readable form. Answers "was this zone built by code that
                      still exists", which no row count can.

**Why the code stamp is not a nicety.** On 2026-08-05 the bucket's derived zone
was found holding H3 r9 cells that ADR-10 had removed from the code the day
before. Nothing detected it: `make check` is DuckDB-only and local-zone-only,
and `check_derived.py` compared row counts, which agreed, because the raw zone
had not moved and only the code had. It surfaced as an `accepted_values`
failure in `make build-bigquery`, several steps downstream of the cause. A
schema change in the derived zone is invisible to a row count, so the stamp is
the guard for a class of failure the other two records structurally cannot see.

**And why it is written into the zone rather than computed on demand.** Later
the same day, the session that went to fix that zone found it already correct,
and could not establish from the zone whether it had been rebuilt or had never
been wrong. That took GCS object mtimes and a cell-count comparison against the
local zone, which is forensics rather than a check. With the stamp and the
per-table `built_at` beside it, "this zone is correct now" and "this zone was
never wrong" are different readings of the manifest, and a fix is attributable
to a run.

**The stamp is a hash of the source, not a constant someone bumps.** Both were
on the table and the choice is asymmetric rather than close. A version constant
is precise: it fires when the author decides the output changed and stays quiet
through a comment edit, so it never triggers a rebuild that changes nothing. It
also fails open. Someone will change `RESOLUTIONS` and forget to bump it, and
the failure mode is exactly the one above, silent and undetectable, which is
the failure this stamp exists to prevent. A file hash fires on a comment
change, which costs one full rebuild, and on the local zone a full rebuild is
about 24 seconds. Twenty-four seconds against a class of undetectable
wrongness is not a trade worth thinking about twice, so this hashes the source.

The cost is worth stating plainly, because someone will hit it and think it is
a bug: **editing a comment in any of the modules below invalidates the whole
derived zone.** It is not a bug, it is the price of the stamp being automatic,
and the stamp's readable fields exist so the next reader can see that the
resolutions and tables did not change even though the hash did.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import raw_zone
from boundaries import MEMBERSHIP_RESOLUTION
from dataset_registry import DATASETS, point_datasets, polygon_datasets
from derived_zone import CODE_VERSION_KEY, PARTITIONS_KEY, RAW_INPUTS_KEY
from h3_points import RESOLUTIONS, dedup_sql

MODULE_DIR = Path(__file__).resolve().parent

# Every module whose source decides what ends up in the derived zone. Over-wide
# on purpose: `geometry.py` never writes a row but every boundary assignment
# rests on it, and this file never computes one but decides which get
# recomputed. A module wrongly on this list costs a rebuild nobody needed; one
# wrongly off it costs a zone nobody can tell is wrong.
STAMPED_MODULES = (
    "spatial.py",
    "h3_points.py",
    "boundaries.py",
    "population.py",
    "geometry.py",
    "derived_state.py",
)

# Reading last run's derived_point_h3 back costs about what recomputing it
# costs: measured 2026-08-05 on the local zone, 0.85 seconds to read 506,632
# cached rows against 0.95 seconds to recompute them from the raw zone. So
# reading the cache and merging is a saving only when the partitions that
# changed hold a small share of the table, and a loss when they hold most of
# it. At or above this share, rebuild the table instead. A daily partition
# against a year of history is far below it; the first run after a backfill is
# above it and should be.
INCREMENTAL_SHARE = 0.5


# ---------------------------------------------------------------------------
# What the raw zone held when this ran
# ---------------------------------------------------------------------------


def raw_input_state(con, root: Path | str | None) -> dict:
    """What the raw zone held for every dataset the spatial step reads.

    Per raw table: the deduplicated row count and the newest watermark. The
    count goes through `dedup_sql`, so it is the number of rows a staging
    model has, not the number of files' worth of appends behind them, and a
    later comparison against it means "does the derived zone still cover every
    row that exists" rather than "has anything been appended".

    Recorded for polygon datasets too. A new neighbourhood boundary does not
    leave a point without geography, so nothing downstream fails loudly, but
    every assignment in the zone was computed against the old boundaries and
    is silently one version behind. That is worth reporting.

    Lived in `spatial.py` until PLAN-5 step 9, on the argument that
    `check_derived.py` imports it rather than reimplementing it and moving it
    would have proved nothing. Step 9 gave it two siblings with the same
    property, and the checker now has three records to read instead of one, so
    the module both sides import is the honest home for all three. It also
    means the checker no longer imports `spatial.py` to check `spatial.py`.
    """
    state: dict = {}
    for cfg in {**point_datasets(), **polygon_datasets()}.values():
        table = cfg["table"]
        if not raw_zone.has_data(table, root):
            continue
        inner = (
            f"select {cfg['grain_key']} as row_key, _socrata_updated_at, _ingested_at "
            f"from {raw_zone.read_sql(table, root)}"
        )
        rows = con.execute(f"select count(*) from ({dedup_sql(inner, 'row_key')})").fetchone()[0]
        state[table] = {
            "rows": rows,
            "watermark": raw_zone.read_watermark(table, root),
        }
    return state


def partition_state(con, root: Path | str | None) -> dict:
    """Per raw table, what each `ingest_date` partition holds now.

    The same question `raw_input_state` asks, one level finer and one level
    cheaper: it comes out of the Parquet footers rather than a deduplicating
    scan. Finer is what makes it actionable, since a partition is the unit a
    rebuild can skip, and `raw_input_state` can only say that something moved.
    """
    return {
        cfg["table"]: raw_zone.partition_state(con, cfg["table"], root)
        for cfg in {**point_datasets(), **polygon_datasets()}.values()
        if raw_zone.has_data(cfg["table"], root)
    }


# ---------------------------------------------------------------------------
# What computed the zone
# ---------------------------------------------------------------------------


def _module_digests() -> dict[str, str]:
    """Source digest per stamped module. Read as bytes, never imported.

    A checker asking "what built this zone" must not have to run, or even
    import, the code it is asking about. Reading the files means it still
    answers on a `spatial.py` that does not currently parse, which is exactly
    the moment someone wants to know what the zone was built from.
    """
    return {
        name: hashlib.sha256((MODULE_DIR / name).read_bytes()).hexdigest()[:12]
        for name in STAMPED_MODULES
    }


def _registry_projection() -> list[dict]:
    """The part of the dataset registry that changes what the zone contains.

    Not the whole registry. `tier`, `stale_after_hours` and `description` are
    read by dbt and by nothing here, so folding them in would invalidate the
    zone on a freshness threshold edit. What is here is what `spatial.py`
    dispatches on: which tables, points or polygons, keyed how, with the
    coordinates or the polygon read from where.
    """
    return [
        {
            "table": cfg["table"],
            "kind": cfg["kind"],
            "grain_key": cfg["grain_key"],
            "geometry": cfg["geometry"],
        }
        for _, cfg in sorted(DATASETS.items())
    ]


def code_version() -> dict:
    """The stamp, plus the readable fields that say what changed when it moves.

    The stamp alone can only report "different". The fields beside it are what
    let a checker say "this zone was built for resolutions 8, 9 and 10 and the
    code computes 8 and 10", which is the r9 failure of 2026-08-05 named at its
    cause rather than four models downstream of it.
    """
    modules = _module_digests()
    payload = json.dumps(
        {
            "modules": modules,
            "registry": _registry_projection(),
            "resolutions": list(RESOLUTIONS),
            "membership_resolution": MEMBERSHIP_RESOLUTION,
        },
        sort_keys=True,
    )
    return {
        "stamp": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
        "resolutions": list(RESOLUTIONS),
        "membership_resolution": MEMBERSHIP_RESOLUTION,
        "point_tables": sorted(cfg["table"] for cfg in point_datasets().values()),
        "polygon_tables": sorted(cfg["table"] for cfg in polygon_datasets().values()),
        "modules": modules,
    }


def describe_code_change(recorded: dict | None, current: dict) -> list[str]:
    """Why the zone's code version and the code differ. Empty when they agree.

    Reports the readable fields first and the module digests last, because a
    resolution list that moved is a reader's answer and a changed digest is
    only a pointer to the file to look at.
    """
    if recorded is None:
        return ["the zone records no code version, so it was built before the stamp existed"]
    if recorded.get("stamp") == current["stamp"]:
        return []

    lines: list[str] = []
    for label in ("resolutions", "membership_resolution", "point_tables", "polygon_tables"):
        was, now = recorded.get(label), current[label]
        if was != now:
            lines.append(f"{label}: zone built for {was}, code computes {now}")

    was_modules = recorded.get("modules") or {}
    changed = sorted(
        name
        for name in set(was_modules) | set(current["modules"])
        if was_modules.get(name) != current["modules"].get(name)
    )
    if changed:
        lines.append(f"changed since the zone was built: {', '.join(changed)}")
    if not lines:
        # The stamp covers the registry projection, which has no readable field
        # of its own because printing every dataset's geometry spec would bury
        # the cases above.
        lines.append("the dataset registry's spatial fields changed")
    return lines


# ---------------------------------------------------------------------------
# What this run has to rebuild
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    """What a run of `spatial.py` has to recompute, and what it can reuse.

    `points` maps a point table to the partitions this run must read for it:
    `None` for all of them, `[]` for none of them, and a list for the ones that
    changed. A table absent from `points` has no raw data at all.

    `reasons` is non-empty only for a full rebuild, and holds the sentences a
    run prints to say why. An empty `reasons` with `rebuild_boundaries` false
    and every `points` value empty is a zone with nothing to do.
    """

    reasons: list[str] = field(default_factory=list)
    rebuild_boundaries: bool = True
    points: dict[str, list[str] | None] = field(default_factory=dict)

    @property
    def is_full(self) -> bool:
        return bool(self.reasons)

    @property
    def is_current(self) -> bool:
        """Nothing to recompute. Note `[]` and `None` are opposite answers here."""
        return not self.rebuild_boundaries and all(
            partitions == [] for partitions in self.points.values()
        )


def _table_plan(recorded: dict, current: dict) -> list[str] | None:
    """Which partitions of one table this run must read. None means all of them.

    A partition that is recorded and now absent, or whose row or file count went
    down, means the zone was replaced rather than appended to, which only a
    local `ingest.py --full-refresh` can do (ADR-4). Nothing about the previous
    run's output survives that, so the table is read whole.
    """
    for partition, before in recorded.items():
        now = current.get(partition)
        if now is None or now["rows"] < before["rows"] or now["files"] < before["files"]:
            return None

    changed = [
        partition
        for partition, now in current.items()
        if recorded.get(partition) != now  # new, or appended to since
    ]
    if not changed:
        return []
    total = sum(now["rows"] for now in current.values())
    touched = sum(current[partition]["rows"] for partition in changed)
    if total and touched / total >= INCREMENTAL_SHARE:
        return None
    return sorted(changed)


def plan_rebuild(
    manifest: dict | None, current: dict, code: dict, forced: str | None = None
) -> Plan:
    """Decide what this run recomputes, from the manifest the last one left.

    Everything is rebuilt unless the manifest proves it need not be, which is
    the safe direction: the cost of an unnecessary rebuild is seconds, and the
    cost of a wrongly skipped one is a derived zone that disagrees with the raw
    zone and looks fine. `forced` is the caller's own reason for a full
    rebuild, as the sentence a run should print, so `--full` and a zone with a
    table missing explain themselves differently.
    """
    points = set(code["point_tables"])
    everything = Plan(points={table: None for table in current if table in points})
    if forced:
        everything.reasons = [forced]
        return everything
    if manifest is None:
        everything.reasons = ["there is no derived zone to build on"]
        return everything

    code_change = describe_code_change(manifest.get(CODE_VERSION_KEY), code)
    if code_change:
        everything.reasons = code_change
        return everything
    if manifest.get(RAW_INPUTS_KEY) is None or manifest.get(PARTITIONS_KEY) is None:
        everything.reasons = ["the zone was built before partitions were recorded"]
        return everything

    recorded = manifest[PARTITIONS_KEY]
    gone = sorted(set(recorded) - set(current))
    if gone:
        everything.reasons = [f"no raw data left for {', '.join(gone)}"]
        return everything

    plan = Plan(reasons=[], rebuild_boundaries=False, points={})
    for table, partitions in current.items():
        table_plan = _table_plan(recorded.get(table, {}), partitions)
        if table not in points:
            # No partial rebuild for boundaries, and no need for one: 733
            # polygons take about a second, and every other table in the zone
            # is computed against them, so a changed boundary set invalidates
            # the lot anyway.
            plan.rebuild_boundaries = plan.rebuild_boundaries or table_plan != []
        else:
            plan.points[table] = table_plan
    return plan
