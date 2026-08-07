"""The compact markdown, and the budget that shapes it.

Spec section 9. The order is a design decision and not a formatting one:

    1. identity and target        6. models and columns
    2. how to read this pack      7. join map
    3. refusals                   8. examples
    4. disclosures                9. freshness
    5. traps                     10. integrity

**Refusals come before the schema** because a model that has read the schema has
already begun composing SQL, and a constraint that arrives after a draft exists
has to overturn something rather than shape it.

**The traps block is here as of 2026-08-07, and was not before.** The first
reading of section 9 took its nine-item list literally, left traps in the JSON
alone, and argued that prose which does not make a question unanswerable is what
a prompt-sized rendering can do without. ADR-13 amended the spec against that.
A trap is an unconditional disclosure, so rendering the conditional warning and
withholding the unconditional one was the wrong way round; and what a trap
changes is the query that gets written, which it cannot do from a file nobody
puts in a prompt. Three of the four prevent a query that returns a plausible
number: pooled `category` vocabularies, an H3 cell compared as a string, and an
inner join that discards the null neighborhoods.

**Under budget pressure the generator drops in a fixed order** and reports what
it dropped:

    1. examples beyond the one required per class 3 refusal
    2. the "no description in the yml" markers on undocumented columns
    3. profile statistics
    4. low-signal columns

**Refusals, disclosures, traps and grain sentences are never dropped.** If the
pack does not fit with all four present, generation fails rather than emitting a
truncated pack, because a pack missing a refusal is worse than no pack: it reads
complete. Traps are in that list and not in the ladder because the ladder sheds
detail from the models block: 585 estimated tokens of traps against about 16,000
of models, and worth more than the profile statistics stage 3 drops.

Stage 2 needs one sentence of interpretation, since the spec's phrase is "column
descriptions for columns with no yml description". A column with no description
renders an explicit marker saying so, which is a real signal under the
closed-world rule - it tells a reader the column is undocumented rather than
leaving them to guess - and it is the first thing worth losing when space runs
out. That marker is what stage 2 drops.
"""

# A rough four characters per token. Deliberately a heuristic and reported as
# one: the pack is model agnostic (PLAN-6), so there is no tokeniser it could be
# measured against without picking a model to be right for.
CHARS_PER_TOKEN = 4

DROP_STAGES = (
    "surplus examples",
    "undocumented-column markers",
    "profile statistics",
    "low-signal columns",
)


class BudgetExceeded(Exception):
    """The pack does not fit with every mandatory block present."""


def estimate_tokens(text: str) -> int:
    return round(len(text) / CHARS_PER_TOKEN)


def render(pack: dict, budget: int) -> tuple[str, dict]:
    """Render the markdown, dropping in order until it fits or the drops run out."""
    for stage in range(len(DROP_STAGES) + 1):
        text = _render_at(pack, stage)
        tokens = estimate_tokens(text)
        if tokens <= budget:
            return text, {
                "estimated_tokens": tokens,
                "budget": budget,
                "dropped": list(DROP_STAGES[:stage]),
            }
    raise BudgetExceeded(
        f"The markdown is about {estimate_tokens(_render_at(pack, len(DROP_STAGES)))} tokens "
        f"with everything droppable dropped, against a budget of {budget}. Refusals, "
        "disclosures and grain sentences are never dropped (section 9), so this fails rather "
        "than emitting a pack that reads complete and is not. Raise --token-budget, or cut "
        "models from the target."
    )


def _render_at(pack: dict, stage: int) -> str:
    lines: list[str] = []
    _identity(lines, pack)
    _how_to_read(lines, pack)
    _refusals(lines, pack)
    _disclosures(lines, pack)
    _traps(lines, pack)
    _models(lines, pack, stage)
    _joins(lines, pack)
    _examples(lines, pack, stage)
    _freshness(lines, pack)
    _integrity(lines, pack)
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 1 and 2. Identity, and how to read this
# ---------------------------------------------------------------------------


def _identity(lines: list[str], pack: dict) -> None:
    identity = pack["identity"]
    lines.append(f"# {identity['name']} context pack, target {pack['target']}")
    lines.append("")
    lines.append(f"{identity['description']}")
    lines.append(
        f"Target `{pack['target']}`, {len(pack['models'])} models, generated "
        f"{pack['generated_at']}, prose revision `{pack['prose_revision']}`, "
        f"spec {pack['spec_version']}, pack {pack['pack_version']}."
    )
    lines.append(
        f"Publisher {identity['publisher']}, jurisdiction {identity['jurisdiction']}. "
        f"{identity['licence']}"
    )
    lines.append("")


def _how_to_read(lines: list[str], pack: dict) -> None:
    lines.append("## How to read this pack")
    lines.append("")
    lines.append(pack["identity"]["how_to_read"])
    lines.append("")
    lines.append("> " + pack["refusals"]["closed_world_rule"].replace("\n", " ").strip())
    lines.append("")


