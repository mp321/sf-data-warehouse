"""Column profiles, stated as rules rather than as one schema.

Spec section 4.3. The right statistics depend on the column, and a profile that
carries the same five fields for every column spends most of its tokens saying
nothing:

    any                     null rate
    low cardinality (<=50)  every distinct value with its share
    high cardinality string distinct count, and 5 example values
    numeric                 min, max, median
    date or timestamp       min, max, and the count in the newest complete month
    boolean                 true share
    H3 cell BIGINT          distinct count only

**H3 cells get a distinct count and nothing else, and that is the rule worth
knowing.** A BIGINT cell id tells a reader nothing at all: it cannot be read,
compared or sanity-checked by eye, so five of them are five tokens of noise in
an artifact whose whole budget problem is section 9.

**The newest complete month is derived from the data and not from the clock.**
It is the month before the month the column's maximum falls in. Reading it off
today's date instead would make the pack change at midnight on the first of the
month with no data having moved, which turns the CI drift check into a monthly
false alarm. The consumer-facing rule this supports is
`refuse.newest-month-is-partial`, which tells an answer to end a series at the
last complete month rather than at today.

One query per model does the arithmetic that can be batched, and the follow-ups
are per column and only for the columns that need one. That ordering matters on
the staging models, which are views over the Parquet zone: every query against
one is a fresh scan of the files.
"""

import datetime as dt
from decimal import Decimal

# At or under this many distinct values, the pack lists them all with their
# shares instead of describing them. 50 is the spec's number.
LOW_CARDINALITY = 50

# How many example values a high-cardinality string column gets.
EXAMPLE_VALUES = 5

# And how long one is allowed to be. Without this, a profile of
# dim_neighborhood.geojson carries five serialised MultiPolygons, which is most
# of a megabyte of pack describing a column nobody will select.
MAX_VALUE_CHARS = 120

_NUMERIC_PREFIXES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "DECIMAL",
    "NUMERIC",
    "DOUBLE",
    "FLOAT",
    "REAL",
)

_TEMPORAL_PREFIXES = ("DATE", "TIMESTAMP", "TIME")

# Columns holding a serialised shape rather than a value. They get a distinct
# count for the same reason an H3 cell does: five MultiPolygons are five
# thousand characters that tell a reader nothing they can act on.
_BLOB_COLUMNS = ("geojson", "the_geom", "polygon")


def column_shape(name: str, data_type: str) -> str:
    """Which of the profile rules applies to this column.

    The order is the precedence, and two parts of it are decisions rather than
    readings of the spec. An H3 cell is checked first because it is a BIGINT and
    would otherwise be profiled as a number, and min, max and median of a cell id
    are three meaningless numbers. A temporal column is checked before the
    low-cardinality rule because its range is the useful thing even when it holds
    six distinct dates.
    """
    upper = (data_type or "").upper()
    lowered = name.lower()
    if lowered == "h3_cell" or lowered.startswith("h3_r"):
        shape = "h3_cell"
    elif lowered in _BLOB_COLUMNS:
        shape = "blob"
    elif upper.startswith(("BOOLEAN", "BOOL")):
        shape = "boolean"
    elif upper.startswith(_TEMPORAL_PREFIXES):
        shape = "temporal"
    elif upper.startswith(_NUMERIC_PREFIXES):
        shape = "numeric"
    elif upper.startswith(("VARCHAR", "TEXT", "STRING")):
        shape = "string"
    else:
        shape = "other"
    return shape