# ---------------------------------------------------------------------------
# 3, 4 and 5. Refusals, then disclosures, then traps
# ---------------------------------------------------------------------------


def _refusals(lines: list[str], pack: dict) -> None:
    block = pack["refusals"]
    lines.append("## Refusals")
    lines.append("")
    lines.append(block["no_ground_truth"].strip())
    lines.append("")
    lines.append(block["census_exception"].strip())
    lines.append("")
    for entry in block["entries"]:
        lines.append(f"### {entry['id']} ({entry['class']})")
        lines.append("")
        for shape in entry["question_shapes"]:
            lines.append(f'- "{shape}"')
        lines.append("")
        lines.append(f"**Rule.** {_flat(entry['rule'])}")
        lines.append("")
        lines.append(f"Why: {_flat(entry['why'])}")
        instead = entry["instead"]
        example = f" See example `{instead['example']}`." if instead.get("example") else ""
        lines.append(f"Instead: {_flat(instead['answer'])}{example}")
        lines.append(f"Evidence: {_evidence(entry)}")
        lines.append("")


def _disclosures(lines: list[str], pack: dict) -> None:
    lines.append("## Mandatory disclosures")
    lines.append("")
    lines.append(
        "Answerable, with a bounded error the answer has to carry. When the condition "
        "holds, the answer states the sentence."
    )
    lines.append("")
    for entry in pack["disclosures"]:
        lines.append(f"### {entry['id']}")
        lines.append("")
        lines.append(f"When: {_flat(entry['when'])}")
        lines.append(f"**State.** {_flat(entry['state'])}")
        lines.append(f"Why: {_flat(entry['why'])}")
        if entry.get("numbers"):
            lines.append("")
            lines.extend(_table(entry["numbers"]))
            lines.append("")
        lines.append(f"Evidence: {_evidence(entry)}")
        lines.append("")


def _traps(lines: list[str], pack: dict) -> None:
    """Section 4.6, rendered because a trap is a disclosure with no condition."""
    if not pack.get("traps"):
        return
    lines.append("## Traps")
    lines.append("")
    lines.append(
        "True of the data and not refusals: the question is answerable and the obvious "
        "query answers a different one. These always apply, so they carry no condition."
    )
    lines.append("")
    for entry in pack["traps"]:
        lines.append(f"### {entry['id']}")
        lines.append("")
        lines.append(f"**State.** {_flat(entry['state'])}")
        lines.append(f"Why: {_flat(entry['why'])}")
        lines.append(f"Evidence: {_evidence(entry)}")
        lines.append("")


# ---------------------------------------------------------------------------
# 6. Models and columns
# ---------------------------------------------------------------------------


def _models(lines: list[str], pack: dict, stage: int) -> None:
    lines.append("## Models")
    lines.append("")
    for model in pack["models"]:
        lines.append(
            f"### {model['name']} ({model['layer']}, {model['materialisation']}, "
            f"{model['row_count']:,} rows)"
        )
        lines.append("")
        lines.append(f"Grain: {model['grain']}")
        lines.append("")
        lines.append("| column | type | description |")
        lines.append("|---|---|---|")
        for column in model["columns"]:
            if stage >= 4 and column.get("low_signal"):
                continue
            description = _column_description(column, stage)
            lines.append(f"| {column['name']} | {column['type']} | {description} |")
        lines.append("")


def _column_description(column: dict, stage: int) -> str:
    parts = []
    if column.get("description"):
        parts.append(_flat(column["description"]))
    elif stage < 2:
        parts.append("(no description in the yml)")
    if stage < 3:
        summary = _profile_summary(column.get("profile") or {})
        if summary:
            parts.append(summary)
    return " ".join(parts) if parts else " "


def _profile_summary(profile: dict) -> str:
    if not profile:
        return ""
    bits = []
    null_rate = profile.get("null_rate")
    if null_rate:
        bits.append(f"{_percent(null_rate)} null")
    if profile.get("note"):
        bits.append(profile["note"])
    if "true_share" in profile:
        bits.append(f"{_percent(profile['true_share'])} true")
    if "values" in profile:
        shown = ", ".join(
            f"{_value(item['value'])} {_percent(item['share'])}" for item in profile["values"][:8]
        )
        more = "" if len(profile["values"]) <= 8 else f", and {len(profile['values']) - 8} more"
        bits.append(f"values: {shown}{more}")
    elif "examples" in profile:
        bits.append(
            f"{profile['distinct_count']:,} distinct, e.g. "
            + ", ".join(_value(item) for item in profile["examples"])
        )
    elif "median" in profile:
        bits.append(
            f"min {_value(profile['min'])}, median {_value(profile['median'])}, "
            f"max {_value(profile['max'])}"
        )
    elif "newest_complete_month" in profile:
        bits.append(f"{_value(profile['min'])} to {_value(profile['max'])}")
        if profile.get("newest_complete_month"):
            bits.append(
                f"newest complete month {profile['newest_complete_month']}: "
                f"{profile['newest_complete_month_count']:,} rows"
            )
    elif "distinct_count" in profile:
        bits.append(f"{profile['distinct_count']:,} distinct")
    return "; ".join(bits)