def _scalar(value):
    """JSON-safe, and stable across runs so a pack diffs cleanly."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat()
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[: MAX_VALUE_CHARS - 3] + "..."
    return value


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _share(part: int, whole: int) -> float:
    return round(part / whole, 6) if whole else 0.0


def profile_model(target, model: str, columns: list[tuple[str, str]], row_count: int) -> dict:
    """Profile every column of one model. Returns {column_name: profile}."""
    shapes = {name: column_shape(name, dtype) for name, dtype in columns}
    profiles: dict[str, dict] = {}

    if row_count == 0:
        # An empty model is a real state (a fixture build before ingestion, or a
        # mart whose upstream is empty), and profiling it would be a page of
        # nulls. Say it once instead.
        for name, _dtype in columns:
            profiles[name] = {"null_rate": None, "note": "the model holds no rows"}
        return profiles

    aggregates, plan = _aggregate_query(model, columns, shapes, target)
    row = target.execute(aggregates)[0]
    values = dict(zip(plan, row, strict=True))

    for name, _dtype in columns:
        shape = shapes[name]
        non_null = values[f"n_{name}"]
        distinct = values[f"d_{name}"]
        profile: dict = {"null_rate": round(1 - (non_null / row_count), 6)}

        if shape in ("h3_cell", "blob"):
            profile["distinct_count"] = distinct
        elif shape == "boolean":
            profile["true_share"] = _share(values[f"t_{name}"], non_null)
            profile["distinct_count"] = distinct
        elif shape == "temporal":
            profile["min"] = _scalar(values[f"lo_{name}"])
            profile["max"] = _scalar(values[f"hi_{name}"])
            profile.update(_newest_complete_month(target, model, name, values[f"hi_{name}"]))
        # `distinct < non_null` and not just the threshold: a 41-row dimension
        # has 41 distinct neighborhood names and 41 distinct areas, and listing
        # every one of them with a share of 2.4 percent describes a key rather
        # than a category. A category is a column whose values repeat.
        elif distinct <= LOW_CARDINALITY and distinct < non_null:
            profile["distinct_count"] = distinct
            profile["values"] = _distinct_values(target, model, name, row_count)
        elif shape == "numeric":
            profile["distinct_count"] = distinct
            profile["min"] = _scalar(values[f"lo_{name}"])
            profile["max"] = _scalar(values[f"hi_{name}"])
            profile["median"] = _scalar(values[f"md_{name}"])
        elif shape == "string":
            profile["distinct_count"] = distinct
            profile["examples"] = _example_values(target, model, name)
        else:
            profile["distinct_count"] = distinct

        profiles[name] = profile
    return profiles


def _aggregate_query(model, columns, shapes, target) -> tuple[str, list[str]]:
    """One SELECT that answers everything that can be answered in one pass."""
    selects: list[str] = []
    plan: list[str] = []
    for name, _dtype in columns:
        quoted = _quoted(name)
        shape = shapes[name]
        selects.append(f"count({quoted})")
        plan.append(f"n_{name}")
        selects.append(f"count(distinct {quoted})")
        plan.append(f"d_{name}")
        if shape in ("numeric", "temporal"):
            selects.append(f"min({quoted})")
            plan.append(f"lo_{name}")
            selects.append(f"max({quoted})")
            plan.append(f"hi_{name}")
        if shape == "numeric":
            selects.append(f"median({quoted})")
            plan.append(f"md_{name}")
        if shape == "boolean":
            selects.append(f"count(*) filter (where {quoted})")
            plan.append(f"t_{name}")
    return f"select {', '.join(selects)} from {target.relation(model)}", plan


def _distinct_values(target, model: str, name: str, row_count: int) -> list[dict]:
    quoted = _quoted(name)
    rows = target.execute(
        f"select {quoted}, count(*) from {target.relation(model)} "
        f"group by 1 order by 2 desc, cast({quoted} as varchar) asc"
    )
    return [
        {"value": _scalar(value), "share": _share(count, row_count)}
        for value, count in rows
        if value is not None
    ]


def _example_values(target, model: str, name: str) -> list:
    """The five commonest values, not five arbitrary ones.

    Ordered by frequency and then by the value itself, so the same warehouse
    produces the same five in the same order and a regenerated pack diffs on data
    that moved rather than on which row the scan happened to reach first.
    """
    quoted = _quoted(name)
    rows = target.execute(
        f"select {quoted} from {target.relation(model)} where {quoted} is not null "
        f"group by 1 order by count(*) desc, cast({quoted} as varchar) asc limit {EXAMPLE_VALUES}"
    )
    return [_scalar(value) for (value,) in rows]


def _newest_complete_month(target, model: str, name: str, maximum) -> dict:
    if maximum is None:
        return {"newest_complete_month": None, "newest_complete_month_count": None}
    month = dt.date(maximum.year, maximum.month, 1)
    previous = (
        dt.date(month.year - 1, 12, 1) if month.month == 1 else month.replace(month=month.month - 1)
    )
    quoted = _quoted(name)
    count = target.execute(
        f"select count(*) from {target.relation(model)} "
        f"where date_trunc('month', {quoted}) = date_trunc('month', cast(? as timestamp))",
        [previous.isoformat()],
    )[0][0]
    return {
        "newest_complete_month": previous.isoformat(),
        "newest_complete_month_count": count,
    }