# ---------------------------------------------------------------------------
# 7 to 10. Joins, examples, freshness, integrity
# ---------------------------------------------------------------------------


def _joins(lines: list[str], pack: dict) -> None:
    lines.append("## Join map")
    lines.append("")
    lines.append("| from | to | cardinality | safe |")
    lines.append("|---|---|---|---|")
    for join in pack["joins"]:
        safe = "yes" if join["safe"] else "NO"
        lines.append(f"| {join['from']} | {join['to']} | {join['cardinality']} | {safe} |")
    lines.append("")
    for join in pack["joins"]:
        if not join.get("why"):
            continue
        lines.append(f"- `{join['from']}` to `{join['to']}`: {_flat(join['why'])}")
        # The on clause only when it is more than the equality the two column
        # names already state. On the bridge joins it carries the flag and the
        # resolution predicate, which is the whole of what makes them safe.
        clause = _flat(join["on"])
        if clause != f"{join['from']} = {join['to']}":
            lines.append(f"  - on: `{clause}`")
    lines.append("")


def _examples(lines: list[str], pack: dict, stage: int) -> None:
    lines.append("## Verified examples")
    lines.append("")
    lines.append(
        "Every query below was executed against this target at generation time. "
        "The row count is what it returned."
    )
    lines.append("")
    for example in pack["examples"]:
        if stage >= 1 and not example.get("required"):
            continue
        lines.append(f"### {example['id']}")
        lines.append("")
        lines.append(_flat(example["question"]))
        lines.append("")
        lines.append("```sql")
        lines.append(example["sql"].strip())
        lines.append("```")
        lines.append("")
        demonstrates = ", ".join(example.get("demonstrates") or []) or "none"
        lines.append(
            f"Demonstrates: {demonstrates}. Verified against {example['verified']['target']} "
            f"at {example['verified']['at']}, {example['verified']['rows']} rows."
        )
        lines.append("")


def _freshness(lines: list[str], pack: dict) -> None:
    lines.append("## Freshness")
    lines.append("")
    lines.append(f"{pack['freshness']['basis']}")
    lines.append("")
    lines.extend(_table(pack["freshness"]["sources"]))
    lines.append("")


def _integrity(lines: list[str], pack: dict) -> None:
    integrity = pack["integrity"]
    lines.append("## Integrity")
    lines.append("")
    lines.append(_flat(integrity["self_refusal"]))
    lines.append("")
    dbt = integrity["dbt"]
    lines.append(
        f"Built from dbt invocation `{dbt['invocation_id']}` ({dbt['dbt_version']}, "
        f"adapter {dbt['adapter_type']}), manifest generated {dbt['manifest_generated_at']}."
    )
    lines.append("")
    if integrity.get("schema_hash_absent_because"):
        lines.append(f"No schema hash: {_flat(integrity['schema_hash_absent_because'])}")
        lines.append("")
    else:
        lines.append("| model | schema hash | rows |")
        lines.append("|---|---|---|")
        for name, entry in integrity["models"].items():
            lines.append(f"| {name} | {entry['schema_hash']} | {entry['row_count']:,} |")
        lines.append("")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _percent(share: float) -> str:
    """A share, without rounding a real value to a flat zero.

    `0.0% null` on a column with 700 nulls in 360,000 rows reads as "no nulls",
    which is the one thing the number is there to deny.
    """
    if 0 < share < 0.001:
        return "<0.1%"
    if 0 < 1 - share < 0.001:
        return ">99.9%"
    return f"{share:.1%}"


def _value(value) -> str:
    # bool before int, or True renders as 1: a freshness table reading
    # `is_stale | 1` is one keystroke from being read as a count.
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:,.4g}"
    if isinstance(value, int):
        return f"{value:,}"
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _evidence(entry: dict) -> str:
    rendered = []
    for citation in entry["evidence"]:
        kind = citation["kind"]
        if kind == "measurement":
            when = (
                "not measured in this project"
                if citation.get("measured") is False
                else f"measured {citation['measured']}"
            )
            rendered.append(f"{_flat(citation['ref'])} ({when})")
        elif kind == "adr":
            rendered.append(str(citation["ref"]).upper())
        elif kind == "registry":
            rendered.append(f"registry {citation['ref']} = {citation.get('equals', '')}".strip())
        else:
            rendered.append(f"{kind} {citation['ref']}")
    return "; ".join(rendered)


def _table(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    headers = list(rows[0])
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(_value(row.get(header)) for header in headers) + " |")
    return lines
